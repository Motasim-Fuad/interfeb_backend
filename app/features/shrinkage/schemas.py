from datetime import date, datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel


class MeasurementIn(BaseModel):
    reading_order: int
    before_length: Optional[float] = None
    before_width: Optional[float] = None
    after_length: Optional[float] = None
    after_width: Optional[float] = None


class MeasurementOut(MeasurementIn):
    id: UUID
    shrink_length: Optional[float] = None
    shrink_width: Optional[float] = None
    adjust_length_pct: Optional[float] = None
    adjust_width_pct: Optional[float] = None
    class Config: from_attributes = True


class RollIn(BaseModel):
    roll_no: str
    client_updated_at: Optional[datetime] = None
    yds: Optional[float] = None
    pattern: Optional[str] = None
    shade: Optional[str] = None
    before_wash_date: Optional[date] = None
    after_wash_date: Optional[date] = None
    roll_status: str = "before_wash_done"
    measurements: List[MeasurementIn] = []


class RollOut(BaseModel):
    id: UUID
    roll_no: str
    yds: Optional[float] = None
    pattern: Optional[str] = None
    shade: Optional[str] = None
    roll_status: str
    measurements: List[MeasurementOut] = []
    avg_length_pct: Optional[float] = None
    avg_width_pct: Optional[float] = None
    class Config: from_attributes = True


class ReportCreate(BaseModel):
    plm_id: UUID
    season: Optional[str] = None
    color: Optional[str] = None
    qc_name: Optional[str] = None
    report_date: Optional[date] = None


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    season: Optional[str] = None
    rolls: List[RollOut] = []
    avg_shrinkage_length: Optional[float] = None
    avg_shrinkage_width: Optional[float] = None
    total_yds: float = 0
    class Config: from_attributes = True
