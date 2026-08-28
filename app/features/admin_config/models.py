from sqlalchemy import Column, String, Integer, Boolean, Numeric, ForeignKey, Enum, UniqueConstraint, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import UUIDMixin, TimestampMixin
from app.core.database import Base


class Buyer(Base, UUIDMixin, TimestampMixin):
    """Formal buyer list instead of free-typed buyer names, so overrides/reports stay consistent."""
    __tablename__ = "buyers"
    name = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class Supplier(Base, UUIDMixin, TimestampMixin):
    """
    Formal supplier list (mills/vendors) - Golden MC, Bosundhara, Jomuna, etc.
    default_measurement_unit drives PLM.measurement_unit automatically once a PLM is
    linked to this supplier's fabric receive - never picked by hand per-PLM anymore.
    """
    __tablename__ = "suppliers"
    name = Column(String, unique=True, nullable=False)
    default_measurement_unit = Column(String, nullable=False, default="inch")  # 'inch' | 'cm'
    is_active = Column(Boolean, default=True, nullable=False)


class SupplierQcProfile(Base, UUIDMixin, TimestampMixin):
    """Reusable supplier-level QC defaults. Fabric Receive snapshots these values.

    This is intentionally a JSON profile because each QC module has different planning
    fields and the dashboard needs to evolve without adding one database column for every
    paper-form field. Final production truth is still FabricReceive.qc_config.
    """
    __tablename__ = "supplier_qc_profiles"
    __table_args__ = (UniqueConstraint("supplier_id", name="uq_supplier_qc_profile_supplier"),)

    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False, index=True)
    qc_config = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, default=True, nullable=False)

    supplier = relationship("Supplier")


class Material(Base, UUIDMixin, TimestampMixin):
    """Reusable fabric identity (Name / Composition / Construction / Appearance).

    Dashboard master data, same pattern as Defect Types. GSM expected, GSM
    tolerance, accepted moisture, and skew acceptance stay live — editing a
    Material updates every PLM that uses it. Consignment 4-point QC is still
    snapshotted separately.
    """
    __tablename__ = "materials"

    # Name
    fabric_name = Column(String, nullable=False)
    dyeing_method = Column(String, nullable=True)
    primary_fiber = Column(String, nullable=True)
    fabric_code = Column(String, nullable=True)
    # Composition
    fiber_type_1 = Column(String, nullable=True)
    fiber_1_pct = Column(String, nullable=True)
    fiber_type_2 = Column(String, nullable=True)
    fiber_2_pct = Column(String, nullable=True)
    fiber_type_3 = Column(String, nullable=True)
    fiber_3_pct = Column(String, nullable=True)
    accepted_moisture = Column(String, nullable=True)
    # Construction
    weave_knit_type = Column(String, nullable=True)
    warp_count = Column(String, nullable=True)
    weft_count = Column(String, nullable=True)
    epi = Column(String, nullable=True)
    ppi = Column(String, nullable=True)
    gsm_expected = Column(String, nullable=True)
    gsm_tolerance = Column(String, nullable=True)
    fabric_width_expected = Column(String, nullable=True)
    width_tolerance = Column(String, nullable=True)
    fabric_length_per_roll = Column(String, nullable=True)
    # Appearance
    shade_color_name = Column(String, nullable=True)
    shade_code = Column(String, nullable=True)
    finish = Column(String, nullable=True)
    handfeel = Column(String, nullable=True)
    surface_defect_tolerance = Column(String, nullable=True)
    pattern_design = Column(String, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    @property
    def display_name(self) -> str:
        parts = [self.fabric_name, self.dyeing_method, self.primary_fiber]
        return " - ".join(p.strip() for p in parts if p and str(p).strip()) or (self.fabric_name or "Material")

    @property
    def composition_label(self) -> str:
        chunks = []
        for i in (1, 2, 3):
            fiber = getattr(self, f"fiber_type_{i}")
            pct = getattr(self, f"fiber_{i}_pct")
            if fiber and str(fiber).strip():
                if pct and str(pct).strip():
                    chunks.append(f"{str(pct).strip()}% {str(fiber).strip()}")
                else:
                    chunks.append(str(fiber).strip())
        return " / ".join(chunks) or (self.primary_fiber or "")


class DefectType(Base, UUIDMixin, TimestampMixin):
    """Shade, Hole, Naps... - admin adds/removes from dashboard, used by Fabric Inspection + Correlation."""
    __tablename__ = "defect_types"
    name = Column(String, unique=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class DefectRangeConfig(Base, UUIDMixin, TimestampMixin):
    """The 4-point ranges (0-3in=1pt, 3-6in=2pt, ...). defect_type_id nullable = shared across all types."""
    __tablename__ = "defect_range_configs"
    defect_type_id = Column(UUID(as_uuid=True), ForeignKey("defect_types.id"), nullable=True)
    range_label = Column(String, nullable=False)
    points = Column(Integer, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    defect_type = relationship("DefectType")


class AcceptanceStandard(Base, UUIDMixin, TimestampMixin):
    """
    Generic acceptance standard reused across GSM tolerance, skew standard,
    relaxation/shrinkage standard, correlation tolerance, fabric-inspection standard.
    Resolution order: supplier-specific row wins, then buyer-specific, then global default.
    """
    __tablename__ = "acceptance_standards"
    module = Column(String, nullable=False, index=True)   # 'fabric_inspection' | 'gsm' | 'skew_bowing' | ...
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    standard_value = Column(Numeric, nullable=False)
    label = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    buyer = relationship("Buyer")
    supplier = relationship("Supplier")


class FabricCompositionStandard(Base, UUIDMixin, TimestampMixin):
    """Supplier-scoped reusable Moisture defaults. Final value is confirmed on consignment QC setup."""
    __tablename__ = "fabric_composition_standards"
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    composition_name = Column(String, nullable=False)
    acceptance_percentage = Column(Numeric, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    supplier = relationship("Supplier")


class FabricWidthStandard(Base, UUIDMixin, TimestampMixin):
    """Supplier-scoped reusable Skew & Bowing defaults."""
    __tablename__ = "fabric_width_standards"
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    width_label = Column(String, nullable=False)
    solid_standard = Column(Numeric, nullable=False)
    stripe_standard = Column(Numeric, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    supplier = relationship("Supplier")


class ShadeCheckPointDefinition(Base, UUIDMixin, TimestampMixin):
    """Supplier-scoped Shade Check point definition managed from Supplier QC Profile."""
    __tablename__ = "shade_check_point_definitions"
    __table_args__ = (UniqueConstraint("supplier_id", "check_key", name="uq_shade_point_supplier_key"),)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    check_key = Column(String, nullable=False)
    label = Column(String, nullable=False)
    check_type = Column(Enum("length", "width", name="shade_check_type"), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    supplier = relationship("Supplier")
