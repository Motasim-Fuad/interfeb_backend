from sqlalchemy import Column, String, Numeric, Integer, Date, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base


class FabricRelaxationReport(Base, BaseEntity):
    __tablename__ = "fabric_relaxation_reports"
    __table_args__ = (UniqueConstraint("plm_id", name="uq_fabric_relaxation_reports_plm"),)

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    color = Column(String, nullable=True)
    standard_id = Column(UUID(as_uuid=True), ForeignKey("acceptance_standards.id"), nullable=True)
    standard_snapshot = Column(Numeric, nullable=True)  # legacy, no longer used for pass/fail
    relaxation_hours_snapshot = Column(Integer, nullable=True)
    comments = Column(String, nullable=True)

    plm = relationship("Plm")
    rolls = relationship("FabricRelaxationRoll", back_populates="report", cascade="all, delete-orphan")


class FabricRelaxationRoll(Base, BaseEntity):
    __tablename__ = "fabric_relaxation_rolls"
    __table_args__ = (UniqueConstraint("report_id", "roll_no", name="uq_relaxation_report_roll"),)
    # Phone-side save time; newer offline copies win (app/core/sync.py).
    client_updated_at = Column(DateTime, nullable=True)

    report_id = Column(UUID(as_uuid=True), ForeignKey("fabric_relaxation_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    color_code = Column(String, nullable=True)
    fabric_yds_mtr = Column(Numeric, nullable=True)
    roll_date = Column(Date, nullable=True)
    before_relax_start_time = Column(String, nullable=True)
    before_relax_length = Column(Numeric, nullable=True)
    before_relax_width = Column(Numeric, nullable=True)
    after_relax_time = Column(String, nullable=True)
    after_relax_length = Column(Numeric, nullable=True)
    after_relax_width = Column(Numeric, nullable=True)
    relaxation_hours = Column(Integer, nullable=True)
    remark = Column(String, nullable=True)

    report = relationship("FabricRelaxationReport", back_populates="rolls")
