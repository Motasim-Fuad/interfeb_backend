from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class PlmRollIn(BaseModel):
    roll_no: str
    marked_width: Optional[float] = None
    marked_length: Optional[float] = None
    planned_quantity: Optional[float] = None
    supplier_total_point: Optional[float] = None


class PlmRollOut(PlmRollIn):
    id: UUID
    class Config: from_attributes = True


class PlmRollUpdate(BaseModel):
    marked_width: Optional[float] = None
    marked_length: Optional[float] = None
    planned_quantity: Optional[float] = None
    supplier_total_point: Optional[float] = None


class PlmRollBulkCreate(BaseModel):
    roll_numbers: List[str] = Field(default_factory=list)
    start_roll: Optional[int] = None
    end_roll: Optional[int] = None
    prefix: str = ""
    marked_width: Optional[float] = None
    marked_length: Optional[float] = None
    planned_quantity: Optional[float] = None
    supplier_total_point: Optional[float] = None

    @model_validator(mode="after")
    def validate_source(self):
        if not self.roll_numbers and (self.start_roll is None or self.end_roll is None):
            raise ValueError("Provide roll_numbers or start_roll + end_roll")
        if self.start_roll is not None and self.end_roll is not None and self.end_roll < self.start_roll:
            raise ValueError("end_roll must be >= start_roll")
        return self


class PlmColorIn(BaseModel):
    colour_code: str
    planned_quantity: Optional[float] = None
    rolls: List[PlmRollIn] = Field(default_factory=list)


class PlmColorOut(BaseModel):
    id: UUID
    colour_code: str
    planned_quantity: Optional[float] = None
    rolls: List[PlmRollOut] = Field(default_factory=list)
    class Config: from_attributes = True


class PlmCreate(BaseModel):
    plm_code: str
    plm_date: date
    fabric_receive_id: UUID
    material_id: UUID
    plm_style: Optional[str] = None
    allocated_quantity: Optional[float] = None
    colors: List[PlmColorIn] = Field(default_factory=list)


class PlmUpdate(BaseModel):
    plm_date: Optional[date] = None
    plm_style: Optional[str] = None
    allocated_quantity: Optional[float] = None


class PlmOut(BaseModel):
    id: UUID
    plm_code: str
    plm_date: date
    buyer_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    buyer_name: Optional[str] = None
    supplier_name: Optional[str] = None
    fabric_receive_id: Optional[UUID] = None
    material_id: Optional[UUID] = None
    material_display_name: Optional[str] = None
    material_snapshot: Dict[str, Any] = Field(default_factory=dict)
    plm_style: Optional[str] = None
    fabric_type: Optional[str] = None
    fabric_composition: Optional[str] = None
    consignment: Optional[str] = None
    measurement_unit: str
    allocated_quantity: Optional[float] = None
    received_quantity: Optional[float] = None
    quantity_unit: str = "yds"
    received_at: Optional[date] = None
    qc_config: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    qc_setup_version: int = 1
    colors: List[PlmColorOut] = Field(default_factory=list)
    created_at: datetime
    class Config: from_attributes = True


class InspectionScheduleIn(BaseModel):
    plm_id: UUID
    scheduled_date: date
    status: str = "pending"
    assigned_to: Optional[str] = None
    machine_note: Optional[str] = None


class InspectionScheduleOut(InspectionScheduleIn):
    id: UUID
    plm_code: Optional[str] = None
    buyer_name: Optional[str] = None
    supplier_name: Optional[str] = None
    class Config: from_attributes = True


class ScheduleStatusUpdate(BaseModel):
    status: str


class ScheduleRescheduleUpdate(BaseModel):
    scheduled_date: date
