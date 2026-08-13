from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles
from app.features.plm.models import Plm
from app.features.gsm import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report
from app.features.attachments.service import get_attachments
from app.features.store.service import require_qc_float

router = APIRouter(prefix="/gsm", tags=["GSM Check"])


def _attach_photos(db, roll):
    roll.attachments = get_attachments(db, "gsm_roll", roll.id); return roll

def _attach_stats(report):
    stats = service.compute_report_stats(report.rolls, float(report.standard_gsm) if report.standard_gsm else 0, float(report.tolerance_snapshot) if report.tolerance_snapshot is not None else None)
    for k,v in stats.items(): setattr(report,k,v)
    return report

@router.post("", response_model=schemas.ReportOut)
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    plm=db.query(Plm).get(payload.plm_id)
    if not plm: raise HTTPException(status_code=404, detail="PLM not found")
    existing=db.query(models.GsmCheckReport).options(joinedload(models.GsmCheckReport.rolls)).filter(models.GsmCheckReport.plm_id==plm.id).first()
    if existing: return _attach_stats(existing)
    standard_gsm,_=require_qc_float(db,plm,"gsm","standard_gsm")
    tolerance,tolerance_id=require_qc_float(db,plm,"gsm","tolerance_percent",legacy_standard_module="gsm")
    report=models.GsmCheckReport(plm_id=plm.id,report_date=payload.report_date,color=payload.color,standard_gsm=standard_gsm,tolerance_standard_id=tolerance_id,tolerance_snapshot=tolerance)
    db.add(report); db.commit(); db.refresh(report); return _attach_stats(report)

@router.get("", response_model=List[schemas.ReportOut])
def list_reports(plm_id: Optional[UUID]=None, db: Session=Depends(get_db)):
    q=db.query(models.GsmCheckReport).options(joinedload(models.GsmCheckReport.rolls))
    if plm_id:q=q.filter(models.GsmCheckReport.plm_id==plm_id)
    reports=q.order_by(models.GsmCheckReport.created_at.desc()).all()
    for r in reports:
        for roll in r.rolls:_attach_photos(db,roll)
        _attach_stats(r)
    return reports

@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id:UUID,db:Session=Depends(get_db)):
    r=db.query(models.GsmCheckReport).options(joinedload(models.GsmCheckReport.rolls)).get(report_id)
    if not r:raise HTTPException(status_code=404,detail="Report not found")
    for roll in r.rolls:_attach_photos(db,roll)
    return _attach_stats(r)

@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
def upsert_roll(report_id:UUID,payload:schemas.RollIn,db:Session=Depends(get_db),_=Depends(require_roles("inspection","worker"))):
    report=db.query(models.GsmCheckReport).get(report_id)
    if not report:raise HTTPException(status_code=404,detail="Report not found")
    roll=db.query(models.GsmCheckRoll).filter(models.GsmCheckRoll.report_id==report_id,models.GsmCheckRoll.roll_no==payload.roll_no).first()
    if roll:
        for k,v in payload.model_dump().items():setattr(roll,k,v)
    else:
        roll=models.GsmCheckRoll(report_id=report_id,**payload.model_dump());db.add(roll)
    db.commit();db.refresh(roll)
    upsert_inspection_report(db,plm_id=report.plm_id,roll_number=roll.roll_no,color_name=roll.color,module_field="gsm_report_id",module_row_id=roll.id);db.commit()
    return _attach_photos(db,roll)
