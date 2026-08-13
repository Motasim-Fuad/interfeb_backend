from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.base_model import UUIDMixin, TimestampMixin
from app.core.database import Base


class Attachment(Base, UUIDMixin, TimestampMixin):
    """
    One generic table for every photo in every module (GSM roll photo, skew swatch photo, etc).
    owner_type + owner_id = polymorphic pointer, e.g. owner_type='gsm_roll', owner_id=<gsm_check_rolls.id>
    File bytes never touch Postgres - only the storage key/url, resolved via app/core/storage.py.
    """
    __tablename__ = "attachments"

    owner_type = Column(String, nullable=False, index=True)
    owner_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    storage_key = Column(String, nullable=False)
    file_url = Column(String, nullable=False)
    uploaded_by = Column(String, nullable=True)
