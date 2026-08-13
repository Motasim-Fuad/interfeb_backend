from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles
from app.features.plm.models import Plm, PlmColor, PlmRoll
from app.features.fabric_inspection import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report
from app.features.store.service import require_qc_float, get_float_qc_value

router = APIRouter(prefix="/fabric-inspection", tags=["Fabric Inspection (4-point)"])


def _query(db: Session):
    return db.query(models.FabricInspectionReport).options(
        joinedload(models.FabricInspectionReport.rolls).joinedload(models.FabricInspectionRoll.defects)
    )


def _attach_stats(report: models.FabricInspectionReport):
    for k, v in service.compute_report_stats(report).items():
        setattr(report, k, v)
    return report


def _planned_roll(db: Session, plm_id, roll_no: str):
    return (
        db.query(PlmRoll)
        .join(PlmColor, PlmRoll.plm_color_id == PlmColor.id)
        .filter(PlmColor.plm_id == plm_id, PlmRoll.roll_no == roll_no)
        .first()
    )


@router.post("", response_model=schemas.ReportOut)
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")

    # One 4-point report per PLM. This makes background auto-create idempotent.
    existing = _query(db).filter(models.FabricInspectionReport.plm_id == payload.plm_id).first()
    if existing:
        return _attach_stats(existing)

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
        color=payload.color,
        fabric_type=plm.fabric_type,
        fabric_composition=plm.fabric_composition,
        received_qty=configured_received if configured_received is not None else plm.allocated_quantity,
        checked_qty=checked_qty,
        individual_standard_id=individual_id,
        individual_standard_snapshot=individual,
        average_standard_id=average_id,
        average_standard_snapshot=average,
        fabric_in_house_date=plm.received_at or payload.fabric_in_house_date,
        inspection_date=payload.inspection_date,
        consignment=plm.consignment,
        comments=payload.comments,
        measurement_unit_snapshot=plm.measurement_unit.value if hasattr(plm.measurement_unit, "value") else str(plm.measurement_unit),
        quantity_unit_snapshot=plm.quantity_unit,
        qc_setup_version_snapshot=plm.qc_setup_version or 1,
    )
    db.add(report)
    db.commit()
    return _attach_stats(_query(db).get(report.id))


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(plm_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    q = _query(db)
    if plm_id:
        q = q.filter(models.FabricInspectionReport.plm_id == plm_id)
    reports = q.order_by(models.FabricInspectionReport.created_at.desc()).all()
    return [_attach_stats(r) for r in reports]


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = _query(db).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _attach_stats(report)


@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
def upsert_roll(report_id: UUID, payload: schemas.RollIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    report = db.query(models.FabricInspectionReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    planned = _planned_roll(db, report.plm_id, payload.roll_no)
    if not planned:
        raise HTTPException(status_code=422, detail="Roll is not configured under this PLM in the dashboard")

    roll = db.query(models.FabricInspectionRoll).filter(
        models.FabricInspectionRoll.report_id == report_id,
        models.FabricInspectionRoll.roll_no == payload.roll_no,
    ).first()
    if not roll:
        roll = models.FabricInspectionRoll(report_id=report_id, roll_no=payload.roll_no)
        db.add(roll)
        db.flush()

    roll.colour_code = planned.plm_color.colour_code
    roll.width_marked = planned.marked_width
    roll.length_marked = planned.marked_length
    roll.width_actual = payload.width_actual
    roll.length_actual = payload.length_actual
    roll.remarks = payload.remarks

    for old in list(roll.defects):
        db.delete(old)
    db.flush()
    try:
        for d in payload.defects:
            snap = service.resolve_defect_snapshot(db, d.defect_type_id, d.range_config_id, d.count)
            db.add(models.FabricInspectionRollDefect(
                roll_id=roll.id,
                defect_type_id=d.defect_type_id,
                range_config_id=d.range_config_id,
                count=d.count,
                points_snapshot=snap["points"],
                defect_type_name_snapshot=snap["defect_type_name"],
                range_label_snapshot=snap["range_label"],
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
    db.commit()
    db.refresh(roll)

    upsert_inspection_report(db, plm_id=report.plm_id, roll_number=roll.roll_no, color_name=roll.colour_code, module_field="fabric_inspection_roll_id", module_row_id=roll.id)
    db.commit()
    return roll


@router.put("/rolls/{roll_id}", response_model=schemas.RollOut)
def update_roll(roll_id: UUID, payload: schemas.RollIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    roll = db.query(models.FabricInspectionRoll).get(roll_id)
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    if payload.roll_no != roll.roll_no:
        raise HTTPException(status_code=422, detail="roll_no cannot be changed after inspection starts")
    return upsert_roll(roll.report_id, payload, db, _)
