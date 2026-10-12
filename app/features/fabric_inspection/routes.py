from datetime import date, datetime, time, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.cache import invalidate_prefix
from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles
from app.core.list_query import paginate
from app.core.sync import SYNC_STALE_HEADER, as_utc_naive as _as_utc_naive
from app.features.admin_config.models import Buyer, Supplier
from app.features.audit.service import actor_label, record_audit, stamp_creator
from app.features.plm.models import Plm, PlmColor, PlmRoll
from app.features.fabric_inspection import models, schemas, service
from app.features.inspection_report.models import InspectionReport
from app.features.inspection_report.service import upsert_inspection_report
from app.features.store.service import require_qc_float, get_float_qc_value

router = APIRouter(prefix="/fabric-inspection", tags=["Fabric Inspection (4-point)"])


def _query(db: Session):
    return db.query(models.FabricInspectionReport).options(
        selectinload(models.FabricInspectionReport.rolls).selectinload(models.FabricInspectionRoll.defects)
    )


def _attach_stats(report: models.FabricInspectionReport):
    for k, v in service.compute_report_stats(report).items():
        setattr(report, k, v)
    return report


def _planned_roll(db: Session, plm_id, roll_no: str):
    return (
        db.query(PlmRoll)
        .options(joinedload(PlmRoll.plm_color))
        .join(PlmColor, PlmRoll.plm_color_id == PlmColor.id)
        .filter(PlmColor.plm_id == plm_id, PlmRoll.roll_no == roll_no)
        .first()
    )


def _ensure_report(db: Session, plm: Plm, payload: Optional[schemas.ReportCreate] = None) -> models.FabricInspectionReport:
    # One 4-point report per PLM. This makes background auto-create idempotent.
    existing = db.query(models.FabricInspectionReport).filter(models.FabricInspectionReport.plm_id == plm.id).first()
    if existing:
        return existing

    individual, individual_id = require_qc_float(
        db, plm, "fabric_inspection", "individual_standard",
        legacy_standard_module="fabric_inspection_individual",
    )
    average, average_id = require_qc_float(
        db, plm, "fabric_inspection", "average_standard",
        legacy_standard_module="fabric_inspection_average",
    )
    checked_qty = get_float_qc_value(plm, "fabric_inspection", "checked_quantity", None)
    configured_received = get_float_qc_value(plm, "fabric_inspection", "received_quantity", None)

    report = models.FabricInspectionReport(
        plm_id=plm.id,
        color=payload.color if payload else None,
        fabric_type=plm.fabric_type,
        fabric_composition=plm.fabric_composition,
        received_qty=configured_received if configured_received is not None else plm.allocated_quantity,
        checked_qty=checked_qty,
        individual_standard_id=individual_id,
        individual_standard_snapshot=individual,
        average_standard_id=average_id,
        average_standard_snapshot=average,
        fabric_in_house_date=plm.received_at or (payload.fabric_in_house_date if payload else None),
        inspection_date=(payload.inspection_date if payload else None) or date.today(),
        consignment=plm.consignment,
        comments=payload.comments if payload else None,
        measurement_unit_snapshot=plm.measurement_unit.value if hasattr(plm.measurement_unit, "value") else str(plm.measurement_unit),
        quantity_unit_snapshot=plm.quantity_unit,
        qc_setup_version_snapshot=plm.qc_setup_version or 1,
    )
    db.add(report)
    db.flush()
    return report


def _refresh_derived(db: Session, plm_id) -> None:
    """Keep correlation + factory report in step with 4-point saves so their GETs stay cheap."""
    from app.features.correlation.routes import refresh_correlation_for_plm
    from app.features.factory_report.routes import refresh_factory_report_for_plm

    plm = db.query(Plm).get(plm_id)
    if not plm:
        return
    for refresh in (refresh_correlation_for_plm, refresh_factory_report_for_plm):
        savepoint = db.begin_nested()
        try:
            refresh(db, plm)
            savepoint.commit()
        except Exception:
            savepoint.rollback()


