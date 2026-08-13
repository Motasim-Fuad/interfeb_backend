from sqlalchemy import Column, String, Numeric, Integer, Date, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base


class FabricCorrelationReport(Base, BaseEntity):
    __tablename__ = "fabric_correlation_reports"

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    tolerance_standard_id = Column(UUID(as_uuid=True), ForeignKey("acceptance_standards.id"), nullable=True)
    tolerance_snapshot = Column(Numeric, nullable=True)
    standard_snapshot = Column(Numeric, nullable=True)
    scs_snapshot = Column(Integer, nullable=True)
    measurement_unit_snapshot = Column(String, nullable=False, default="inch")
    quantity_unit_snapshot = Column(String, nullable=False, default="yds")
    qc_setup_version_snapshot = Column(Integer, nullable=False, default=1)

    plm = relationship("Plm")
    rolls = relationship("FabricCorrelationRoll", back_populates="report", cascade="all, delete-orphan")


class FabricCorrelationRoll(Base, BaseEntity):
    __tablename__ = "fabric_correlation_rolls"
    __table_args__ = (UniqueConstraint("report_id", "roll_no", name="uq_correlation_report_roll"),)

    report_id = Column(UUID(as_uuid=True), ForeignKey("fabric_correlation_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    color_code = Column(String, nullable=True)
    length_yards = Column(Numeric, nullable=True)
    width = Column(Numeric, nullable=True)
    standard = Column(Numeric, nullable=True)
    scs = Column(Integer, nullable=True)
    mill_total_point = Column(Numeric, nullable=True)
    roll_date = Column(Date, nullable=True)

    factory_total_points = Column(Integer, default=0, nullable=False)
    factory_percent_points = Column(Numeric, nullable=True)
    factory_length = Column(Numeric, nullable=True)
    factory_width = Column(Numeric, nullable=True)

    report = relationship("FabricCorrelationReport", back_populates="rolls")
    defects = relationship("FabricCorrelationRollDefect", back_populates="roll", cascade="all, delete-orphan")


class FabricCorrelationRollDefect(Base, BaseEntity):
    __tablename__ = "fabric_correlation_roll_defects"

    roll_id = Column(UUID(as_uuid=True), ForeignKey("fabric_correlation_rolls.id"), nullable=False, index=True)
    defect_type_id = Column(UUID(as_uuid=True), ForeignKey("defect_types.id"), nullable=False)
    range_config_id = Column(UUID(as_uuid=True), ForeignKey("defect_range_configs.id"), nullable=False)
    count = Column(Integer, nullable=False, default=0)
    points_snapshot = Column(Integer, nullable=False, default=0)
    defect_type_name_snapshot = Column(String, nullable=True)
    range_label_snapshot = Column(String, nullable=True)

    roll = relationship("FabricCorrelationRoll", back_populates="defects")
