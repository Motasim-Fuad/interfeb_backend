from datetime import date
from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator


class FabricReceiveCreate(BaseModel):
    consignment: str
    supplier_id: UUID
    buyer_id: UUID
    fabric_length: Optional[float] = None  # legacy compatibility
    received_quantity: Optional[float] = None
    quantity_unit: str = "yds"
    fabric_type: Optional[str] = None
    fabric_composition: Optional[str] = None
    measurement_unit: Optional[str] = None  # compatibility only; backend always derives it from supplier
    received_at: Optional[date] = None
    qc_config: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("measurement_unit")
    @classmethod
    def validate_measurement_unit(cls, value):
        if value is not None and value not in ("inch", "cm"):
            raise ValueError("measurement_unit must be 'inch' or 'cm'")
        return value

    @field_validator("quantity_unit")
    @classmethod
    def validate_quantity_unit(cls, value):
        if value is not None and value not in ("yds", "meter"):
            raise ValueError("quantity_unit must be 'yds' or 'meter'")
        return value



class FabricReceiveUpdate(BaseModel):
    consignment: Optional[str] = None
    supplier_id: Optional[UUID] = None
    buyer_id: Optional[UUID] = None
    fabric_length: Optional[float] = None
    received_quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    fabric_type: Optional[str] = None
    fabric_composition: Optional[str] = None
    measurement_unit: Optional[str] = None
    received_at: Optional[date] = None
    qc_config: Optional[Dict[str, Dict[str, Any]]] = None

    @field_validator("measurement_unit")
    @classmethod
    def validate_measurement_unit(cls, value):
        if value is not None and value not in ("inch", "cm"):
            raise ValueError("measurement_unit must be 'inch' or 'cm'")
        return value

    @field_validator("quantity_unit")
    @classmethod
    def validate_quantity_unit(cls, value):
        if value is not None and value not in ("yds", "meter"):
            raise ValueError("quantity_unit must be 'yds' or 'meter'")
        return value



class FabricReceiveOut(FabricReceiveCreate):
    id: UUID
    qc_setup_version: int = 1
    class Config: from_attributes = True
