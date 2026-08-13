from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles
from app.features.plm.models import Plm
from app.features.shrinkage import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report

router = APIRouter(prefix="/shrinkage", tags=["Shrinkage Pattern"])


def _attach_measurement_fields(roll: models.ShrinkageRoll):
    for m in roll.measurements:
        for k, v in service.compute_measurement_fields(m).items():
            setattr(m, k, v)


def _attach_roll_and_report_stats(report: models.ShrinkagePatternReport):
    for roll in report.rolls:
        _attach_measurement_fields(roll)
        for k, v in service.compute_roll_fields(roll, None).items():
            setattr(roll, k, v)
    for k, v in service.compute_report_stats(report.rolls, None).items():
        setattr(report, k, v)
    return report


@router.post("", response_model=schemas.ReportOut)
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    existing = db.query(models.ShrinkagePatternReport).options(joinedload(models.ShrinkagePatternReport.rolls).joinedload(models.ShrinkageRoll.measurements)).filter(models.ShrinkagePatternReport.plm_id == plm.id).first()
    if existing:
        return _attach_roll_and_report_stats(existing)
    report = models.ShrinkagePatternReport(**payload.model_dump())
    db.add(report)
    db.commit()
    db.refresh(report)
    _attach_roll_and_report_stats(report)
    return report


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(plm_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    q = db.query(models.ShrinkagePatternReport).options(
        joinedload(models.ShrinkagePatternReport.rolls).joinedload(models.ShrinkageRoll.measurements)
    )
    if plm_id:
        q = q.filter(models.ShrinkagePatternReport.plm_id == plm_id)
    reports = q.order_by(models.ShrinkagePatternReport.created_at.desc()).all()
    for report in reports:
        _attach_roll_and_report_stats(report)
    return reports


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = (
        db.query(models.ShrinkagePatternReport)
        .options(joinedload(models.ShrinkagePatternReport.rolls).joinedload(models.ShrinkageRoll.measurements))
        .get(report_id)
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    _attach_roll_and_report_stats(report)
    return report


@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
def upsert_roll(report_id: UUID, payload: schemas.RollIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    report = db.query(models.ShrinkagePatternReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    roll = (
        db.query(models.ShrinkageRoll)
        .filter(models.ShrinkageRoll.report_id == report_id, models.ShrinkageRoll.roll_no == payload.roll_no)
        .first()
    )
    if roll:
        for k, v in payload.model_dump(exclude={"measurements"}).items():
            setattr(roll, k, v)
    else:
        roll = models.ShrinkageRoll(report_id=report_id, **payload.model_dump(exclude={"measurements"}))
        db.add(roll)
    db.flush()

    for m in list(roll.measurements):
        db.delete(m)
    db.flush()
    for m in payload.measurements:
        db.add(models.ShrinkageMeasurement(roll_id=roll.id, **m.model_dump()))

    db.commit()
    db.refresh(roll)

    upsert_inspection_report(
        db, plm_id=report.plm_id, roll_number=roll.roll_no,
        module_field="shrinkage_report_id", module_row_id=roll.id,
    )
    db.commit()

    _attach_measurement_fields(roll)
    for k, v in service.compute_roll_fields(roll, None).items():
        setattr(roll, k, v)
    return roll
