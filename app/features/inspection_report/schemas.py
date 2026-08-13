from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel


class InspectionReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    roll_number: str
    color_name: Optional[str] = None
    gsm_report_id: Optional[UUID] = None
    moisture_report_id: Optional[UUID] = None
    skew_report_id: Optional[UUID] = None
    relaxation_report_id: Optional[UUID] = None
    factory_report_id: Optional[UUID] = None
    shade_check_report_id: Optional[UUID] = None
    correlation_report_id: Optional[UUID] = None
    shrinkage_report_id: Optional[UUID] = None
    color_continuity_card_id: Optional[UUID] = None
    fabric_inspection_roll_id: Optional[UUID] = None
    submitted_at: Optional[datetime] = None
    class Config: from_attributes = True
