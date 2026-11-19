from copy import deepcopy
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, noload, selectinload

from app.core.clock import factory_today
from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles, require_admin
from app.core.list_query import apply_text_search, apply_year_month, paginate
from app.features.audit.service import record_audit, stamp_creator
from app.features.plm import models, schemas
from app.features.store.models import FabricReceive
from app.features.admin_config.models import Material
from app.features.admin_config.service import (
    apply_live_material,
    apply_material_to_qc,
    ensure_default_shade_points,
    material_snapshot,
)

router = APIRouter(prefix="/plms", tags=["PLM"])


def _plm_query(db: Session, *, lite: bool = False):
    colors = noload(models.Plm.colors) if lite else selectinload(models.Plm.colors).selectinload(models.PlmColor.rolls)
    return db.query(models.Plm).options(
        joinedload(models.Plm.fabric_receive),
        joinedload(models.Plm.buyer),
        joinedload(models.Plm.supplier),
        joinedload(models.Plm.material),
        colors,
    )


def _validate_allocation(db: Session, receive: FabricReceive, quantity: Optional[float], exclude_plm_id=None):
    if quantity is None or receive.received_quantity is None:
        return
    q = db.query(func.coalesce(func.sum(models.Plm.allocated_quantity), 0)).filter(models.Plm.fabric_receive_id == receive.id)
    if exclude_plm_id:
        q = q.filter(models.Plm.id != exclude_plm_id)
    used = float(q.scalar() or 0)
    if used + float(quantity) > float(receive.received_quantity) + 1e-9:
        raise HTTPException(
            status_code=422,
            detail=f"PLM allocation exceeds consignment received quantity. Available: {float(receive.received_quantity) - used:g} {receive.quantity_unit}",
        )


def _validate_planning_payload(payload: schemas.PlmCreate, receive: FabricReceive) -> None:
    if payload.allocated_quantity is None or float(payload.allocated_quantity) <= 0:
        raise HTTPException(status_code=422, detail="PLM allocated_quantity must be greater than zero")

    if not payload.colors:
        return

    color_total = 0.0
    for color in payload.colors:
        if color.planned_quantity is None or float(color.planned_quantity) <= 0:
            raise HTTPException(status_code=422, detail=f"Color {color.colour_code} needs a planned quantity")
        color_total += float(color.planned_quantity)
        if not color.rolls:
            raise HTTPException(status_code=422, detail=f"Color {color.colour_code} needs at least one planned roll")
        roll_total = 0.0
        for roll in color.rolls:
            missing = []
            if roll.marked_width is None or float(roll.marked_width) <= 0:
                missing.append("marked width")
            if roll.marked_length is None or float(roll.marked_length) <= 0:
                missing.append("marked length")
            # Roll quantity is entered in the app (Color Continuity), not planned here.
            if roll.planned_quantity is not None and float(roll.planned_quantity) <= 0:
                missing.append("planned quantity must be greater than 0")
            if roll.supplier_total_point is None:
                missing.append("supplier total point")
            elif float(roll.supplier_total_point) < 0:
                missing.append("supplier total point must be >= 0")
            if missing:
                raise HTTPException(
                    status_code=422,
                    detail=f"Roll {roll.roll_no} under {color.colour_code} is missing/invalid: {', '.join(missing)}",
                )
            roll_total += float(roll.planned_quantity or 0)
        if roll_total > float(color.planned_quantity) + 1e-9:
            raise HTTPException(
                status_code=422,
                detail=f"Roll planned quantities for color {color.colour_code} exceed that color's planned quantity",
            )
    if color_total > float(payload.allocated_quantity) + 1e-9:
        raise HTTPException(status_code=422, detail="Color planned quantities exceed the PLM allocated quantity")


def _plm_roll_numbers(db: Session, plm_id, exclude_color_id=None) -> dict:
    """Roll number (lower-case) -> colour code for every planned roll of the PLM. Inspection
    rows and inspection_reports match rolls by number across the whole PLM, so it must be unique there."""
    q = (db.query(models.PlmRoll.roll_no, models.PlmColor.colour_code)
         .join(models.PlmColor, models.PlmColor.id == models.PlmRoll.plm_color_id)
         .filter(models.PlmColor.plm_id == plm_id))
    if exclude_color_id is not None:
        q = q.filter(models.PlmColor.id != exclude_color_id)
    return {roll_no.strip().lower(): code for roll_no, code in q.all()}


