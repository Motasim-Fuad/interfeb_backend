from sqlalchemy import Column, String, Numeric, Integer, Date, Boolean, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base
from app.core.shared_enums import RollStatusEnum


class SkewBowingReport(Base, BaseEntity):
    __tablename__ = "skew_bowing_reports"

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=True)
    receive_qty = Column(Numeric, nullable=True)
    total_roll = Column(Integer, nullable=True)
    fabric_construction = Column(String, nullable=True)
    in_house_date = Column(Date, nullable=True)
    fabric_width_standard_id = Column(UUID(as_uuid=True), ForeignKey("fabric_width_standards.id"), nullable=True)
    is_stripe = Column(Boolean, default=False, nullable=False)
    standard_point_snapshot = Column(Numeric, nullable=True)
    fabric_width_label_snapshot = Column(String, nullable=True)
    measurement_unit_snapshot = Column(String, nullable=False, default="inch")
    quantity_unit_snapshot = Column(String, nullable=False, default="yds")
    qc_setup_version_snapshot = Column(Integer, nullable=False, default=1)
    comments_action_plan = Column(String, nullable=True)

    plm = relationship("Plm")
    rolls = relationship("SkewBowingRoll", back_populates="report", cascade="all, delete-orphan")


class SkewBowingRoll(Base, BaseEntity):
    __tablename__ = "skew_bowing_rolls"
    __table_args__ = (UniqueConstraint("report_id", "roll_no", name="uq_skew_report_roll"),)
    # Phone-side save time; newer offline copies win (app/core/sync.py).
    client_updated_at = Column(DateTime, nullable=True)

    report_id = Column(UUID(as_uuid=True), ForeignKey("skew_bowing_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    colour_code = Column(String, nullable=True)

    ac_value_before = Column(Numeric, nullable=True)
    bd_value_before = Column(Numeric, nullable=True)
    ac_value_after = Column(Numeric, nullable=True)
    bd_value_after = Column(Numeric, nullable=True)
    reference_fabric_length = Column(Numeric, nullable=True)
    before_wash_date = Column(Date, nullable=True)
    after_wash_date = Column(Date, nullable=True)
    roll_status = Column(RollStatusEnum, nullable=False, default="before_wash_done")

    report = relationship("SkewBowingReport", back_populates="rolls")
