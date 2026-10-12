from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.base_model import UUIDMixin, TimestampMixin
from app.core.database import Base


class InspectionReport(Base, UUIDMixin, TimestampMixin):
    """
    Per-roll master record. Created the first time ANY module submits data for a
    (plm_id, roll_number) pair, then updated in place as other modules submit for
    that same roll. Lets the dashboard show "this roll: GSM done, Moisture pending..." at a glance.
    """
    __tablename__ = "inspection_reports"
    __table_args__ = (UniqueConstraint("plm_id", "roll_number", name="uq_inspection_report_plm_roll"),)

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    roll_number = Column(String, nullable=False, index=True)
    color_name = Column(String, nullable=True)

    gsm_report_id = Column(UUID(as_uuid=True), ForeignKey("gsm_check_rolls.id"), nullable=True, index=True)
    moisture_report_id = Column(UUID(as_uuid=True), ForeignKey("moisture_check_rolls.id"), nullable=True, index=True)
    skew_report_id = Column(UUID(as_uuid=True), ForeignKey("skew_bowing_rolls.id"), nullable=True, index=True)
    relaxation_report_id = Column(UUID(as_uuid=True), ForeignKey("fabric_relaxation_rolls.id"), nullable=True, index=True)
    factory_report_id = Column(UUID(as_uuid=True), ForeignKey("factory_reports.id"), nullable=True, index=True)
    shade_check_report_id = Column(UUID(as_uuid=True), ForeignKey("shade_check_rolls.id"), nullable=True, index=True)
    correlation_report_id = Column(UUID(as_uuid=True), ForeignKey("fabric_correlation_rolls.id"), nullable=True, index=True)
    shrinkage_report_id = Column(UUID(as_uuid=True), ForeignKey("shrinkage_rolls.id"), nullable=True, index=True)
    color_continuity_card_id = Column(UUID(as_uuid=True), ForeignKey("color_continuity_rolls.id"), nullable=True, index=True)
    fabric_inspection_roll_id = Column(UUID(as_uuid=True), ForeignKey("fabric_inspection_rolls.id"), nullable=True, index=True)

    submitted_at = Column(DateTime, nullable=True, index=True)