def _reject_taken_roll(roll_no: str, taken: dict) -> None:
    if roll_no.lower() in taken:
        raise HTTPException(status_code=422, detail=f"Roll {roll_no} already exists in color {taken[roll_no.lower()]} of this PLM")


def _require_roll_fields(roll: schemas.PlmRollIn) -> None:
    missing = []
    if not (roll.roll_no or "").strip():
        missing.append("roll number")
    if roll.marked_width is None or float(roll.marked_width) <= 0:
        missing.append("marked width")
    if roll.marked_length is None or float(roll.marked_length) <= 0:
        missing.append("marked length")
    if roll.planned_quantity is not None and float(roll.planned_quantity) <= 0:
        missing.append("roll quantity must be greater than 0")
    if roll.supplier_total_point is None:
        missing.append("supplier total point")
    elif float(roll.supplier_total_point) < 0:
        missing.append("supplier total point must be >= 0")
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Roll {roll.roll_no or ''} is missing/invalid: {', '.join(missing)}",
        )


def _sync_color_quantity(db: Session, color: models.PlmColor) -> None:
    total = float(
        db.query(func.coalesce(func.sum(models.PlmRoll.planned_quantity), 0))
        .filter(models.PlmRoll.plm_color_id == color.id)
        .scalar() or 0
    )
    if color.planned_quantity is None:
        if total > 0:
            color.planned_quantity = total
        return
    if total > float(color.planned_quantity) + 1e-9:
        color.planned_quantity = total


def _validate_color_roll_totals(db: Session, color: models.PlmColor) -> None:
    _sync_color_quantity(db, color)


