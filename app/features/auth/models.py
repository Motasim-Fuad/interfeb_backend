from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import BaseEntity, UUIDMixin, TimestampMixin
from app.core.database import Base
from app.core.shared_enums import AccessCodeRoleEnum


class AdminUser(Base, UUIDMixin, TimestampMixin):
    """Logs into the Admin Dashboard. Full JWT auth (email + password)."""
    __tablename__ = "admin_users"

    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class AccessCode(Base, UUIDMixin, TimestampMixin):
    """
    Role-based login code used by the Flutter app (worker / inspection / store).
    Admin generates these from the dashboard - no individual employee accounts needed.
    """
    __tablename__ = "access_codes"

    code_hash = Column(String, nullable=False)          # bcrypt hash of the plain code
    role = Column(AccessCodeRoleEnum, nullable=False, index=True)  # 'worker' | 'inspection' | 'store'
    label = Column(String, nullable=False)              # e.g. "Floor A Worker Code"
    is_active = Column(Boolean, default=True, nullable=False)
    created_by_admin_id = Column(UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)

    created_by_admin = relationship("AdminUser")
