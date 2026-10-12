from sqlalchemy import Column, String, Numeric, Date, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base
from app.core.shared_enums import RollStatusEnum


class MoistureCheckReport(Base, BaseEntity):
    __tablename__ = "moisture_check_reports"

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=True)
    report_time = Column(String, nullable=True)
    vendor = Column(String, nullable=True)
    factory = Column(String, nullable=True)
    fabric_composition_standard_id = Column(UUID(as_uuid=True), ForeignKey("fabric_composition_standards.id"), nullable=True)
    acceptance_snapshot = Column(Numeric, nullable=True)

    plm = relationship("Plm")
    rolls = relationship("MoistureCheckRoll", back_populates="report", cascade="all, delete-orphan")


class MoistureCheckRoll(Base, BaseEntity):
    __tablename__ = "moisture_check_rolls"
    __table_args__ = (UniqueConstraint("report_id", "roll_no", name="uq_moisture_report_roll"),)
    # Phone-side save time; newer offline copies win (app/core/sync.py).
    client_updated_at = Column(DateTime, nullable=True)

    report_id = Column(UUID(as_uuid=True), ForeignKey("moisture_check_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    color = Column(String, nullable=True)
    fabric_composition = Column(String, nullable=True)

    swatch_rating_before = Column(Numeric, nullable=True)
    swatch_rating_after = Column(Numeric, nullable=True)
    before_wash_date = Column(Date, nullable=True)
    after_wash_date = Column(Date, nullable=True)
    roll_status = Column(RollStatusEnum, nullable=False, default="before_wash_done")

    report = relationship("MoistureCheckReport", back_populates="rolls")