@router.post("", response_model=schemas.PlmOut)
def create_plm(payload: schemas.PlmCreate, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    if db.query(models.Plm).filter(models.Plm.plm_code == payload.plm_code).first():
        raise HTTPException(status_code=400, detail="A PLM with this code already exists")

    receive = db.query(FabricReceive).get(payload.fabric_receive_id)
    if not receive:
        raise HTTPException(status_code=404, detail="Fabric receive record not found")
    material = db.query(Material).filter(Material.id == payload.material_id, Material.is_active == True).first()  # noqa: E712
    if not material:
        raise HTTPException(status_code=422, detail="Select an active Material from Dashboard")
    _validate_planning_payload(payload, receive)
    _validate_allocation(db, receive, payload.allocated_quantity)

    snapshot = material_snapshot(material)
    qc_config = apply_material_to_qc(deepcopy(receive.qc_config or {}), material)
    shade = dict(qc_config.get("shade_check") or {})
    if not shade.get("points"):
        shade["points"] = ensure_default_shade_points(db)
        qc_config["shade_check"] = shade

    plm = models.Plm(
        plm_code=payload.plm_code,
        plm_date=payload.plm_date,
        buyer_id=receive.buyer_id,
        supplier_id=receive.supplier_id,
        fabric_receive_id=receive.id,
        material_id=material.id,
        plm_style=payload.plm_style,
        fabric_type=material.fabric_name,
        fabric_composition=material.composition_label or receive.fabric_composition,
        consignment=receive.consignment,
        measurement_unit=receive.measurement_unit,
        allocated_quantity=payload.allocated_quantity,
        received_quantity=receive.received_quantity,
        quantity_unit=receive.quantity_unit,
        received_at=receive.received_at,
        qc_config=qc_config,
        qc_setup_version=receive.qc_setup_version or 1,
        material_snapshot=snapshot,
    )
    stamp_creator(plm, user)
    db.add(plm)
    db.flush()
    record_audit(db, user, action="plm_created", entity_type="plm", entity_id=plm.id, plm_id=plm.id,
                 summary=f"PLM {plm.plm_code} created ({payload.allocated_quantity or 0:g} {plm.quantity_unit})")

    seen_colors = set()
    seen_rolls = set()
    for color_in in payload.colors:
        key = color_in.colour_code.strip().lower()
        if key in seen_colors:
            raise HTTPException(status_code=422, detail=f"Duplicate color code: {color_in.colour_code}")
        seen_colors.add(key)
        color = models.PlmColor(
            plm_id=plm.id,
            colour_code=color_in.colour_code.strip(),
            planned_quantity=color_in.planned_quantity,
        )
        db.add(color)
        db.flush()
        for roll_in in color_in.rolls:
            roll_no = roll_in.roll_no.strip()
            if not roll_no or roll_no.lower() in seen_rolls:
                raise HTTPException(status_code=422, detail=f"Duplicate/empty roll number in {color.colour_code}: {roll_no}")
            seen_rolls.add(roll_no.lower())
            db.add(models.PlmRoll(plm_color_id=color.id, **{**roll_in.model_dump(), "roll_no": roll_no}))

    db.commit()
    return _plm_query(db).get(plm.id)


@router.put("/{plm_id}", response_model=schemas.PlmOut)
def update_plm(plm_id: UUID, payload: schemas.PlmUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    plm = _plm_query(db).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    if payload.allocated_quantity is not None and plm.fabric_receive:
        _validate_allocation(db, plm.fabric_receive, payload.allocated_quantity, exclude_plm_id=plm.id)
    changes = payload.model_dump(exclude_none=True)
    if "plm_code" in changes:
        code = changes["plm_code"].strip()
        if not code:
            raise HTTPException(status_code=422, detail="PLM code cannot be empty")
        if db.query(models.Plm).filter(models.Plm.plm_code == code, models.Plm.id != plm.id).first():
            raise HTTPException(status_code=400, detail="A PLM with this code already exists")
        changes["plm_code"] = code
    material = None
    if "material_id" in changes:
        material = db.query(Material).filter(Material.id == changes["material_id"], Material.is_active == True).first()  # noqa: E712
        if not material:
            raise HTTPException(status_code=422, detail="Select an active Material from Dashboard")
    for k, v in changes.items():
        setattr(plm, k, v)
    if material is not None:
        plm.material = material
        apply_live_material(plm)
    record_audit(db, user, action="plm_updated", entity_type="plm", entity_id=plm.id, plm_id=plm.id,
                 summary=f"PLM {plm.plm_code} updated: {', '.join(changes) or 'no changes'}",
                 data={k: str(v) for k, v in changes.items()})
    db.commit()
    return _plm_query(db).get(plm.id)


@router.post("/{plm_id}/colors", response_model=schemas.PlmColorOut)
def add_color(plm_id: UUID, payload: schemas.PlmColorIn, db: Session = Depends(get_db), _=Depends(require_admin())):
    if not db.query(models.Plm).get(plm_id):
        raise HTTPException(status_code=404, detail="PLM not found")
    existing = db.query(models.PlmColor).filter(
        models.PlmColor.plm_id == plm_id,
        models.PlmColor.colour_code == payload.colour_code.strip(),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Color already exists under this PLM")
    if payload.planned_quantity is not None and payload.planned_quantity <= 0:
        raise HTTPException(status_code=422, detail="Color planned_quantity must be greater than zero")
    color = models.PlmColor(
        plm_id=plm_id,
        colour_code=payload.colour_code.strip(),
        planned_quantity=payload.planned_quantity,
    )
    db.add(color)
    db.flush()
    taken = _plm_roll_numbers(db, plm_id)
    for r in payload.rolls:
        _require_roll_fields(r)
        roll_no = r.roll_no.strip()
        if not roll_no:
            raise HTTPException(status_code=422, detail="Roll number is required")
        _reject_taken_roll(roll_no, taken)
        taken[roll_no.lower()] = color.colour_code
        db.add(models.PlmRoll(plm_color_id=color.id, **{**r.model_dump(), "roll_no": roll_no}))
    db.flush()
    _sync_color_quantity(db, color)
    db.commit()
    db.refresh(color)
    return color


@router.post("/colors/{color_id}/rolls", response_model=schemas.PlmRollOut)
def add_roll(color_id: UUID, payload: schemas.PlmRollIn, db: Session = Depends(get_db), _=Depends(require_admin())):
    color = db.query(models.PlmColor).get(color_id)
    if not color:
        raise HTTPException(status_code=404, detail="PLM color not found")
    _require_roll_fields(payload)
    roll_no = payload.roll_no.strip()
    existing = db.query(models.PlmRoll).filter(
        models.PlmRoll.plm_color_id == color_id,
        models.PlmRoll.roll_no == roll_no,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Roll {roll_no} already exists under this color")
    _reject_taken_roll(roll_no, _plm_roll_numbers(db, color.plm_id, exclude_color_id=color.id))
    row = models.PlmRoll(plm_color_id=color_id, **payload.model_dump())
    row.roll_no = roll_no
    db.add(row)
    db.flush()
    _sync_color_quantity(db, color)
    db.commit()
    db.refresh(row)
    return row


@router.post("/colors/{color_id}/rolls/bulk", response_model=List[schemas.PlmRollOut])
def bulk_add_rolls(color_id: UUID, payload: schemas.PlmRollBulkCreate, db: Session = Depends(get_db), _=Depends(require_admin())):
    color = db.query(models.PlmColor).get(color_id)
    if not color:
        raise HTTPException(status_code=404, detail="PLM color not found")

    numbers = [str(x).strip() for x in payload.roll_numbers if str(x).strip()]
    if not numbers and payload.start_roll is not None and payload.end_roll is not None:
        numbers = [f"{payload.prefix}{n}" for n in range(payload.start_roll, payload.end_roll + 1)]

    existing = {
        r.roll_no.lower(): r for r in db.query(models.PlmRoll).filter(models.PlmRoll.plm_color_id == color_id).all()
    }
    other_colors = _plm_roll_numbers(db, color.plm_id, exclude_color_id=color.id)
    created = []
    for roll_no in numbers:
        _reject_taken_roll(roll_no, other_colors)
        if roll_no.lower() in existing:
            # Idempotent bulk operation: update common planned fields instead of duplicating.
            row = existing[roll_no.lower()]
            if payload.marked_width is not None:
                row.marked_width = payload.marked_width
            if payload.marked_length is not None:
                row.marked_length = payload.marked_length
            if payload.planned_quantity is not None:
                row.planned_quantity = payload.planned_quantity
            if payload.supplier_total_point is not None:
                if float(payload.supplier_total_point) < 0:
                    raise HTTPException(status_code=422, detail="supplier_total_point must be >= 0")
                row.supplier_total_point = payload.supplier_total_point
        else:
            if (payload.marked_width is None or payload.marked_width <= 0 or payload.marked_length is None or payload.marked_length <= 0 or
                    (payload.planned_quantity is not None and payload.planned_quantity <= 0) or payload.supplier_total_point is None or payload.supplier_total_point < 0):
                raise HTTPException(
                    status_code=422,
                    detail=f"New roll {roll_no} requires marked_width, marked_length and supplier_total_point (>= 0) from Dashboard",
                )
            row = models.PlmRoll(
                plm_color_id=color_id,
                roll_no=roll_no,
                marked_width=payload.marked_width,
                marked_length=payload.marked_length,
                planned_quantity=payload.planned_quantity,
                supplier_total_point=payload.supplier_total_point,
            )
            db.add(row)
            existing[roll_no.lower()] = row
        created.append(row)
    db.flush()
    _validate_color_roll_totals(db, color)
    db.commit()
    for r in created:
        db.refresh(r)
    return created


_IMPORT_FIELDS = ("marked_width", "marked_length", "planned_quantity", "supplier_total_point")


@router.post("/{plm_id}/rolls/import", response_model=schemas.RollImportResult)
def import_rolls(plm_id: UUID, payload: schemas.RollImportIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    """Packing-list import. dry_run returns the preview only; a real run writes nothing unless every line is valid."""
    plm = db.query(models.Plm).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")

    colors = {c.colour_code.strip().lower(): c for c in db.query(models.PlmColor).filter(models.PlmColor.plm_id == plm_id).all()}
    existing = {}
    for roll, code in (db.query(models.PlmRoll, models.PlmColor.colour_code)
                       .join(models.PlmColor, models.PlmColor.id == models.PlmRoll.plm_color_id)
                       .filter(models.PlmColor.plm_id == plm_id).all()):
        existing[roll.roll_no.strip().lower()] = (roll, code)

    lines: List[schemas.RollImportLine] = []
    plan = []
    new_colors: dict = {}
    seen: dict = {}
    counts = {"new": 0, "update": 0, "unchanged": 0, "error": 0}

    for row in payload.rows:
        color = row.color.strip()
        roll_no = row.roll_no.strip()
        problems = []
        if not color:
            problems.append("color is required")
        if not roll_no:
            problems.append("roll number is required")
        for key, label in (("marked_width", "marked width"), ("marked_length", "marked length"), ("planned_quantity", "quantity")):
            value = getattr(row, key)
            if (value is None and key != "planned_quantity") or (value is not None and value <= 0):
                problems.append(f"{label} must be greater than 0")
        if row.supplier_total_point is None or row.supplier_total_point < 0:
            problems.append("supplier points must be 0 or more")
        if roll_no:
            first = seen.setdefault(roll_no.lower(), row.line)
            if first != row.line:
                problems.append(f"roll {roll_no} is repeated (line {first})")

        action, message = "new", None
        if not problems and roll_no.lower() in existing:
            roll, code = existing[roll_no.lower()]
            if code.strip().lower() != color.lower():
                problems.append(f"roll {roll_no} already belongs to color {code}")
            else:
                changes = {k: getattr(row, k) for k in _IMPORT_FIELDS
                           if getattr(row, k) is not None
                           and (getattr(roll, k) is None or abs(float(getattr(roll, k)) - float(getattr(row, k))) > 1e-9)}
                action = "update" if changes else "unchanged"
                message = ", ".join(k.replace("_", " ") for k in changes) or None
                plan.append((action, roll, changes))
        elif not problems:
            if color.lower() not in colors:
                new_colors.setdefault(color.lower(), color)
            plan.append(("new", color.lower(), row))

        if problems:
            action, message = "error", "; ".join(problems)
        counts[action] += 1
        lines.append(schemas.RollImportLine(line=row.line, color=color, roll_no=roll_no, action=action, message=message))

    result = dict(new_colors=list(new_colors.values()), new_rolls=counts["new"], updated_rolls=counts["update"],
                  unchanged_rolls=counts["unchanged"], errors=counts["error"], lines=lines)
    if payload.dry_run or counts["error"]:
        return schemas.RollImportResult(applied=False, **result)

    for key, name in new_colors.items():
        colors[key] = models.PlmColor(plm_id=plm_id, colour_code=name)
        db.add(colors[key])
    db.flush()
    touched = set()
    for action, target, data in plan:
        if action == "new":
            color = colors[target]
            db.add(models.PlmRoll(plm_color_id=color.id, roll_no=data.roll_no.strip(),
                                  **{k: getattr(data, k) for k in _IMPORT_FIELDS}))
            touched.add(color.id)
        elif action == "update":
            for k, v in data.items():
                setattr(target, k, v)
            touched.add(target.plm_color_id)
    db.flush()
    for color in colors.values():
        if color.id in touched:
            _sync_color_quantity(db, color)
    record_audit(db, user, action="rolls_imported", entity_type="plm", entity_id=plm.id, plm_id=plm.id,
                 summary=f"PLM {plm.plm_code}: imported {counts['new']} new, {counts['update']} updated roll(s)"
                         + (f", new colors {', '.join(new_colors.values())}" if new_colors else ""))
    db.commit()
    return schemas.RollImportResult(applied=True, **result)


@router.patch("/rolls/{roll_id}", response_model=schemas.PlmRollOut)
def update_roll(roll_id: UUID, payload: schemas.PlmRollUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    roll = db.query(models.PlmRoll).get(roll_id)
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    updates = payload.model_dump(exclude_none=True)
    for key in ("marked_width", "marked_length", "planned_quantity"):
        if key in updates and float(updates[key]) <= 0:
            raise HTTPException(status_code=422, detail=f"{key} must be greater than zero")
    if "supplier_total_point" in updates and float(updates["supplier_total_point"]) < 0:
        raise HTTPException(status_code=422, detail="supplier_total_point must be >= 0")
    for k, v in updates.items():
        setattr(roll, k, v)
    db.flush()
    color = db.query(models.PlmColor).get(roll.plm_color_id)
    if color:
        _validate_color_roll_totals(db, color)
    record_audit(db, user, action="planned_roll_updated", entity_type="plm_roll", entity_id=roll.id,
                 plm_id=color.plm_id if color else None,
                 summary=f"Planned roll {roll.roll_no} updated: {', '.join(updates) or 'no changes'}",
                 data={k: str(v) for k, v in updates.items()})
    db.commit()
    db.refresh(roll)
    return roll


# QC report tables that point at a PLM, with the name shown when they block a delete.
_PLM_REPORT_TABLES = [
    ("fabric_inspection_reports", "4-Point inspection"), ("fabric_correlation_reports", "Correlation"),
    ("skew_bowing_reports", "Skew & Bowing"), ("gsm_check_reports", "GSM"), ("moisture_check_reports", "Moisture"),
    ("fabric_relaxation_reports", "Relaxation"), ("shade_check_reports", "Shade check"),
    ("shrinkage_pattern_reports", "Shrinkage"), ("color_continuity_cards", "Color continuity"),
    ("factory_reports", "Factory report"), ("inspection_reports", "Inspection report"),
]


def plm_report_names(db: Session, plm_ids: list) -> list[str]:
    """Names of the QC reports already saved for these PLMs (empty = safe to delete)."""
    if not plm_ids:
        return []
    from sqlalchemy import bindparam, text
    found = []
    for table, label in _PLM_REPORT_TABLES:
        stmt = text(f"SELECT 1 FROM {table} WHERE plm_id IN :ids LIMIT 1").bindparams(bindparam("ids", expanding=True))
        if db.execute(stmt, {"ids": list(plm_ids)}).first():
            found.append(label)
    return found


@router.delete("/{plm_id}", status_code=204)
def delete_plm(plm_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    """Deletes a PLM with its colours, planned rolls and schedules. A PLM that already has QC
    reports is kept, so no inspection result is ever lost."""
    from app.features.attachments.models import Attachment

    plm = db.query(models.Plm).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    reports = plm_report_names(db, [plm.id])
    if reports:
        raise HTTPException(status_code=409, detail=f"PLM {plm.plm_code} already has QC reports ({', '.join(reports)}), so it cannot be deleted.")
    db.query(models.InspectionSchedule).filter(models.InspectionSchedule.plm_id == plm.id).delete(synchronize_session=False)
    db.query(Attachment).filter(Attachment.owner_id == plm.id).delete(synchronize_session=False)
    record_audit(db, user, action="plm_deleted", entity_type="plm", entity_id=plm.id, plm_id=plm.id,
                 summary=f"PLM {plm.plm_code} deleted")
    db.delete(plm)  # colours and planned rolls go with it (cascade)
    db.commit()
    return Response(status_code=204)


@router.delete("/rolls/{roll_id}", status_code=204)
def delete_roll(roll_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin())):
    """Removes a planned roll (e.g. a wrong line from a mill report). A roll the app has already
    inspected (4-point) keeps its plan, so its report and correlation stay intact."""
    from app.features.fabric_inspection.models import FabricInspectionReport, FabricInspectionRoll

    roll = db.query(models.PlmRoll).get(roll_id)
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    color = db.query(models.PlmColor).get(roll.plm_color_id)
    inspected = (
        db.query(FabricInspectionRoll.id)
        .join(FabricInspectionReport, FabricInspectionRoll.report_id == FabricInspectionReport.id)
        .filter(FabricInspectionReport.plm_id == color.plm_id, FabricInspectionRoll.roll_no == roll.roll_no)
        .first()
    )
    if inspected:
        raise HTTPException(status_code=409, detail=f"Roll {roll.roll_no} is already inspected in the app, so it cannot be deleted.")
    db.delete(roll)
    db.flush()
    record_audit(db, user, action="planned_roll_deleted", entity_type="plm_roll", entity_id=roll.id,
                 plm_id=color.plm_id, summary=f"Planned roll {roll.roll_no} ({color.colour_code}) deleted")
    db.commit()
    return Response(status_code=204)


@router.get("", response_model=List[schemas.PlmOut])
def list_plms(
    response: Response,
    buyer_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    fabric_receive_id: Optional[UUID] = None,
    consignment: Optional[str] = None,
    q: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    lite: bool = False,
    db: Session = Depends(get_db),
):
    qset = db.query(models.Plm)
    if buyer_id:
        qset = qset.filter(models.Plm.buyer_id == buyer_id)
    if supplier_id:
        qset = qset.filter(models.Plm.supplier_id == supplier_id)
    if fabric_receive_id:
        qset = qset.filter(models.Plm.fabric_receive_id == fabric_receive_id)
    if consignment:
        qset = qset.filter(models.Plm.consignment.ilike(f"%{consignment.strip()}%"))
    qset = apply_text_search(qset, models.Plm, q, ("plm_code", "consignment", "plm_style", "fabric_type"))
    qset = apply_year_month(qset, models.Plm.plm_date, year, month)
    if date_from:
        qset = qset.filter(models.Plm.plm_date >= date_from)
    if date_to:
        qset = qset.filter(models.Plm.plm_date <= date_to)
    count_query = qset
    page = paginate(qset.order_by(models.Plm.created_at.desc()), response, limit, offset, count_query=count_query)
    ids = [row.id for row in page.with_entities(models.Plm.id).all()]
    if not ids:
        return []
    loaded = {plm.id: plm for plm in _plm_query(db, lite=lite).filter(models.Plm.id.in_(ids)).all()}
    if lite:
        counts = dict(
            db.query(models.PlmColor.plm_id, func.count(models.PlmRoll.id))
            .join(models.PlmRoll, models.PlmRoll.plm_color_id == models.PlmColor.id)
            .filter(models.PlmColor.plm_id.in_(ids))
            .group_by(models.PlmColor.plm_id)
            .all()
        )
        for plm in loaded.values():
            plm.roll_count = int(counts.get(plm.id, 0))
    return [apply_live_material(loaded[i]) for i in ids if i in loaded]


def _schedule_query(db: Session):
    return db.query(models.InspectionSchedule).options(
        joinedload(models.InspectionSchedule.plm).joinedload(models.Plm.buyer),
        joinedload(models.InspectionSchedule.plm).joinedload(models.Plm.supplier),
    )


SCHEDULE_STATUSES = ("pending", "in_progress", "completed")
OPEN_SCHEDULE_STATUSES = ("pending", "in_progress")


def _clean_text(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def _attach_schedule_progress(db: Session, rows):
    """Planned rolls vs rolls with a 4-point entry, per PLM, in two grouped queries."""
    from app.features.fabric_inspection.models import FabricInspectionReport, FabricInspectionRoll

    rows = [r for r in rows if r is not None]
    plm_ids = {r.plm_id for r in rows}
    if not plm_ids:
        return rows
    totals = dict(
        db.query(models.PlmColor.plm_id, func.count(models.PlmRoll.id))
        .join(models.PlmRoll, models.PlmRoll.plm_color_id == models.PlmColor.id)
        .filter(models.PlmColor.plm_id.in_(plm_ids))
        .group_by(models.PlmColor.plm_id)
        .all()
    )
    inspected = dict(
        db.query(FabricInspectionReport.plm_id, func.count(func.distinct(FabricInspectionRoll.roll_no)))
        .join(FabricInspectionRoll, FabricInspectionRoll.report_id == FabricInspectionReport.id)
        .join(models.PlmColor, models.PlmColor.plm_id == FabricInspectionReport.plm_id)
        .join(models.PlmRoll, (models.PlmRoll.plm_color_id == models.PlmColor.id)
              & (models.PlmRoll.roll_no == FabricInspectionRoll.roll_no))
        .filter(FabricInspectionReport.plm_id.in_(plm_ids))
        .group_by(FabricInspectionReport.plm_id)
        .all()
    )
    for r in rows:
        r.total_rolls = int(totals.get(r.plm_id, 0))
        r.inspected_rolls = int(inspected.get(r.plm_id, 0))
    return rows


def _one_schedule(db: Session, schedule_id) -> models.InspectionSchedule:
    row = _schedule_query(db).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    _attach_schedule_progress(db, [row])
    return row


def _ensure_no_other_open_schedule(db: Session, plm: models.Plm, exclude_id=None):
    q = db.query(models.InspectionSchedule).filter(
        models.InspectionSchedule.plm_id == plm.id,
        models.InspectionSchedule.status.in_(OPEN_SCHEDULE_STATUSES),
    )
    if exclude_id is not None:
        q = q.filter(models.InspectionSchedule.id != exclude_id)
    other = q.first()
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"PLM {plm.plm_code} already has an open schedule on {other.scheduled_date.strftime('%d-%m-%Y')}. "
                   f"Reschedule or complete that one instead.",
        )


def _ensure_not_past(day: date):
    if day < factory_today():
        raise HTTPException(status_code=422, detail="Schedule date cannot be in the past")


# Keep static /schedules routes ABOVE /{plm_id} so route matching is unambiguous.
@router.post("/schedules", response_model=schemas.InspectionScheduleOut)
def create_schedule(payload: schemas.InspectionScheduleIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("inspection"))):
    plm = db.query(models.Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    _ensure_not_past(payload.scheduled_date)
    _ensure_no_other_open_schedule(db, plm)
    row = models.InspectionSchedule(
        plm_id=plm.id,
        scheduled_date=payload.scheduled_date,
        status="pending",
        assigned_to=_clean_text(payload.assigned_to),
        machine_note=_clean_text(payload.machine_note),
    )
    stamp_creator(row, user)
    db.add(row)
    db.flush()
    record_audit(db, user, action="schedule_created", entity_type="inspection_schedule", entity_id=row.id, plm_id=plm.id,
                 summary=f"Schedule for {plm.plm_code} on {payload.scheduled_date.isoformat()}"
                         + (f" (assigned to {row.assigned_to})" if row.assigned_to else ""))
    db.commit()
    return _one_schedule(db, row.id)


@router.get("/schedules/assignees", response_model=List[schemas.ScheduleAssignee])
def schedule_assignees(db: Session = Depends(get_db), _: CurrentUser = Depends(require_roles("inspection"))):
    """Labels of active floor access codes, offered as 'assign to' choices."""
    from app.features.auth.models import AccessCode

    rows = (
        db.query(AccessCode.label, AccessCode.role)
        .filter(AccessCode.is_active.is_(True), AccessCode.role.in_(("worker", "inspection")))
        .order_by(AccessCode.role, AccessCode.label)
        .all()
    )
    seen, out = set(), []
    for label, role in rows:
        key = (label or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(schemas.ScheduleAssignee(label=label.strip(), role=str(getattr(role, "value", role))))
    return out


@router.get("/schedules/list", response_model=List[schemas.InspectionScheduleOut])
def list_schedules(
    response: Response,
    status: Optional[str] = None,
    plm_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    q: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = _schedule_query(db)
    if status:
        query = query.filter(models.InspectionSchedule.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if plm_id:
        query = query.filter(models.InspectionSchedule.plm_id == plm_id)
    if date_from:
        query = query.filter(models.InspectionSchedule.scheduled_date >= date_from)
    if date_to:
        query = query.filter(models.InspectionSchedule.scheduled_date <= date_to)
    if q and q.strip():
        query = query.join(models.Plm, models.Plm.id == models.InspectionSchedule.plm_id).filter(
            models.Plm.plm_code.ilike(f"%{q.strip()}%")
        )
    query = query.order_by(models.InspectionSchedule.scheduled_date, models.InspectionSchedule.created_at)
    return _attach_schedule_progress(db, paginate(query, response, limit, offset, default=2000, maximum=2000).all())


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("inspection"))):
    row = _schedule_query(db).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    record_audit(db, user, action="schedule_deleted", entity_type="inspection_schedule", entity_id=row.id, plm_id=row.plm_id,
                 summary=f"Schedule for {row.plm_code} on {row.scheduled_date.isoformat()} deleted")
    db.delete(row)
    db.commit()


@router.patch("/schedules/{schedule_id}/status", response_model=schemas.InspectionScheduleOut)
def update_schedule_status(schedule_id: UUID, payload: schemas.ScheduleStatusUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("inspection", "worker"))):
    row = _schedule_query(db).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if user.role == "worker" and not user.is_admin and payload.status != "in_progress":
        raise HTTPException(status_code=403, detail="Workers can only start a scheduled inspection")
    if payload.status not in SCHEDULE_STATUSES:
        raise HTTPException(status_code=422, detail="status must be pending, in_progress, or completed")
    before = row.status
    if before == "completed" and payload.status in OPEN_SCHEDULE_STATUSES:
        _ensure_no_other_open_schedule(db, row.plm, exclude_id=row.id)
    row.status = payload.status
    if before != payload.status:
        record_audit(db, user, action="schedule_status", entity_type="inspection_schedule", entity_id=row.id, plm_id=row.plm_id,
                     summary=f"Schedule {row.plm_code}: {before} -> {payload.status}")
    db.commit()
    return _one_schedule(db, row.id)


@router.patch("/schedules/{schedule_id}/reschedule", response_model=schemas.InspectionScheduleOut)
def reschedule(schedule_id: UUID, payload: schemas.ScheduleRescheduleUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("inspection"))):
    row = _schedule_query(db).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if row.status == "completed":
        raise HTTPException(status_code=409, detail="A completed schedule cannot be rescheduled")
    _ensure_not_past(payload.scheduled_date)
    before = row.scheduled_date
    row.scheduled_date = payload.scheduled_date
    record_audit(db, user, action="schedule_rescheduled", entity_type="inspection_schedule", entity_id=row.id, plm_id=row.plm_id,
                 summary=f"Schedule {row.plm_code}: {before.isoformat()} -> {payload.scheduled_date.isoformat()}")
    db.commit()
    return _one_schedule(db, row.id)


@router.patch("/schedules/{schedule_id}/assign", response_model=schemas.InspectionScheduleOut)
def assign_schedule(schedule_id: UUID, payload: schemas.ScheduleAssignUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles("inspection"))):
    row = _schedule_query(db).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    before = (row.assigned_to, row.machine_note)
    row.assigned_to = _clean_text(payload.assigned_to)
    row.machine_note = _clean_text(payload.machine_note)
    if before != (row.assigned_to, row.machine_note):
        record_audit(db, user, action="schedule_assigned", entity_type="inspection_schedule", entity_id=row.id, plm_id=row.plm_id,
                     summary=f"Schedule {row.plm_code}: assigned to {row.assigned_to or '-'}"
                             + (f", table {row.machine_note}" if row.machine_note else ""))
    db.commit()
    return _one_schedule(db, row.id)


@router.get("/{plm_id}", response_model=schemas.PlmOut)
def get_plm(plm_id: UUID, db: Session = Depends(get_db)):
    plm = _plm_query(db).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    return apply_live_material(plm)
