from sqlalchemy import Column, String, Numeric, Date, ForeignKey, Index, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base
from app.core.shared_enums import MeasurementUnitEnum


class FabricReceive(Base, BaseEntity):
    """
    One physical fabric consignment received by Store.

    This row is the FINAL planning/QC context for everything created from the consignment:
    buyer, supplier, fabric info, quantity, measurement unit and module-specific QC setup.
    PLMs/colours/rolls never re-ask for these values. Reports snapshot the relevant values
    when inspection starts so historical results do not change if this setup is edited later.
    """
    __tablename__ = "fabric_receives"
    __table_args__ = (
        Index("ix_fabric_receives_supplier_id", "supplier_id"),
        Index("ix_fabric_receives_buyer_id", "buyer_id"),
        Index("ix_fabric_receives_received_at", "received_at"),
        Index("ix_fabric_receives_created_at", "created_at"),
    )

    consignment = Column(String, nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=True)

    # Backwards compatible legacy field. New UI uses received_quantity.
    fabric_length = Column(Numeric, nullable=True)
    received_quantity = Column(Numeric, nullable=True)
    quantity_unit = Column(String, nullable=False, default="yds")

    fabric_type = Column(String, nullable=True)
    fabric_composition = Column(String, nullable=True)
    measurement_unit = Column(MeasurementUnitEnum, nullable=False, default="inch")
    received_at = Column(Date, nullable=True)

    # Flexible module-wise dashboard configuration. Example:
    # {"gsm": {"standard_gsm": 180, "tolerance_percent": 5},
    #  "fabric_inspection": {"individual_standard": 40, "average_standard": 40,
    #                        "checked_quantity": 500}}
    qc_config = Column(JSON, nullable=False, default=dict)
    qc_setup_status = Column(String, nullable=False, default="draft")  # draft | finalized
    qc_setup_version = Column(Integer, nullable=False, default=1)
    notes = Column(String, nullable=True)

    buyer = relationship("Buyer")
    supplier = relationship("Supplier")
    plms = relationship("Plm", back_populates="fabric_receive")
