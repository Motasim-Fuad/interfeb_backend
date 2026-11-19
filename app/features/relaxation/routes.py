from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from app.core.sync import client_stamp, is_stale, mark_stale, retry_on_conflict
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.list_query import bound_unscoped
from app.core.deps import require_roles
from app.features.store.service import get_float_qc_value
from app.features.plm.models import Plm
from app.features.relaxation import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report
from app.features.attachments.service import attach_photos_bulk, attach_stage_photos, get_attachments

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


def _hydrate(db: Session, report: models.FabricRelaxationReport):
    attach_photos_bulk(db, "relaxation_roll", report.rolls)
    attach_stage_photos(db, report, report_type="relaxation_report")
    return _attach_roll_and_report_stats(report)


@router.post("", response_model=schemas.ReportOut)
@retry_on_conflict
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    existing = db.query(models.FabricRelaxationReport).options(joinedload(models.FabricRelaxationReport.rolls)).filter(models.FabricRelaxationReport.plm_id == plm.id).first()
    if existing:
        if payload.relaxation_hours:
            existing.relaxation_hours_snapshot = payload.relaxation_hours
            db.commit()
            db.refresh(existing)
        return _hydrate(db, existing)
    # Typed in the app; an optional supplier QC profile value is only a fallback. No fixed 12.
    relaxation_hours = payload.relaxation_hours or get_float_qc_value(plm, "relaxation", "relaxation_hours", None)
    report = models.FabricRelaxationReport(
        **payload.model_dump(exclude={"relaxation_hours"}),
        relaxation_hours_snapshot=int(relaxation_hours) if relaxation_hours else None,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _hydrate(db, report)


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(response: Response, plm_id: Optional[UUID] = None, limit: Optional[int] = None, offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(models.FabricRelaxationReport).options(joinedload(models.FabricRelaxationReport.rolls))
    if plm_id:
        q = q.filter(models.FabricRelaxationReport.plm_id == plm_id)
    reports = bound_unscoped(q.order_by(models.FabricRelaxationReport.created_at.desc()), bool(plm_id), response, limit, offset).all()
    for report in reports:
        _hydrate(db, report)
    return reports


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = db.query(models.FabricRelaxationReport).options(joinedload(models.FabricRelaxationReport.rolls)).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _hydrate(db, report)


@router.patch("/{report_id}", response_model=schemas.ReportOut)
def update_report(report_id: UUID, payload: schemas.ReportUpdate, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    report = db.query(models.FabricRelaxationReport).options(joinedload(models.FabricRelaxationReport.rolls)).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if payload.relaxation_hours is not None:
        if payload.relaxation_hours <= 0:
            raise HTTPException(status_code=422, detail="Relaxation time must be greater than 0 hours")
        report.relaxation_hours_snapshot = payload.relaxation_hours
    if payload.comments is not None:
        report.comments = payload.comments
    db.commit()
    db.refresh(report)
    return _hydrate(db, report)


@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
@retry_on_conflict
def add_roll(report_id: UUID, payload: schemas.RollIn, response: Response, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    report = db.query(models.FabricRelaxationReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    roll = (db.query(models.FabricRelaxationRoll)
            .filter(models.FabricRelaxationRoll.report_id == report_id, models.FabricRelaxationRoll.roll_no == payload.roll_no)
            .first())
    if is_stale(roll, payload.client_updated_at):
        mark_stale(response)
    else:
        data = payload.model_dump()
        data["client_updated_at"] = client_stamp(payload.client_updated_at)
        if payload.after_readings is not None:
            kept = [r for r in data["after_readings"] if r.get("length") is not None or r.get("width") is not None]
            data["after_readings"] = kept
            last = kept[-1] if kept else {}
            data.update(after_relax_time=last.get("time"), after_relax_length=last.get("length"), after_relax_width=last.get("width"))
        else:
            data.pop("after_readings")
            # Older app builds send one after reading; it replaces the latest stored one.
            if roll is not None and roll.after_readings and data.get("after_relax_length") is not None:
                roll.after_readings = roll.after_readings[:-1] + [{"time": data.get("after_relax_time"),
                                                                    "length": data.get("after_relax_length"),
                                                                    "width": data.get("after_relax_width")}]
        if roll:
            for k, v in data.items():
                setattr(roll, k, v)
        else:
            roll = models.FabricRelaxationRoll(report_id=report_id, **data)
            db.add(roll)
        db.flush()
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
