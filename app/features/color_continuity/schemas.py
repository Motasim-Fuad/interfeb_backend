from datetime import date
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel


class RollIn(BaseModel):
    roll_no: str
    shade_group_before: Optional[str] = None
    shade_group_after: Optional[str] = None
    yardage: Optional[float] = None
    before_wash_date: Optional[date] = None
    after_wash_date: Optional[date] = None
    roll_status: str = "before_wash_done"


class RollOut(RollIn):
    id: UUID
    class Config: from_attributes = True


class CardCreate(BaseModel):
    plm_id: UUID


class CardOut(BaseModel):
    id: UUID
    plm_id: UUID
    rolls: List[RollOut] = []
    total_roll: int = 0
    total_yardage: float = 0
    class Config: from_attributes = True
