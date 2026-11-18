from datetime import date, datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel

from app.features.attachments.schemas import AttachmentOut


class RollIn(BaseModel):
    roll_no: str
    client_updated_at: Optional[datetime] = None
    color: Optional[str] = None
    actual_gsm_before: Optional[float] = None
    actual_gsm_after: Optional[float] = None
    weight: Optional[float] = None
    remarks_before: Optional[str] = None
    remarks_after: Optional[str] = None
    before_wash_date: Optional[date] = None
    after_wash_date: Optional[date] = None
    roll_status: str = "before_wash_done"


class RollOut(RollIn):
    id: UUID
    attachments: List[AttachmentOut] = []
    class Config: from_attributes = True


class ReportCreate(BaseModel):
    plm_id: UUID
    report_date: Optional[date] = None
    color: Optional[str] = None
    standard_gsm: Optional[float] = None  # Required GSM entered in the app
    # Accepted GSM tolerance (%) entered in the app.
    tolerance_percent: Optional[float] = None


class ReportUpdate(BaseModel):
    standard_gsm: Optional[float] = None
    tolerance_percent: Optional[float] = None


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    report_date: Optional[date] = None
    color: Optional[str] = None
    standard_gsm: Optional[float] = None
    tolerance_snapshot: Optional[float] = None
    rolls: List[RollOut] = []

    # computed server-side (replaces Flutter's old hardcoded 5% tolerance logic)
    avg_gsm_before: Optional[float] = None
    avg_gsm_after: Optional[float] = None
    min_gsm_before: Optional[float] = None
    max_gsm_before: Optional[float] = None
    min_gsm_after: Optional[float] = None
    max_gsm_after: Optional[float] = None
    avg_over_less_before_pct: Optional[float] = None
    avg_over_less_after_pct: Optional[float] = None
    pass_before: Optional[bool] = None
    pass_after: Optional[bool] = None
    before_wash_attachments: List[AttachmentOut] = []
    after_wash_attachments: List[AttachmentOut] = []

    class Config: from_attributes = True
