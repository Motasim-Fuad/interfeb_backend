from uuid import UUID
from sqlalchemy.orm import Session

from app.features.attachments.models import Attachment


def get_attachments(db: Session, owner_type: str, owner_id: UUID):
    """Shared helper - every module's GET route calls this to embed photos in its response."""
    return (
        db.query(Attachment)
        .filter(Attachment.owner_type == owner_type, Attachment.owner_id == owner_id)
        .order_by(Attachment.created_at)
        .all()
    )
