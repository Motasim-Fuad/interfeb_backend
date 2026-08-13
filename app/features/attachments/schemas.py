from uuid import UUID
from pydantic import BaseModel


class AttachmentOut(BaseModel):
    id: UUID
    owner_type: str
    owner_id: UUID
    file_url: str
    class Config: from_attributes = True
