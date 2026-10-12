from sqlalchemy import Column, String, Numeric, Integer, Date, DateTime, Boolean, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base

DISPOSITIONS = ("pending", "accepted", "hold", "rejected", "claim")
DEFECT_SIDES = ("left", "center", "right")


class FabricInspectionReport(Base, BaseEntity):
    __tablename__ = "fabric_inspection_reports"

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    color = Column(String, nullable=True)
    fabric_type = Column(String, nullable=True)
    fabric_composition = Column(String, nullable=True)
    received_qty = Column(Numeric, nullable=True)
    checked_qty = Column(Numeric, nullable=True)

    individual_standard_id = Column(UUID(as_uuid=True), ForeignKey("acceptance_standards.id"), nullable=True)
    individual_standard_snapshot = Column(Numeric, nullable=True)
    average_standard_id = Column(UUID(as_uuid=True), ForeignKey("acceptance_standards.id"), nullable=True)
    average_standard_snapshot = Column(Numeric, nullable=True)

    fabric_in_house_date = Column(Date, nullable=True)
    inspection_date = Column(Date, nullable=True, index=True)
    consignment = Column(String, nullable=True)
    comments = Column(String, nullable=True)
    measurement_unit_snapshot = Column(String, nullable=False, default="inch")
    quantity_unit_snapshot = Column(String, nullable=False, default="yds")
    qc_setup_version_snapshot = Column(Integer, nullable=False, default=1)

    plm = relationship("Plm")
    rolls = relationship("FabricInspectionRoll", back_populates="report", cascade="all, delete-orphan")


class FabricInspectionRoll(Base, BaseEntity):
    __tablename__ = "fabric_inspection_rolls"
    __table_args__ = (
        UniqueConstraint("report_id", "roll_no", name="uq_fabric_inspection_report_roll"),
        Index("ix_fabric_inspection_rolls_created_at", "created_at"),
        Index("ix_fabric_inspection_rolls_is_pass", "is_pass"),
    )

    report_id = Column(UUID(as_uuid=True), ForeignKey("fabric_inspection_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    colour_code = Column(String, nullable=True)
    width_marked = Column(Numeric, nullable=True)
    length_marked = Column(Numeric, nullable=True)
    width_actual = Column(Numeric, nullable=True)
    length_actual = Column(Numeric, nullable=True)
    remarks = Column(String, nullable=True)

    # computed + cached at save time (see service.py) so listing rolls doesn't need to re-sum defects every time
    total_points = Column(Integer, default=0, nullable=False)
    percent_points = Column(Numeric, nullable=True)
    is_pass = Column(Boolean, nullable=True)  # None = not yet evaluated

    # QC decision on the physical roll, separate from the computed pass/fail.
    disposition = Column(String, nullable=False, default="pending", server_default="pending", index=True)
    disposition_note = Column(String, nullable=True)
    shade_band = Column(String, nullable=True)
    inspector_name = Column(String, nullable=True)
    inspected_at = Column(DateTime, nullable=True, index=True)
    # Device time of the save that produced this state; older offline replays are ignored.
    client_updated_at = Column(DateTime, nullable=True)

    report = relationship("FabricInspectionReport", back_populates="rolls")
    defects = relationship("FabricInspectionRollDefect", back_populates="roll", cascade="all, delete-orphan")


class FabricInspectionRollDefect(Base, BaseEntity):
    __tablename__ = "fabric_inspection_roll_defects"

    roll_id = Column(UUID(as_uuid=True), ForeignKey("fabric_inspection_rolls.id"), nullable=False, index=True)
    defect_type_id = Column(UUID(as_uuid=True), ForeignKey("defect_types.id"), nullable=False, index=True)
    range_config_id = Column(UUID(as_uuid=True), ForeignKey("defect_range_configs.id"), nullable=False)
    count = Column(Integer, nullable=False, default=0)
    points_snapshot = Column(Integer, nullable=False, default=0)
    defect_type_name_snapshot = Column(String, nullable=True)
    range_label_snapshot = Column(String, nullable=True)
    # Optional per-defect location/evidence; legacy aggregated rows leave these empty.
    position_yard = Column(Numeric, nullable=True)
    side = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    recorded_at = Column(DateTime, nullable=True)

    roll = relationship("FabricInspectionRoll", back_populates="defects")
    defect_type = relationship("DefectType")
    range_config = relationship("DefectRangeConfig")

    @property
    def defect_type_name(self):
        return self.defect_type_name_snapshot or (self.defect_type.name if self.defect_type else None)

    @property
    def range_label(self):
        return self.range_label_snapshot or (self.range_config.range_label if self.range_config else None)