def _save_roll(
    db: Session,
    report: models.FabricInspectionReport,
    payload: schemas.RollIn,
    user: CurrentUser,
    response: Optional[Response] = None,
) -> models.FabricInspectionRoll:
    planned = _planned_roll(db, report.plm_id, payload.roll_no)
    if not planned:
        raise HTTPException(status_code=422, detail="Roll is not configured under this PLM in the dashboard")

    roll = db.query(models.FabricInspectionRoll).filter(
        models.FabricInspectionRoll.report_id == report.id,
        models.FabricInspectionRoll.roll_no == payload.roll_no,
    ).first()

    client_ts = _as_utc_naive(payload.client_updated_at)
    if roll is not None and client_ts is not None and roll.client_updated_at is not None and client_ts < roll.client_updated_at:
        if response is not None:
            response.headers[SYNC_STALE_HEADER] = "1"
        return roll

    is_new = roll is None
    if is_new:
        roll = models.FabricInspectionRoll(report_id=report.id, roll_no=payload.roll_no)
        stamp_creator(roll, user)
        db.add(roll)
        db.flush()

    roll.colour_code = planned.plm_color.colour_code
    roll.width_marked = planned.marked_width
    roll.length_marked = planned.marked_length
    roll.width_actual = payload.width_actual
    roll.length_actual = payload.length_actual
    roll.remarks = payload.remarks
    roll.inspector_name = payload.inspector_name or actor_label(user) or roll.inspector_name
    roll.inspected_at = _as_utc_naive(payload.inspected_at) or client_ts or datetime.utcnow()
    roll.client_updated_at = client_ts

    for old in list(roll.defects):
        db.delete(old)
    db.flush()
    lookup = service.load_defect_lookup(
        db, [d.defect_type_id for d in payload.defects], [d.range_config_id for d in payload.defects],
    )
    try:
        for d in payload.defects:
            snap = service.resolve_defect_snapshot(db, d.defect_type_id, d.range_config_id, d.count, lookup=lookup)
            db.add(models.FabricInspectionRollDefect(
                roll_id=roll.id,
                defect_type_id=d.defect_type_id,
                range_config_id=d.range_config_id,
                count=d.count,
                points_snapshot=snap["points"],
                defect_type_name_snapshot=snap["defect_type_name"],
                range_label_snapshot=snap["range_label"],
                position_yard=d.position_yard,
                side=d.side,
                photo_url=d.photo_url,
                recorded_at=_as_utc_naive(d.recorded_at),
            ))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.flush()
    db.refresh(roll)
    service.recalculate_roll(
        db,
        roll,
        float(report.individual_standard_snapshot) if report.individual_standard_snapshot is not None else None,
        report.measurement_unit_snapshot,
        report.quantity_unit_snapshot,
    )
    record_audit(
        db, user,
        action="roll_created" if is_new else "roll_updated",
        entity_type="fabric_inspection_roll",
        entity_id=roll.id,
        plm_id=report.plm_id,
        summary=f"Roll {roll.roll_no}: {roll.total_points} pts, "
                f"{'PASS' if roll.is_pass else 'FAIL' if roll.is_pass is False else 'not evaluated'}",
        data={"defects": len(payload.defects), "percent_points": float(roll.percent_points) if roll.percent_points is not None else None},
    )
    db.commit()
    db.refresh(roll)

    upsert_inspection_report(db, plm_id=report.plm_id, roll_number=roll.roll_no, color_name=roll.colour_code, module_field="fabric_inspection_roll_id", module_row_id=roll.id)
    db.commit()
    _refresh_derived(db, report.plm_id)
    db.commit()
    invalidate_prefix("analytics")
    db.refresh(roll)
    return roll


