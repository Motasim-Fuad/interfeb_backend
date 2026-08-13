from datetime import date
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel


class DefectEntryIn(BaseModel):
    defect_type_id: UUID
    range_config_id: UUID
    count: int


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
    defects: List[DefectEntryOut] = []
    class Config: from_attributes = True


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
