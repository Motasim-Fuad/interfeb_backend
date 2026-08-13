from sqlalchemy import Column, String, Numeric, Date, Boolean, ForeignKey, UniqueConstraint, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base


class ShadeCheckReport(Base, BaseEntity):
    __tablename__ = "shade_check_reports"

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)
    comments = Column(String, nullable=True)
    points_snapshot = Column(JSON, nullable=False, default=list)
    qc_setup_version_snapshot = Column(Integer, nullable=False, default=1)

    plm = relationship("Plm")
    rolls = relationship("ShadeCheckRoll", back_populates="report", cascade="all, delete-orphan")


class ShadeCheckRoll(Base, BaseEntity):
    __tablename__ = "shade_check_rolls"
    __table_args__ = (UniqueConstraint("report_id", "roll_no", name="uq_shade_report_roll"),)

    report_id = Column(UUID(as_uuid=True), ForeignKey("shade_check_reports.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    colour = Column(String, nullable=True)
    cons = Column(Numeric, nullable=True)
    roll_date = Column(Date, nullable=True)
    remarks = Column(String, nullable=True)

    report = relationship("ShadeCheckReport", back_populates="rolls")
    results = relationship("ShadeCheckResult", back_populates="roll", cascade="all, delete-orphan")


class ShadeCheckResult(Base, BaseEntity):
    __tablename__ = "shade_check_results"

    roll_id = Column(UUID(as_uuid=True), ForeignKey("shade_check_rolls.id"), nullable=False, index=True)
    point_definition_id = Column(UUID(as_uuid=True), ForeignKey("shade_check_point_definitions.id"), nullable=False)
    point_key_snapshot = Column(String, nullable=True)
    point_label_snapshot = Column(String, nullable=True)
    point_type_snapshot = Column(String, nullable=True)
    first_ok = Column(Boolean, nullable=True)
    middle_ok = Column(Boolean, nullable=True)   # left null for length-wise checks (single value = first_ok)
    last_ok = Column(Boolean, nullable=True)

    roll = relationship("ShadeCheckRoll", back_populates="results")
    point_definition = relationship("ShadeCheckPointDefinition")

    @property
    def point_key(self):
        return self.point_key_snapshot or (self.point_definition.check_key if self.point_definition else None)

    @property
    def point_label(self):
        return self.point_label_snapshot or (self.point_definition.label if self.point_definition else None)

    @property
    def point_type(self):
        return self.point_type_snapshot or (self.point_definition.check_type if self.point_definition else None)
