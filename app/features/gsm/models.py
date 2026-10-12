from sqlalchemy import Column, String, Numeric, Date, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base
from app.core.shared_enums import RollStatusEnum as ROLL_STATUS


class GsmCheckReport(Base, BaseEntity):
    __tablename__ = "gsm_check_reports"
    __table_args__ = (UniqueConstraint("plm_id", name="uq_gsm_check_reports_plm"),)

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=True)
    color = Column(String, nullable=True)
    standard_gsm = Column(Numeric, nullable=True)
    tolerance_standard_id = Column(UUID(as_uuid=True), ForeignKey("acceptance_standards.id"), nullable=True)
    tolerance_snapshot = Column(Numeric, nullable=True)

    plm = relationship("Plm")
    rolls = relationship("GsmCheckRoll", back_populates="report", cascade="all, delete-orphan")


class GsmCheckRoll(Base, BaseEntity):
    __tablename__ = "gsm_check_rolls"
    __table_args__ = (UniqueConstraint("report_id", "roll_no", name="uq_gsm_report_roll"),)
    # Phone-side save time; newer offline copies win (app/core/sync.py).
    client_updated_at = Column(DateTime, nullable=True)

    report_id = Column(UUID(as_uuid=True), ForeignKey("gsm_check_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    color = Column(String, nullable=True)

    actual_gsm_before = Column(Numeric, nullable=True)
    actual_gsm_after = Column(Numeric, nullable=True)
    weight = Column(Numeric, nullable=True)
    remarks_before = Column(String, nullable=True)
    remarks_after = Column(String, nullable=True)
    before_wash_date = Column(Date, nullable=True)
    after_wash_date = Column(Date, nullable=True)
    roll_status = Column(ROLL_STATUS, nullable=False, default="before_wash_done")

    report = relationship("GsmCheckReport", back_populates="rolls")
