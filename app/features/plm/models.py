from sqlalchemy import Column, String, Date, Enum, ForeignKey, Numeric, UniqueConstraint, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base
from app.core.shared_enums import MeasurementUnitEnum


class Plm(Base, BaseEntity):
    """Production order under one received fabric consignment."""
    __tablename__ = "plms"

    plm_code = Column(String, unique=True, nullable=False, index=True)
    plm_date = Column(Date, nullable=False)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    fabric_receive_id = Column(UUID(as_uuid=True), ForeignKey("fabric_receives.id"), nullable=True)
    plm_style = Column(String, nullable=True)
    fabric_type = Column(String, nullable=True)
    fabric_composition = Column(String, nullable=True)
    consignment = Column(String, nullable=True)
    measurement_unit = Column(MeasurementUnitEnum, nullable=False, default="inch")
    allocated_quantity = Column(Numeric, nullable=True)

    # Snapshot the finalized consignment context at PLM creation. A later QC revision on
    # the same consignment may be used by NEW PLMs, but must never silently change an
    # already-created PLM or any inspection started from it.
    received_quantity = Column(Numeric, nullable=True)
    quantity_unit = Column(String, nullable=False, default="yds")
    received_at = Column(Date, nullable=True)
    qc_config = Column(JSON, nullable=False, default=dict)
    qc_setup_version = Column(Integer, nullable=False, default=1)

    buyer = relationship("Buyer")
    supplier = relationship("Supplier")
    fabric_receive = relationship("FabricReceive", back_populates="plms")
    colors = relationship("PlmColor", back_populates="plm", cascade="all, delete-orphan")

    @property
    def buyer_name(self):
        return self.buyer.name if self.buyer else None

    @property
    def supplier_name(self):
        return self.supplier.name if self.supplier else None


class PlmColor(Base, BaseEntity):
    __tablename__ = "plm_colors"
    __table_args__ = (UniqueConstraint("plm_id", "colour_code", name="uq_plm_color_code"),)

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    colour_code = Column(String, nullable=False)
    planned_quantity = Column(Numeric, nullable=True)

    plm = relationship("Plm", back_populates="colors")
    rolls = relationship("PlmRoll", back_populates="plm_color", cascade="all, delete-orphan")


class PlmRoll(Base, BaseEntity):
    __tablename__ = "plm_rolls"
    __table_args__ = (UniqueConstraint("plm_color_id", "roll_no", name="uq_plm_color_roll_no"),)

    plm_color_id = Column(UUID(as_uuid=True), ForeignKey("plm_colors.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    marked_width = Column(Numeric, nullable=True)
    marked_length = Column(Numeric, nullable=True)
    planned_quantity = Column(Numeric, nullable=True)
    # Supplier/mill total defect point supplied during Dashboard roll planning.
    # Correlation compares this against the factory 4-point result; the app never re-enters it.
    supplier_total_point = Column(Numeric, nullable=True)

    plm_color = relationship("PlmColor", back_populates="rolls")


class InspectionSchedule(Base, BaseEntity):
    __tablename__ = "inspection_schedules"

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    scheduled_date = Column(Date, nullable=False)
    status = Column(Enum("pending", "in_progress", "completed", name="schedule_status_enum"),
                     nullable=False, default="pending")
    assigned_to = Column(String, nullable=True)
    machine_note = Column(String, nullable=True)

    plm = relationship("Plm")

    @property
    def plm_code(self):
        return self.plm.plm_code if self.plm else None

    @property
    def buyer_name(self):
        return self.plm.buyer.name if self.plm and self.plm.buyer else None

    @property
    def supplier_name(self):
        return self.plm.supplier.name if self.plm and self.plm.supplier else None
