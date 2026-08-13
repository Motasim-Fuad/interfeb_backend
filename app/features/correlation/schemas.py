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
    defect_type_name_snapshot: Optional[str] = None
    range_label_snapshot: Optional[str] = None
    class Config: from_attributes = True


class RollIn(BaseModel):
    roll_no: str
    color_code: Optional[str] = None
    length_yards: Optional[float] = None
    width: Optional[float] = None
    standard: Optional[float] = None
    scs: Optional[int] = None
    mill_total_point: Optional[float] = None
    roll_date: Optional[date] = None
    defects: List[DefectEntryIn] = []


class RollOut(BaseModel):
    id: UUID
    roll_no: str
    color_code: Optional[str] = None
    mill_total_point: Optional[float] = None
    factory_total_points: int
    length_yards: Optional[float] = None
    width: Optional[float] = None
    standard: Optional[float] = None
    scs: Optional[int] = None
    factory_percent_points: Optional[float] = None
    factory_length: Optional[float] = None
    factory_width: Optional[float] = None
    mill_average_point: Optional[float] = None
    factory_average_point: Optional[float] = None
    difference: Optional[float] = None
    mill_pass: Optional[bool] = None
    factory_pass: Optional[bool] = None
    measurement_unit: str
    quantity_unit: str
    defects: List[DefectEntryOut] = []
    class Config: from_attributes = True


class ReportCreate(BaseModel):
    plm_id: UUID


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    standard_snapshot: Optional[float] = None
    scs_snapshot: Optional[int] = None
    measurement_unit_snapshot: str
    quantity_unit_snapshot: str
    qc_setup_version_snapshot: int = 1
    rolls: List[RollOut] = []
    class Config: from_attributes = True
