from datetime import date
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel

from app.features.attachments.schemas import AttachmentOut


class RollIn(BaseModel):
    roll_no: str
    color_code: Optional[str] = None
    fabric_yds_mtr: Optional[float] = None
    roll_date: Optional[date] = None
    before_relax_start_time: Optional[str] = None
    before_relax_length: Optional[float] = None
    before_relax_width: Optional[float] = None
    after_relax_time: Optional[str] = None
    after_relax_length: Optional[float] = None
    after_relax_width: Optional[float] = None
    relaxation_hours: Optional[int] = None
    remark: Optional[str] = None


class RollOut(RollIn):
    id: UUID
    attachments: List[AttachmentOut] = []
    shrinkage_length_pct: Optional[float] = None
    shrinkage_width_pct: Optional[float] = None
    class Config: from_attributes = True


class ReportCreate(BaseModel):
    plm_id: UUID
    color: Optional[str] = None
    comments: Optional[str] = None


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    color: Optional[str] = None
    relaxation_hours_snapshot: Optional[int] = None
    comments: Optional[str] = None
    rolls: List[RollOut] = []
    attachments: List[AttachmentOut] = []
    avg_shrinkage_length: Optional[float] = None
    avg_shrinkage_width: Optional[float] = None
    class Config: from_attributes = True
