from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SupplierRankItem(BaseModel):
    supplier_id: UUID
    supplier_name: str
    total_count: int
    total_points: int


class PlmRankItem(BaseModel):
    plm_id: UUID
    plm_code: str
    total_count: int
    total_points: int


class DefectRankItem(BaseModel):
    defect_type_id: UUID
    defect_type_name: str
    total_count: int
    total_points: int


class PassFailSummary(BaseModel):
    module: str
    total_rolls: int
    passed: int
    failed: int
    pass_rate_percent: Optional[float] = None


class InspectionRollSummary(BaseModel):
    inspection_report_id: UUID
    submitted_at: Optional[datetime] = None
    buyer_id: Optional[UUID] = None
    buyer_name: str = ''
    supplier_id: Optional[UUID] = None
    supplier_name: str = ''
    fabric_receive_id: Optional[UUID] = None
    consignment: str = ''
    plm_id: UUID
    plm_code: str
    plm_style: Optional[str] = None
    color_name: Optional[str] = None
    roll_number: str
    measurement_unit: str = 'inch'
    gsm: bool = False
    moisture: bool = False
    skew_bowing: bool = False
    relaxation: bool = False
    shade_check: bool = False
    shrinkage: bool = False
    correlation: bool = False
    color_continuity: bool = False
    fabric_inspection: bool = False
    factory_report: bool = False
    completed_modules: int = 0
    total_modules: int = 10


class InspectionRollPage(BaseModel):
    total: int
    completed_rolls: int
    in_progress_rolls: int
    rows: list[InspectionRollSummary]


class SupplierMonthlyInputIn(BaseModel):
    supplier_id: UUID
    year: int
    month: int = Field(ge=1, le=12)
    communication_rating: Optional[float] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = None


class SupplierMonthlyInputOut(SupplierMonthlyInputIn):
    id: UUID
    supplier_name: str = ""
    created_at: datetime
    class Config:
        from_attributes = True

