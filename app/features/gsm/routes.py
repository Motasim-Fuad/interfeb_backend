from datetime import date
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from app.core.sync import client_stamp, is_stale, mark_stale, retry_on_conflict
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.list_query import bound_unscoped
from app.core.deps import require_roles
from app.features.plm.models import Plm
from app.features.gsm import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report
from app.features.attachments.service import attach_photos_bulk, attach_stage_photos, get_attachments
from app.core.roll_payload import merge_roll_fields, require_before_wash_first, stamp_wash_dates
from app.core.clock import factory_today

router = APIRouter(prefix="/gsm", tags=["GSM Check"])


def _attach_photos(db, roll):
    roll.attachments = get_attachments(db, "gsm_roll", roll.id); return roll

def _hydrate(db, report):
    # Required GSM and tolerance % are both entered in the app (stored on the report).
    attach_photos_bulk(db, "gsm_roll", report.rolls)
    attach_stage_photos(db, report, before_type="gsm_before_wash", after_type="gsm_after_wash")
    return _attach_stats(report)

def _attach_stats(report):
    stats = service.compute_report_stats(report.rolls, float(report.standard_gsm) if report.standard_gsm else 0, float(report.tolerance_snapshot) if report.tolerance_snapshot is not None else None)
    for k,v in stats.items(): setattr(report,k,v)
    return report

@router.post("", response_model=schemas.ReportOut)
@retry_on_conflict
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    plm=db.query(Plm).get(payload.plm_id)
    if not plm: raise HTTPException(status_code=404, detail="PLM not found")
    existing=db.query(models.GsmCheckReport).options(joinedload(models.GsmCheckReport.rolls)).filter(models.GsmCheckReport.plm_id==plm.id).first()
    if existing:
        if payload.tolerance_percent is not None:
            existing.tolerance_snapshot=payload.tolerance_percent
        if payload.standard_gsm is not None:
            existing.standard_gsm=payload.standard_gsm
        db.commit(); db.refresh(existing)
        return _hydrate(db, existing)
    standard_gsm=payload.standard_gsm
    tolerance,tolerance_id=payload.tolerance_percent,None
    color = payload.color or ', '.join(c.colour_code for c in (plm.colors or []) if c.colour_code) or None
    report=models.GsmCheckReport(plm_id=plm.id,report_date=payload.report_date or factory_today(),color=color,standard_gsm=standard_gsm,tolerance_standard_id=tolerance_id,tolerance_snapshot=tolerance)
    db.add(report); db.commit(); db.refresh(report); return _hydrate(db, report)

@router.get("", response_model=List[schemas.ReportOut])
def list_reports(response: Response, plm_id: Optional[UUID] = None, limit: Optional[int] = None, offset: int = 0, db: Session = Depends(get_db)):
    q=db.query(models.GsmCheckReport).options(joinedload(models.GsmCheckReport.rolls))
    if plm_id:q=q.filter(models.GsmCheckReport.plm_id==plm_id)
    reports=bound_unscoped(q.order_by(models.GsmCheckReport.created_at.desc()),bool(plm_id),response,limit,offset).all()
    for r in reports:
        _hydrate(db, r)
    return reports

@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id:UUID,db:Session=Depends(get_db)):
    r=db.query(models.GsmCheckReport).options(joinedload(models.GsmCheckReport.rolls)).get(report_id)
    if not r:raise HTTPException(status_code=404,detail="Report not found")
    return _hydrate(db, r)

@router.patch("/{report_id}", response_model=schemas.ReportOut)
def update_report(report_id:UUID,payload:schemas.ReportUpdate,db:Session=Depends(get_db),_=Depends(require_roles("inspection","worker"))):
    r=db.query(models.GsmCheckReport).options(joinedload(models.GsmCheckReport.rolls)).get(report_id)
    if not r:raise HTTPException(status_code=404,detail="Report not found")
    if payload.tolerance_percent is not None:
        if payload.tolerance_percent<0:raise HTTPException(status_code=422,detail="Tolerance cannot be negative")
        r.tolerance_snapshot=payload.tolerance_percent
    if payload.standard_gsm is not None:
        if payload.standard_gsm<=0:raise HTTPException(status_code=422,detail="Required GSM must be greater than 0")
        r.standard_gsm=payload.standard_gsm
    db.commit();db.refresh(r)
    return _hydrate(db, r)

@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
@retry_on_conflict
def upsert_roll(report_id:UUID,payload:schemas.RollIn,response:Response,db:Session=Depends(get_db),_=Depends(require_roles("inspection","worker"))):
    report=db.query(models.GsmCheckReport).get(report_id)
    if not report:raise HTTPException(status_code=404,detail="Report not found")
    data=payload.model_dump()
    data["client_updated_at"]=client_stamp(payload.client_updated_at)
    roll=db.query(models.GsmCheckRoll).filter(models.GsmCheckRoll.report_id==report_id,models.GsmCheckRoll.roll_no==payload.roll_no).first()
    if is_stale(roll,payload.client_updated_at):
        mark_stale(response)
        return _attach_photos(db,roll)
    require_before_wash_first(roll, data, ("actual_gsm_before",), ("actual_gsm_after",))
    stamp_wash_dates(roll, data, ("actual_gsm_before",), ("actual_gsm_after",))
    if roll:
        merge_roll_fields(roll, data)
    else:
        roll=models.GsmCheckRoll(report_id=report_id,**{k:v for k,v in data.items() if v is not None or k=="roll_no"});db.add(roll)
    db.flush();db.refresh(roll)
    upsert_inspection_report(db,plm_id=report.plm_id,roll_number=roll.roll_no,color_name=roll.color,module_field="gsm_report_id",module_row_id=roll.id);db.commit()
    return _attach_photos(db,roll)
