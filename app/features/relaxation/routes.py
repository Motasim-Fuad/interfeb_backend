from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles
from app.features.store.service import get_float_qc_value
from app.features.plm.models import Plm
from app.features.relaxation import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report
from app.features.attachments.service import get_attachments

router = APIRouter(prefix="/relaxation", tags=["Fabric Relaxation"])


def _attach_photos(db: Session, roll: models.FabricRelaxationRoll):
    roll.attachments = get_attachments(db, "relaxation_roll", roll.id)
    return roll


def _attach_roll_and_report_stats(report: models.FabricRelaxationReport):
    for roll in report.rolls:
        for k, v in service.compute_roll_fields(roll).items():
            setattr(roll, k, v)
    for k, v in service.compute_report_stats(report.rolls).items():
        setattr(report, k, v)
    return report


@router.post("", response_model=schemas.ReportOut)
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    existing = db.query(models.FabricRelaxationReport).options(joinedload(models.FabricRelaxationReport.rolls)).filter(models.FabricRelaxationReport.plm_id == plm.id).first()
    if existing:
        return _attach_roll_and_report_stats(existing)
    relaxation_hours = get_float_qc_value(plm, "relaxation", "relaxation_hours", 12)
    report = models.FabricRelaxationReport(
        **payload.model_dump(),
        relaxation_hours_snapshot=int(relaxation_hours or 12),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    _attach_roll_and_report_stats(report)
    return report


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(plm_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    q = db.query(models.FabricRelaxationReport).options(joinedload(models.FabricRelaxationReport.rolls))
    if plm_id:
        q = q.filter(models.FabricRelaxationReport.plm_id == plm_id)
    reports = q.order_by(models.FabricRelaxationReport.created_at.desc()).all()
    for report in reports:
        for roll in report.rolls:
            _attach_photos(db, roll)
        _attach_roll_and_report_stats(report)
    return reports


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = db.query(models.FabricRelaxationReport).options(joinedload(models.FabricRelaxationReport.rolls)).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    for roll in report.rolls:
        _attach_photos(db, roll)
    _attach_roll_and_report_stats(report)
    return report


@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
def add_roll(report_id: UUID, payload: schemas.RollIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    report = db.query(models.FabricRelaxationReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    roll = (db.query(models.FabricRelaxationRoll)
            .filter(models.FabricRelaxationRoll.report_id == report_id, models.FabricRelaxationRoll.roll_no == payload.roll_no)
            .first())
    if roll:
        for k, v in payload.model_dump().items():
            setattr(roll, k, v)
    else:
        roll = models.FabricRelaxationRoll(report_id=report_id, **payload.model_dump())
        db.add(roll)
    db.commit()
    db.refresh(roll)

    upsert_inspection_report(
        db, plm_id=report.plm_id, roll_number=roll.roll_no,
        module_field="relaxation_report_id", module_row_id=roll.id,
    )
    db.commit()

    _attach_photos(db, roll)
    for k, v in service.compute_roll_fields(roll).items():
        setattr(roll, k, v)
    return roll
