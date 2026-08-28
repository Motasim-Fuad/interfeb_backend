from sqlalchemy import Column, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base


class SupplierMonthlyInput(Base, BaseEntity):
    """Dashboard monthly notes per supplier: communication rating and optional comment."""
    __tablename__ = "supplier_monthly_inputs"
    __table_args__ = (UniqueConstraint("supplier_id", "year", "month", name="uq_supplier_month_input"),)

    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    communication_rating = Column(Numeric, nullable=True)
    notes = Column(String, nullable=True)

    supplier = relationship("Supplier")
