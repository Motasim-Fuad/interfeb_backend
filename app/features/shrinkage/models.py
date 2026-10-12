from sqlalchemy import Column, String, Numeric, Integer, Date, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base
from app.core.shared_enums import RollStatusEnum


class ShrinkagePatternReport(Base, BaseEntity):
    __tablename__ = "shrinkage_pattern_reports"
    __table_args__ = (UniqueConstraint("plm_id", name="uq_shrinkage_pattern_reports_plm"),)

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    season = Column(String, nullable=True)
    color = Column(String, nullable=True)
    qc_name = Column(String, nullable=True)
    report_date = Column(Date, nullable=True)
    standard_id = Column(UUID(as_uuid=True), ForeignKey("acceptance_standards.id"), nullable=True)
    standard_snapshot = Column(Numeric, nullable=True)

    plm = relationship("Plm")
    rolls = relationship("ShrinkageRoll", back_populates="report", cascade="all, delete-orphan")


class ShrinkageRoll(Base, BaseEntity):
    __tablename__ = "shrinkage_rolls"
    __table_args__ = (UniqueConstraint("report_id", "roll_no", name="uq_shrinkage_report_roll"),)
    # Phone-side save time; newer offline copies win (app/core/sync.py).
    client_updated_at = Column(DateTime, nullable=True)

    report_id = Column(UUID(as_uuid=True), ForeignKey("shrinkage_pattern_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    yds = Column(Numeric, nullable=True)
    pattern = Column(String, nullable=True)  # 'A' | 'B'
    shade = Column(String, nullable=True)
    before_wash_date = Column(Date, nullable=True)
    after_wash_date = Column(Date, nullable=True)
    roll_status = Column(RollStatusEnum, nullable=False, default="before_wash_done")

    report = relationship("ShrinkagePatternReport", back_populates="rolls")
    measurements = relationship("ShrinkageMeasurement", back_populates="roll", cascade="all, delete-orphan")


class ShrinkageMeasurement(Base, BaseEntity):
    __tablename__ = "shrinkage_measurements"

    roll_id = Column(UUID(as_uuid=True), ForeignKey("shrinkage_rolls.id"), nullable=False, index=True)
    reading_order = Column(Integer, nullable=False)  # 1, 2, 3
    before_length = Column(Numeric, nullable=True)
    before_width = Column(Numeric, nullable=True)
    after_length = Column(Numeric, nullable=True)
    after_width = Column(Numeric, nullable=True)

    roll = relationship("ShrinkageRoll", back_populates="measurements")
