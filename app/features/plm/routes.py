from copy import deepcopy
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles, require_admin
from app.core.list_query import apply_page, apply_text_search, apply_year_month
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


def _plm_query(db: Session):
    return db.query(models.Plm).options(
        joinedload(models.Plm.fabric_receive),
        joinedload(models.Plm.buyer),
        joinedload(models.Plm.supplier),
        joinedload(models.Plm.material),
        joinedload(models.Plm.colors).joinedload(models.PlmColor.rolls),
    )


def _validate_allocation(db: Session, receive: FabricReceive, quantity: Optional[float], exclude_plm_id=None):
    if quantity is None or receive.received_quantity is None:
        return
    q = db.query(models.Plm).filter(models.Plm.fabric_receive_id == receive.id)
    if exclude_plm_id:
        q = q.filter(models.Plm.id != exclude_plm_id)
    used = sum(float(p.allocated_quantity or 0) for p in q.all())
    if used + float(quantity) > float(receive.received_quantity) + 1e-9:
        raise HTTPException(
            status_code=422,
            detail=f"PLM allocation exceeds consignment received quantity. Available: {float(receive.received_quantity) - used:g} {receive.quantity_unit}",
        )


def _validate_planning_payload(payload: schemas.PlmCreate, receive: FabricReceive) -> None:
    if receive.qc_setup_status != "finalized":
        raise HTTPException(status_code=422, detail="Finalize the consignment QC setup in Admin Dashboard before creating PLMs")
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
            if roll.planned_quantity is None or float(roll.planned_quantity) <= 0:
                missing.append("planned quantity")
            if roll.supplier_total_point is None:
                missing.append("supplier total point")
            elif float(roll.supplier_total_point) < 0:
                missing.append("supplier total point must be >= 0")
            if missing:
                raise HTTPException(
                    status_code=422,
                    detail=f"Roll {roll.roll_no} under {color.colour_code} is missing/invalid: {', '.join(missing)}",
                )
            roll_total += float(roll.planned_quantity)
        if roll_total > float(color.planned_quantity) + 1e-9:
            raise HTTPException(
                status_code=422,
                detail=f"Roll planned quantities for color {color.colour_code} exceed that color's planned quantity",
            )
    if color_total > float(payload.allocated_quantity) + 1e-9:
        raise HTTPException(status_code=422, detail="Color planned quantities exceed the PLM allocated quantity")


def _require_roll_fields(roll: schemas.PlmRollIn) -> None:
    missing = []
    if not (roll.roll_no or "").strip():
        missing.append("roll number")
    if roll.marked_width is None or float(roll.marked_width) <= 0:
        missing.append("marked width")
    if roll.marked_length is None or float(roll.marked_length) <= 0:
        missing.append("marked length")
    if roll.planned_quantity is None or float(roll.planned_quantity) <= 0:
        missing.append("roll quantity")
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
    rows = db.query(models.PlmRoll).filter(models.PlmRoll.plm_color_id == color.id).all()
    total = sum(float(r.planned_quantity or 0) for r in rows)
    if color.planned_quantity is None:
        if total > 0:
            color.planned_quantity = total
        return
    if total > float(color.planned_quantity) + 1e-9:
        color.planned_quantity = total


def _validate_color_roll_totals(db: Session, color: models.PlmColor) -> None:
    _sync_color_quantity(db, color)


@router.post("", response_model=schemas.PlmOut)
def create_plm(payload: schemas.PlmCreate, db: Session = Depends(get_db), _=Depends(require_admin())):
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
    db.add(plm)
    db.flush()

    seen_colors = set()
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
        seen_rolls = set()
        for roll_in in color_in.rolls:
            roll_no = roll_in.roll_no.strip()
            if not roll_no or roll_no.lower() in seen_rolls:
                raise HTTPException(status_code=422, detail=f"Duplicate/empty roll number in {color.colour_code}: {roll_no}")
            seen_rolls.add(roll_no.lower())
            db.add(models.PlmRoll(plm_color_id=color.id, **roll_in.model_dump()))

    db.commit()
    return _plm_query(db).get(plm.id)


@router.put("/{plm_id}", response_model=schemas.PlmOut)
def update_plm(plm_id: UUID, payload: schemas.PlmUpdate, db: Session = Depends(get_db), _=Depends(require_admin())):
    plm = _plm_query(db).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    if payload.allocated_quantity is not None and plm.fabric_receive:
        _validate_allocation(db, plm.fabric_receive, payload.allocated_quantity, exclude_plm_id=plm.id)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(plm, k, v)
    db.commit()
    return _plm_query(db).get(plm.id)


