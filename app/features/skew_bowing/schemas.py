from datetime import date
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel

from app.features.attachments.schemas import AttachmentOut


class RollIn(BaseModel):
    roll_no: str
    colour_code: Optional[str] = None
    ac_value_before: Optional[float] = None
    bd_value_before: Optional[float] = None
    ac_value_after: Optional[float] = None
    bd_value_after: Optional[float] = None
    reference_fabric_length: Optional[float] = None
    before_wash_date: Optional[date] = None
    after_wash_date: Optional[date] = None
    roll_status: str = "before_wash_done"


class RollOut(RollIn):
    id: UUID
    attachments: List[AttachmentOut] = []
    skew_percent_before: Optional[float] = None
    skew_percent_after: Optional[float] = None
    is_pass_before: Optional[bool] = None
    is_pass_after: Optional[bool] = None
    class Config: from_attributes = True


class ReportCreate(BaseModel):
    plm_id: UUID
    report_date: Optional[date] = None
    receive_qty: Optional[float] = None
    total_roll: Optional[int] = None
    fabric_construction: Optional[str] = None
    in_house_date: Optional[date] = None
    fabric_width_standard_id: Optional[UUID] = None
    is_stripe: bool = False
    comments_action_plan: Optional[str] = None


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    report_date: Optional[date] = None
    receive_qty: Optional[float] = None
    total_roll: Optional[int] = None
    fabric_construction: Optional[str] = None
    in_house_date: Optional[date] = None
    is_stripe: bool
    comments_action_plan: Optional[str] = None
    standard_point_snapshot: Optional[float] = None
    fabric_width_label_snapshot: Optional[str] = None
    measurement_unit_snapshot: str
    quantity_unit_snapshot: str
    qc_setup_version_snapshot: int = 1
    rolls: List[RollOut] = []
    attachments: List[AttachmentOut] = []  # report-level fabric photo
    before_wash_attachments: List[AttachmentOut] = []
    after_wash_attachments: List[AttachmentOut] = []

    avg_percent_before: Optional[float] = None
    avg_percent_after: Optional[float] = None
    pass_count_before: int = 0
    fail_count_before: int = 0
    pass_count_after: int = 0
    fail_count_after: int = 0
    is_pass_before: Optional[bool] = None
    is_pass_after: Optional[bool] = None

    class Config: from_attributes = True
