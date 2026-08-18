from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles
from app.features.moisture import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report
from app.features.plm.models import Plm
from app.features.store.service import require_qc_float
from app.features.attachments.service import attach_stage_photos
from app.core.roll_payload import merge_roll_fields, require_before_wash_first

router = APIRouter(prefix="/moisture", tags=["Moisture Check"])


def _attach_stats(report):
    for k, v in service.compute_report_stats(
        report.rolls,
        float(report.acceptance_snapshot) if report.acceptance_snapshot is not None else None,
    ).items():
        setattr(report, k, v)
    return report


def _hydrate(db, report):
    attach_stage_photos(db, report, before_type="moisture_before_wash", after_type="moisture_after_wash")
    return _attach_stats(report)


@router.post("", response_model=schemas.ReportOut)
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    existing = db.query(models.MoistureCheckReport).options(joinedload(models.MoistureCheckReport.rolls)).filter(models.MoistureCheckReport.plm_id == plm.id).first()
    if existing:
        return _hydrate(db, existing)

    acceptance, _ = require_qc_float(db, plm, "moisture", "acceptance_percentage")
    legacy_id = payload.fabric_composition_standard_id
    if acceptance is None and legacy_id:
        from app.features.admin_config.models import FabricCompositionStandard
        old = db.query(FabricCompositionStandard).get(legacy_id)
        acceptance = float(old.acceptance_percentage) if old else None

    report = models.MoistureCheckReport(
        plm_id=plm.id,
        report_date=payload.report_date,
        report_time=payload.report_time,
        vendor=plm.supplier.name if plm.supplier else payload.vendor,
        factory=payload.factory,
        fabric_composition_standard_id=legacy_id,
        acceptance_snapshot=acceptance,
    )
    db.add(report); db.commit(); db.refresh(report)
    return _hydrate(db, report)


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(plm_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    q = db.query(models.MoistureCheckReport).options(joinedload(models.MoistureCheckReport.rolls))
    if plm_id: q = q.filter(models.MoistureCheckReport.plm_id == plm_id)
    return [_hydrate(db, r) for r in q.order_by(models.MoistureCheckReport.created_at.desc()).all()]


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = db.query(models.MoistureCheckReport).options(joinedload(models.MoistureCheckReport.rolls)).get(report_id)
    if not report: raise HTTPException(status_code=404, detail="Report not found")
    return _hydrate(db, report)


@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
def upsert_roll(report_id: UUID, payload: schemas.RollIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    report = db.query(models.MoistureCheckReport).get(report_id)
    if not report: raise HTTPException(status_code=404, detail="Report not found")
    roll = db.query(models.MoistureCheckRoll).filter(models.MoistureCheckRoll.report_id == report_id, models.MoistureCheckRoll.roll_no == payload.roll_no).first()
    data = payload.model_dump()
    # Composition is planning data from the PLM/consignment, never re-entered by worker.
    plm = db.query(Plm).get(report.plm_id)
    data['fabric_composition'] = plm.fabric_composition if plm else data.get('fabric_composition')
    require_before_wash_first(roll, data, ("swatch_rating_before",), ("swatch_rating_after",))
    if roll:
        merge_roll_fields(roll, data)
    else:
        roll = models.MoistureCheckRoll(report_id=report_id, **{k: v for k, v in data.items() if v is not None or k == "roll_no"}); db.add(roll)
    db.commit(); db.refresh(roll)
    upsert_inspection_report(db, plm_id=report.plm_id, roll_number=roll.roll_no, color_name=roll.color, module_field="moisture_report_id", module_row_id=roll.id)
    db.commit(); return roll
