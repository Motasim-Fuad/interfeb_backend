from datetime import date, datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel

from app.features.attachments.schemas import AttachmentOut


class RollIn(BaseModel):
    roll_no: str
    client_updated_at: Optional[datetime] = None
    color: Optional[str] = None
    fabric_composition: Optional[str] = None
    swatch_rating_before: Optional[float] = None
    swatch_rating_after: Optional[float] = None
    before_wash_date: Optional[date] = None
    after_wash_date: Optional[date] = None
    roll_status: str = "before_wash_done"


class RollOut(RollIn):
    id: UUID
    class Config: from_attributes = True


class ReportCreate(BaseModel):
    plm_id: UUID
    report_date: Optional[date] = None
    report_time: Optional[str] = None
    vendor: Optional[str] = None
    factory: Optional[str] = None
    fabric_composition_standard_id: Optional[UUID] = None


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    report_date: Optional[date] = None
    report_time: Optional[str] = None
    vendor: Optional[str] = None
    factory: Optional[str] = None
    fabric_composition_standard_id: Optional[UUID] = None
    acceptance_snapshot: Optional[float] = None
    rolls: List[RollOut] = []
    avg_swatch: Optional[float] = None
    pass_count: int = 0
    fail_count: int = 0
    before_wash_attachments: List[AttachmentOut] = []
    after_wash_attachments: List[AttachmentOut] = []
    class Config: from_attributes = True
