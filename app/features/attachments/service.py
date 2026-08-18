from uuid import UUID
from typing import Optional
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


def attach_stage_photos(db: Session, report, *, before_type: Optional[str] = None, after_type: Optional[str] = None, report_type: Optional[str] = None):
    """One optional photo per wash stage (or one report photo) — never per roll."""
    if before_type:
        report.before_wash_attachments = get_attachments(db, before_type, report.id)
    if after_type:
        report.after_wash_attachments = get_attachments(db, after_type, report.id)
    if report_type:
        report.attachments = get_attachments(db, report_type, report.id)
    return report
