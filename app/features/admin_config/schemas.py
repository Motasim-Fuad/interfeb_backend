from typing import Optional
from uuid import UUID
from pydantic import BaseModel, field_validator


class BuyerIn(BaseModel):
    name: str
    is_active: bool = True


class BuyerOut(BuyerIn):
    id: UUID
    class Config: from_attributes = True


class SupplierIn(BaseModel):
    name: str
    default_measurement_unit: str = "inch"   # "inch" | "cm"
    is_active: bool = True

    @field_validator("default_measurement_unit")
    @classmethod
    def validate_measurement_unit(cls, value: str):
        value = value.strip().lower()
        if value not in ("inch", "cm"):
            raise ValueError("default_measurement_unit must be 'inch' or 'cm'")
        return value


class SupplierOut(SupplierIn):
    id: UUID
    class Config: from_attributes = True


class MaterialIn(BaseModel):
    fabric_name: str
    dyeing_method: Optional[str] = None
    primary_fiber: Optional[str] = None
    fabric_code: Optional[str] = None
    fiber_type_1: Optional[str] = None
    fiber_1_pct: Optional[str] = None
    fiber_type_2: Optional[str] = None
    fiber_2_pct: Optional[str] = None
    fiber_type_3: Optional[str] = None
    fiber_3_pct: Optional[str] = None
    accepted_moisture: Optional[str] = None
    weave_knit_type: Optional[str] = None
    warp_count: Optional[str] = None
    weft_count: Optional[str] = None
    epi: Optional[str] = None
    ppi: Optional[str] = None
    gsm_expected: Optional[str] = None
    gsm_tolerance: Optional[str] = None
    fabric_width_expected: Optional[str] = None
    width_tolerance: Optional[str] = None
    fabric_length_per_roll: Optional[str] = None
    shade_color_name: Optional[str] = None
    shade_code: Optional[str] = None
    finish: Optional[str] = None
    handfeel: Optional[str] = None
    surface_defect_tolerance: Optional[str] = None
    pattern_design: Optional[str] = None
    is_active: bool = True


class MaterialOut(MaterialIn):
    id: UUID
    display_name: str
    composition_label: str
    class Config: from_attributes = True


class DefectTypeIn(BaseModel):
    name: str
    sort_order: int = 0
    is_active: bool = True


class DefectTypeOut(DefectTypeIn):
    id: UUID
    class Config: from_attributes = True


class DefectRangeConfigIn(BaseModel):
    defect_type_id: Optional[UUID] = None
    range_label: str
    points: int
    sort_order: int = 0
    is_active: bool = True


class DefectRangeConfigOut(DefectRangeConfigIn):
    id: UUID
    class Config: from_attributes = True


class AcceptanceStandardIn(BaseModel):
    module: str
    buyer_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    standard_value: float
    label: Optional[str] = None
    is_active: bool = True


class AcceptanceStandardOut(AcceptanceStandardIn):
    id: UUID
    class Config: from_attributes = True


class FabricCompositionStandardIn(BaseModel):
    supplier_id: Optional[UUID] = None
    composition_name: str
    acceptance_percentage: float
    is_active: bool = True


class FabricCompositionStandardOut(FabricCompositionStandardIn):
    id: UUID
    class Config: from_attributes = True


class FabricWidthStandardIn(BaseModel):
    supplier_id: Optional[UUID] = None
    width_label: str
    solid_standard: float
    stripe_standard: float
    is_active: bool = True


class FabricWidthStandardOut(FabricWidthStandardIn):
    id: UUID
    class Config: from_attributes = True


class ShadeCheckPointDefinitionIn(BaseModel):
    supplier_id: Optional[UUID] = None
    check_key: str
    label: str
    check_type: str   # "length" | "width"
    sort_order: int = 0
    is_active: bool = True


class ShadeCheckPointDefinitionOut(ShadeCheckPointDefinitionIn):
    id: UUID
    class Config: from_attributes = True

from typing import Any, Dict, List
from pydantic import Field


class SupplierQcProfileIn(BaseModel):
    supplier_id: UUID
    qc_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class SupplierQcProfileOut(SupplierQcProfileIn):
    id: UUID
    class Config: from_attributes = True


class SupplierQcDefaultsOut(BaseModel):
    supplier_id: UUID
    supplier_name: str
    measurement_unit: str
    qc_config: Dict[str, Any] = Field(default_factory=dict)


class SupplierShadePointIn(BaseModel):
    check_key: str
    label: str
    check_type: str
    sort_order: int = 0
    is_active: bool = True

    @field_validator("check_type")
    @classmethod
    def validate_check_type(cls, value: str):
        value = value.strip().lower()
        if value not in ("length", "width"):
            raise ValueError("check_type must be 'length' or 'width'")
        return value


class SupplierQcProfileSaveIn(BaseModel):
    qc_config: Dict[str, Any] = Field(default_factory=dict)
    shade_points: List[SupplierShadePointIn] = Field(default_factory=list)
    is_active: bool = True
