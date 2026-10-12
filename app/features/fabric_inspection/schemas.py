from datetime import date, datetime
from typing import List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field

Disposition = Literal["pending", "accepted", "hold", "rejected", "claim"]
DefectSide = Literal["left", "center", "right"]


class DefectEntryIn(BaseModel):
    defect_type_id: UUID
    range_config_id: UUID
    count: int
    position_yard: Optional[float] = Field(default=None, ge=0)
    side: Optional[DefectSide] = None
    photo_url: Optional[str] = None
    recorded_at: Optional[datetime] = None


class DefectEntryOut(DefectEntryIn):
    id: UUID
    points_snapshot: int
    defect_type_name: Optional[str] = None
    range_label: Optional[str] = None
    class Config: from_attributes = True


class RollIn(BaseModel):
    roll_no: str
    colour_code: Optional[str] = None
    width_marked: Optional[float] = None
    length_marked: Optional[float] = None
    width_actual: Optional[float] = None
    length_actual: Optional[float] = None
    remarks: Optional[str] = None
    defects: List[DefectEntryIn] = []
    inspector_name: Optional[str] = None
    inspected_at: Optional[datetime] = None
    client_updated_at: Optional[datetime] = None


class RollOut(BaseModel):
    id: UUID
    roll_no: str
    colour_code: Optional[str] = None
    width_marked: Optional[float] = None
    length_marked: Optional[float] = None
    width_actual: Optional[float] = None
    length_actual: Optional[float] = None
    remarks: Optional[str] = None
    total_points: int
    percent_points: Optional[float] = None
    is_pass: Optional[bool] = None
    disposition: str = "pending"
    disposition_note: Optional[str] = None
    shade_band: Optional[str] = None
    inspector_name: Optional[str] = None
    inspected_at: Optional[datetime] = None
    client_updated_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    defects: List[DefectEntryOut] = []
    class Config: from_attributes = True


class DispositionUpdate(BaseModel):
    disposition: Disposition
    disposition_note: Optional[str] = None
    shade_band: Optional[str] = Field(default=None, max_length=20)


class RollListItem(BaseModel):
    id: UUID
    report_id: UUID
    plm_id: UUID
    plm_code: str
    supplier_id: Optional[UUID] = None
    supplier_name: Optional[str] = None
    buyer_id: Optional[UUID] = None
    buyer_name: Optional[str] = None
    roll_no: str
    colour_code: Optional[str] = None
    total_points: int = 0
    percent_points: Optional[float] = None
    is_pass: Optional[bool] = None
    disposition: str = "pending"
    disposition_note: Optional[str] = None
    shade_band: Optional[str] = None
    inspector_name: Optional[str] = None
    inspected_at: Optional[datetime] = None


class PreviewIn(BaseModel):
    plm_id: UUID
    width_actual: Optional[float] = None
    length_actual: Optional[float] = None
    total_points: int = Field(default=0, ge=0)


class PreviewOut(BaseModel):
    percent_points: Optional[float] = None
    is_pass: Optional[bool] = None
    individual_standard: Optional[float] = None


class ReportCreate(BaseModel):
    plm_id: UUID
    color: Optional[str] = None
    fabric_type: Optional[str] = None
    fabric_composition: Optional[str] = None
    received_qty: Optional[float] = None
    checked_qty: Optional[float] = None
    fabric_in_house_date: Optional[date] = None
    inspection_date: Optional[date] = None
    consignment: Optional[str] = None
    comments: Optional[str] = None


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    color: Optional[str] = None
    fabric_type: Optional[str] = None
    fabric_composition: Optional[str] = None
    received_qty: Optional[float] = None
    checked_qty: Optional[float] = None
    individual_standard_snapshot: Optional[float] = None
    average_standard_snapshot: Optional[float] = None
    fabric_in_house_date: Optional[date] = None
    inspection_date: Optional[date] = None
    consignment: Optional[str] = None
    comments: Optional[str] = None
    measurement_unit_snapshot: str
    quantity_unit_snapshot: str
    qc_setup_version_snapshot: int = 1
    rolls: List[RollOut] = []
    total_defect_points: int = 0
    total_length_actual: float = 0
    avg_width_actual: Optional[float] = None
    overall_percent_points: Optional[float] = None
    pass_count: int = 0
    fail_count: int = 0
    is_pass: Optional[bool] = None
    class Config: from_attributes = True
