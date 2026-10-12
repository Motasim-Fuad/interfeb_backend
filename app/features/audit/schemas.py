from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: UUID
    created_at: datetime
    actor_role: Optional[str] = None
    actor_label: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[UUID] = None
    plm_id: Optional[UUID] = None
    summary: Optional[str] = None

    class Config:
        from_attributes = True
