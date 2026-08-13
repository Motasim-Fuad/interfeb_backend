from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.features.inspection_report import models, schemas

router = APIRouter(prefix="/inspection-reports", tags=["Inspection Report (per-roll master)"])


@router.get("", response_model=List[schemas.InspectionReportOut])
def list_inspection_reports(plm_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    q = db.query(models.InspectionReport)
    if plm_id:
        q = q.filter(models.InspectionReport.plm_id == plm_id)
    return q.order_by(models.InspectionReport.roll_number).all()
