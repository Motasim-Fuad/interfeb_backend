import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class UUIDMixin:
    """Every table uses a UUID primary key (future-proof, safe for multi-service / offline sync)."""
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)


class TimestampMixin:
    """created_at / updated_at on every table, auto-managed by the DB."""
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AuditMixin:
    """
    Who created/edited a record. Kept as free text (role + name typed at entry),
    since app login is currently role-based access codes, not individual employee accounts.
    Upgrade path: add an `employees` table later and swap these to a FK without breaking anything.
    """
    created_by_role = Column(String, nullable=True)
    created_by_name = Column(String, nullable=True)


class BaseEntity(UUIDMixin, TimestampMixin, AuditMixin):
    """Standard base for every transactional table in the system."""
    __abstract__ = True
