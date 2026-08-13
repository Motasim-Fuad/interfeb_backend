from datetime import date
from typing import Optional
from uuid import UUID
from pydantic import BaseModel


class FactoryReportCreate(BaseModel):
    plm_id: UUID
    supplier: Optional[str] = None
    job_no: Optional[str] = None
    construction: Optional[str] = None
    color: Optional[str] = None
    order_qty: Optional[float] = None
    inspection_yardage: Optional[float] = None
    inspection_pieces: Optional[int] = None
    actual_width: Optional[float] = None
    total_point: Optional[float] = None
    avg_shipment_point: Optional[float] = None
    inspection_date: Optional[date] = None
    inspection_percentage: Optional[float] = None
    inspection_stage: Optional[str] = None


class FactoryReportOut(FactoryReportCreate):
    id: UUID
    standard_snapshot: Optional[float] = None
    is_pass: Optional[bool] = None
    class Config: from_attributes = True