@router.post("", response_model=schemas.ReportOut)
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    report = _ensure_report(db, plm, payload)
    db.commit()
    return _attach_stats(_query(db).get(report.id))


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(
    response: Response,
    plm_id: Optional[UUID] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = _query(db)
    if plm_id:
        q = q.filter(models.FabricInspectionReport.plm_id == plm_id)
    q = q.order_by(models.FabricInspectionReport.created_at.desc())
    if not plm_id:
        q = paginate(q, response, limit, offset, default=50, maximum=200)
    return [_attach_stats(r) for r in q.all()]


@router.post("/preview", response_model=schemas.PreviewOut)
def preview_score(payload: schemas.PreviewIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    """Live 4-point score for the entry screen, using the PLM's frozen QC snapshot."""
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    report = db.query(models.FabricInspectionReport).filter(models.FabricInspectionReport.plm_id == plm.id).first()
    if report is not None:
        standard = float(report.individual_standard_snapshot) if report.individual_standard_snapshot is not None else None
        mu, qu = report.measurement_unit_snapshot, report.quantity_unit_snapshot
    else:
        standard = get_float_qc_value(plm, "fabric_inspection", "individual_standard", None)
        mu = plm.measurement_unit.value if hasattr(plm.measurement_unit, "value") else str(plm.measurement_unit)
        qu = plm.quantity_unit
    try:
        percent, is_pass = service.score_roll(payload.total_points, payload.width_actual, payload.length_actual, standard, mu, qu)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"percent_points": percent, "is_pass": is_pass, "individual_standard": standard}


@router.get("/rolls", response_model=List[schemas.RollListItem])
def list_rolls(
    response: Response,
    plm_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    buyer_id: Optional[UUID] = None,
    disposition: Optional[str] = None,
    is_pass: Optional[bool] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    q: Optional[str] = None,
    limit: Optional[int] = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Flat, paginated roll list for the dashboard disposition queue."""
    Roll = models.FabricInspectionRoll
    Report = models.FabricInspectionReport
    event_at = func.coalesce(Roll.inspected_at, Roll.created_at)
    query = (
        db.query(Roll, Report.plm_id, Plm.plm_code, Plm.supplier_id, Supplier.name, Plm.buyer_id, Buyer.name)
        .join(Report, Report.id == Roll.report_id)
        .join(Plm, Plm.id == Report.plm_id)
        .outerjoin(Supplier, Supplier.id == Plm.supplier_id)
        .outerjoin(Buyer, Buyer.id == Plm.buyer_id)
    )
    if plm_id:
        query = query.filter(Report.plm_id == plm_id)
    if supplier_id:
        query = query.filter(Plm.supplier_id == supplier_id)
    if buyer_id:
        query = query.filter(Plm.buyer_id == buyer_id)
    if disposition:
        query = query.filter(Roll.disposition.in_([d.strip() for d in disposition.split(",") if d.strip()]))
    if is_pass is not None:
        query = query.filter(Roll.is_pass.is_(is_pass))
    if date_from:
        query = query.filter(event_at >= datetime.combine(date_from, time.min))
    if date_to:
        query = query.filter(event_at <= datetime.combine(date_to, time.max))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(Roll.roll_no.ilike(like) | Plm.plm_code.ilike(like) | Roll.colour_code.ilike(like))
    query = query.order_by(event_at.desc(), Roll.roll_no)
    rows = paginate(query, response, limit, offset, default=50, maximum=500).all()
    return [
        {
            "id": roll.id,
            "report_id": roll.report_id,
            "plm_id": plm_id_,
            "plm_code": plm_code,
            "supplier_id": supplier_id_,
            "supplier_name": supplier_name,
            "buyer_id": buyer_id_,
            "buyer_name": buyer_name,
            "roll_no": roll.roll_no,
            "colour_code": roll.colour_code,
            "total_points": roll.total_points or 0,
            "percent_points": float(roll.percent_points) if roll.percent_points is not None else None,
            "is_pass": roll.is_pass,
            "disposition": roll.disposition or "pending",
            "disposition_note": roll.disposition_note,
            "shade_band": roll.shade_band,
            "inspector_name": roll.inspector_name,
            "inspected_at": roll.inspected_at or roll.created_at,
        }
        for roll, plm_id_, plm_code, supplier_id_, supplier_name, buyer_id_, buyer_name in rows
    ]


@router.patch("/rolls/{roll_id}/disposition", response_model=schemas.RollOut)
def set_disposition(
    roll_id: UUID,
    payload: schemas.DispositionUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("inspection")),
):
    roll = db.query(models.FabricInspectionRoll).options(joinedload(models.FabricInspectionRoll.report)).get(roll_id)
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    before = roll.disposition
    roll.disposition = payload.disposition
    roll.disposition_note = payload.disposition_note
    if payload.shade_band is not None:
        roll.shade_band = payload.shade_band.strip() or None
    record_audit(
        db, user,
        action="roll_disposition",
        entity_type="fabric_inspection_roll",
        entity_id=roll.id,
        plm_id=roll.report.plm_id if roll.report else None,
        summary=f"Roll {roll.roll_no}: {before} -> {payload.disposition}",
        data={"note": payload.disposition_note, "shade_band": roll.shade_band},
    )
    db.commit()
    invalidate_prefix("analytics")
    db.refresh(roll)
    return roll


@router.post("/by-plm/{plm_id}/rolls", response_model=schemas.RollOut)
def upsert_roll_by_plm(
    plm_id: UUID,
    payload: schemas.RollIn,
    response: Response,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("inspection", "worker")),
):
    """Offline-friendly save: devices only need the PLM id; the report is created on demand."""
    plm = db.query(Plm).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    report = _ensure_report(db, plm)
    return _save_roll(db, report, payload, user, response)


@router.put("/rolls/{roll_id}", response_model=schemas.RollOut)
def update_roll(
    roll_id: UUID,
    payload: schemas.RollIn,
    response: Response,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("inspection", "worker")),
):
    roll = db.query(models.FabricInspectionRoll).get(roll_id)
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    if payload.roll_no != roll.roll_no:
        raise HTTPException(status_code=422, detail="roll_no cannot be changed after inspection starts")
    return _save_roll(db, roll.report, payload, user, response)


@router.delete("/rolls/{roll_id}", status_code=204)
def delete_roll(
    roll_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("inspection", "worker")),
):
    """Floor undo for a roll saved by mistake. Once QC has decided on the roll it is locked."""
    roll = db.query(models.FabricInspectionRoll).options(joinedload(models.FabricInspectionRoll.report)).get(roll_id)
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    if (roll.disposition or "pending") != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Roll {roll.roll_no} already has a QC decision ({roll.disposition}); ask the admin to clear it first",
        )
    plm_id = roll.report.plm_id if roll.report else None
    db.query(InspectionReport).filter(InspectionReport.fabric_inspection_roll_id == roll.id).update(
        {InspectionReport.fabric_inspection_roll_id: None}, synchronize_session=False,
    )
    record_audit(
        db, user,
        action="roll_deleted",
        entity_type="fabric_inspection_roll",
        entity_id=roll.id,
        plm_id=plm_id,
        summary=f"Roll {roll.roll_no} deleted ({roll.total_points} pts)",
        data={"defects": len(roll.defects), "width_actual": float(roll.width_actual) if roll.width_actual is not None else None,
              "length_actual": float(roll.length_actual) if roll.length_actual is not None else None},
    )
    db.delete(roll)
    db.commit()
    if plm_id is not None:
        _refresh_derived(db, plm_id)
        db.commit()
    invalidate_prefix("analytics")
    return Response(status_code=204)


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = _query(db).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _attach_stats(report)


@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
def upsert_roll(
    report_id: UUID,
    payload: schemas.RollIn,
    response: Response,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("inspection", "worker")),
):
    report = db.query(models.FabricInspectionReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _save_roll(db, report, payload, user, response)
