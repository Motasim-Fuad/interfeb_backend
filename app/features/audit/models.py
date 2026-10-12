from datetime import datetime

from sqlalchemy import Column, DateTime, Index, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.base_model import UUIDMixin
from app.core.database import Base


class AuditLog(Base, UUIDMixin):
    """Append-only record of who changed what. Never updated or deleted by the API."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_plm_id", "plm_id"),
    )

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor_role = Column(String, nullable=True)
    actor_label = Column(String, nullable=True)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    plm_id = Column(UUID(as_uuid=True), nullable=True)
    summary = Column(String, nullable=True)
    data = Column(JSON, nullable=True)
