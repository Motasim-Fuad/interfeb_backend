from sqlalchemy import Column, String, Numeric, Integer, Date, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base


class FactoryReport(Base, BaseEntity):
    __tablename__ = "factory_reports"
    __table_args__ = (UniqueConstraint("plm_id", name="uq_factory_report_plm"),)

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    supplier = Column(String, nullable=True)
    job_no = Column(String, nullable=True)
    construction = Column(String, nullable=True)
    color = Column(String, nullable=True)
    order_qty = Column(Numeric, nullable=True)
    inspection_yardage = Column(Numeric, nullable=True)
    inspection_pieces = Column(Integer, nullable=True)
    actual_width = Column(Numeric, nullable=True)
    total_point = Column(Numeric, nullable=True)
    avg_shipment_point = Column(Numeric, nullable=True)
    standard_snapshot = Column(Numeric, nullable=True)
    is_pass = Column(Boolean, nullable=True)
    inspection_date = Column(Date, nullable=True)
    inspection_percentage = Column(Numeric, nullable=True)
    inspection_stage = Column(String, nullable=True)

    plm = relationship("Plm")
