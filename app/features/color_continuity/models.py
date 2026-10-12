from sqlalchemy import Column, String, Numeric, Date, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity
from app.core.database import Base
from app.core.shared_enums import RollStatusEnum


class ColorContinuityCard(Base, BaseEntity):
    __tablename__ = "color_continuity_cards"
    __table_args__ = (UniqueConstraint("plm_id", name="uq_color_continuity_cards_plm"),)

    plm_id = Column(UUID(as_uuid=True), ForeignKey("plms.id"), nullable=False, index=True)

    plm = relationship("Plm")
    rolls = relationship("ColorContinuityRoll", back_populates="card", cascade="all, delete-orphan")


class ColorContinuityRoll(Base, BaseEntity):
    __tablename__ = "color_continuity_rolls"
    __table_args__ = (UniqueConstraint("card_id", "roll_no", name="uq_continuity_card_roll"),)
    # Phone-side save time; newer offline copies win (app/core/sync.py).
    client_updated_at = Column(DateTime, nullable=True)

    card_id = Column(UUID(as_uuid=True), ForeignKey("color_continuity_cards.id"), nullable=False, index=True)
    roll_no = Column(String, nullable=False)
    shade_group_before = Column(String, nullable=True)
    shade_group_after = Column(String, nullable=True)
    yardage = Column(Numeric, nullable=True)
    before_wash_date = Column(Date, nullable=True)
    after_wash_date = Column(Date, nullable=True)
    roll_status = Column(RollStatusEnum, nullable=False, default="before_wash_done")

    card = relationship("ColorContinuityCard", back_populates="rolls")