@router.post("/{plm_id}/colors", response_model=schemas.PlmColorOut)
def add_color(plm_id: UUID, payload: schemas.PlmColorIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
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
    for r in payload.rolls:
        _require_roll_fields(r)
        db.add(models.PlmRoll(plm_color_id=color.id, **r.model_dump()))
    db.flush()
    _sync_color_quantity(db, color)
    db.commit()
    db.refresh(color)
    return color


@router.post("/colors/{color_id}/rolls", response_model=schemas.PlmRollOut)
def add_roll(color_id: UUID, payload: schemas.PlmRollIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
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
    row = models.PlmRoll(plm_color_id=color_id, **payload.model_dump())
    row.roll_no = roll_no
    db.add(row)
    db.flush()
    _sync_color_quantity(db, color)
    db.commit()
    db.refresh(row)
    return row


@router.post("/colors/{color_id}/rolls/bulk", response_model=List[schemas.PlmRollOut])
def bulk_add_rolls(color_id: UUID, payload: schemas.PlmRollBulkCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    color = db.query(models.PlmColor).get(color_id)
    if not color:
        raise HTTPException(status_code=404, detail="PLM color not found")

    numbers = [str(x).strip() for x in payload.roll_numbers if str(x).strip()]
    if not numbers and payload.start_roll is not None and payload.end_roll is not None:
        numbers = [f"{payload.prefix}{n}" for n in range(payload.start_roll, payload.end_roll + 1)]

    existing = {
        r.roll_no.lower(): r for r in db.query(models.PlmRoll).filter(models.PlmRoll.plm_color_id == color_id).all()
    }
    created = []
    for roll_no in numbers:
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
                    payload.planned_quantity is None or payload.planned_quantity <= 0 or payload.supplier_total_point is None or payload.supplier_total_point < 0):
                raise HTTPException(
                    status_code=422,
                    detail=f"New roll {roll_no} requires marked_width, marked_length, planned_quantity and supplier_total_point (>= 0) from Dashboard",
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


@router.patch("/rolls/{roll_id}", response_model=schemas.PlmRollOut)
def update_roll(roll_id: UUID, payload: schemas.PlmRollUpdate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
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
    db.commit()
    db.refresh(roll)
    return roll


@router.get("", response_model=List[schemas.PlmOut])
def list_plms(
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
    db: Session = Depends(get_db),
):
    qset = _plm_query(db)
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
    qset = qset.order_by(models.Plm.created_at.desc())
    qset = apply_page(qset, limit=limit, offset=offset)
    return [apply_live_material(plm) for plm in qset.all()]


# Keep static /schedules routes ABOVE /{plm_id} so route matching is unambiguous.
@router.post("/schedules", response_model=schemas.InspectionScheduleOut)
def create_schedule(payload: schemas.InspectionScheduleIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    if not db.query(models.Plm).get(payload.plm_id):
        raise HTTPException(status_code=404, detail="PLM not found")
    row = models.InspectionSchedule(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/schedules/list", response_model=List[schemas.InspectionScheduleOut])
def list_schedules(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(models.InspectionSchedule)
    if status:
        q = q.filter(models.InspectionSchedule.status == status)
    return q.order_by(models.InspectionSchedule.scheduled_date).all()


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: UUID, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    row = db.query(models.InspectionSchedule).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(row)
    db.commit()


@router.patch("/schedules/{schedule_id}/status", response_model=schemas.InspectionScheduleOut)
def update_schedule_status(schedule_id: UUID, payload: schemas.ScheduleStatusUpdate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    row = db.query(models.InspectionSchedule).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if payload.status not in ("pending", "in_progress", "completed"):
        raise HTTPException(status_code=422, detail="status must be pending, in_progress, or completed")
    row.status = payload.status
    db.commit()
    db.refresh(row)
    return row


@router.patch("/schedules/{schedule_id}/reschedule", response_model=schemas.InspectionScheduleOut)
def reschedule(schedule_id: UUID, payload: schemas.ScheduleRescheduleUpdate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    row = db.query(models.InspectionSchedule).get(schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    row.scheduled_date = payload.scheduled_date
    row.status = "pending"
    db.commit()
    db.refresh(row)
    return row


@router.get("/{plm_id}", response_model=schemas.PlmOut)
def get_plm(plm_id: UUID, db: Session = Depends(get_db)):
    plm = _plm_query(db).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    return apply_live_material(plm)
