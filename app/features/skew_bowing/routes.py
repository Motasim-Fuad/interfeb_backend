from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles
from app.features.skew_bowing import models, schemas, service
from app.features.inspection_report.service import upsert_inspection_report
from app.features.attachments.service import get_attachments
from app.features.plm.models import Plm
from app.features.store.service import require_qc_float, get_qc_value

router=APIRouter(prefix="/skew-bowing",tags=["Skew & Bowing"])

def _attach(db,report):
    report.attachments=get_attachments(db,"skew_bowing_report",report.id)
    std=float(report.standard_point_snapshot) if report.standard_point_snapshot is not None else None
    for roll in report.rolls:
        roll.attachments=get_attachments(db,"skew_bowing_roll",roll.id)
        for k,v in service.compute_roll_fields(roll,std,report.measurement_unit_snapshot).items():setattr(roll,k,v)
    for k,v in service.compute_report_stats(report.rolls,std,report.measurement_unit_snapshot).items():setattr(report,k,v)
    return report

@router.post("",response_model=schemas.ReportOut)
def create_report(payload:schemas.ReportCreate,db:Session=Depends(get_db),_=Depends(require_roles("inspection"))):
    plm=db.query(Plm).get(payload.plm_id)
    if not plm:raise HTTPException(status_code=404,detail="PLM not found")
    existing=db.query(models.SkewBowingReport).options(joinedload(models.SkewBowingReport.rolls)).filter(models.SkewBowingReport.plm_id==plm.id).first()
    if existing:return _attach(db,existing)
    std,_=require_qc_float(db,plm,"skew_bowing","standard_point")
    is_stripe=bool(get_qc_value(plm,"skew_bowing","is_stripe",payload.is_stripe))
    legacy_width_id=None
    total_roll=sum(len(c.rolls) for c in plm.colors)
    report=models.SkewBowingReport(
        plm_id=plm.id,report_date=payload.report_date,
        receive_qty=plm.allocated_quantity or plm.received_quantity or payload.receive_qty,
        total_roll=total_roll,fabric_construction=plm.fabric_composition,
        in_house_date=plm.received_at or payload.in_house_date,
        fabric_width_standard_id=legacy_width_id,is_stripe=is_stripe,
        standard_point_snapshot=std,
        fabric_width_label_snapshot=str(get_qc_value(plm,"skew_bowing","width_label",get_qc_value(plm,"skew_bowing","fabric_width_label", "")) or ""),
        measurement_unit_snapshot=plm.measurement_unit.value if hasattr(plm.measurement_unit,"value") else str(plm.measurement_unit),
        quantity_unit_snapshot=plm.quantity_unit,
        qc_setup_version_snapshot=plm.qc_setup_version or 1,
        comments_action_plan=payload.comments_action_plan,
    )
    db.add(report);db.commit();db.refresh(report);return _attach(db,report)

@router.get("",response_model=List[schemas.ReportOut])
def list_reports(plm_id:Optional[UUID]=None,db:Session=Depends(get_db)):
    q=db.query(models.SkewBowingReport).options(joinedload(models.SkewBowingReport.rolls))
    if plm_id:q=q.filter(models.SkewBowingReport.plm_id==plm_id)
    return [_attach(db,r) for r in q.order_by(models.SkewBowingReport.created_at.desc()).all()]

@router.get("/{report_id}",response_model=schemas.ReportOut)
def get_report(report_id:UUID,db:Session=Depends(get_db)):
    r=db.query(models.SkewBowingReport).options(joinedload(models.SkewBowingReport.rolls)).get(report_id)
    if not r:raise HTTPException(status_code=404,detail="Report not found")
    return _attach(db,r)

@router.post("/{report_id}/rolls",response_model=schemas.RollOut)
def upsert_roll(report_id:UUID,payload:schemas.RollIn,db:Session=Depends(get_db),_=Depends(require_roles("inspection","worker"))):
    report=db.query(models.SkewBowingReport).get(report_id)
    if not report:raise HTTPException(status_code=404,detail="Report not found")
    plm=db.query(Plm).get(report.plm_id)
    if not plm:raise HTTPException(status_code=404,detail="PLM not found")
    data=payload.model_dump()
    # Percentage is derived only from AC/BD; no hidden reference length is needed.
    data["reference_fabric_length"]=None
    roll=db.query(models.SkewBowingRoll).filter(models.SkewBowingRoll.report_id==report_id,models.SkewBowingRoll.roll_no==payload.roll_no).first()
    if roll:
        for k,v in data.items():setattr(roll,k,v)
    else:
        roll=models.SkewBowingRoll(report_id=report_id,**data);db.add(roll)
    db.commit();db.refresh(roll)
    upsert_inspection_report(db,plm_id=report.plm_id,roll_number=roll.roll_no,color_name=roll.colour_code,module_field="skew_report_id",module_row_id=roll.id);db.commit()
    roll.attachments=get_attachments(db,"skew_bowing_roll",roll.id)
    std=float(report.standard_point_snapshot) if report.standard_point_snapshot is not None else None
    for k,v in service.compute_roll_fields(roll,std,report.measurement_unit_snapshot).items():setattr(roll,k,v)
    return roll
