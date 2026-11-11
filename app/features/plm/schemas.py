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


class RollImportRow(BaseModel):
    line: int
    color: str = Field(default="", max_length=120)
    roll_no: str = Field(default="", max_length=60)
    marked_width: Optional[float] = None
    marked_length: Optional[float] = None
    planned_quantity: Optional[float] = None
    supplier_total_point: Optional[float] = None


class RollImportIn(BaseModel):
    rows: List[RollImportRow] = Field(min_length=1, max_length=5000)
    dry_run: bool = True


class RollImportLine(BaseModel):
    line: int
    color: str
    roll_no: str
    action: str  # new | update | unchanged | error
    message: Optional[str] = None


class RollImportResult(BaseModel):
    applied: bool
    new_colors: List[str]
    new_rolls: int
    updated_rolls: int
    unchanged_rolls: int
    errors: int
    lines: List[RollImportLine]


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
    # Only filled on lite lists, where colors/rolls are not loaded.
    roll_count: Optional[int] = None
    created_at: datetime
    class Config: from_attributes = True


class InspectionScheduleIn(BaseModel):
    plm_id: UUID
    scheduled_date: date
    assigned_to: Optional[str] = Field(default=None, max_length=120)
    machine_note: Optional[str] = Field(default=None, max_length=300)


class InspectionScheduleOut(InspectionScheduleIn):
    id: UUID
    status: str
    plm_code: Optional[str] = None
    buyer_name: Optional[str] = None
    supplier_name: Optional[str] = None
    # Planned rolls under the PLM vs. rolls that already have a 4-point entry.
    total_rolls: int = 0
    inspected_rolls: int = 0
    class Config: from_attributes = True


class ScheduleAssignUpdate(BaseModel):
    assigned_to: Optional[str] = Field(default=None, max_length=120)
    machine_note: Optional[str] = Field(default=None, max_length=300)


class ScheduleAssignee(BaseModel):
    label: str
    role: str


class ScheduleStatusUpdate(BaseModel):
    status: str


class ScheduleRescheduleUpdate(BaseModel):
    scheduled_date: date
