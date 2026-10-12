from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.features.audit.models import AuditLog


def _role(user) -> Optional[str]:
    if user is None:
        return None
    return "admin" if getattr(user, "is_admin", False) else getattr(user, "role", None)


def actor_label(user: Optional[CurrentUser]) -> Optional[str]:
    if user is None:
        return None
    return getattr(user, "label", None) or _role(user)


def record_audit(
    db: Session,
    user: Optional[CurrentUser],
    *,
    action: str,
    entity_type: str,
    entity_id=None,
    plm_id=None,
    summary: Optional[str] = None,
    data: Optional[dict[str, Any]] = None,
) -> None:
    """Adds an audit row to the current transaction; the caller commits."""
    db.add(AuditLog(
        actor_role=_role(user),
        actor_label=actor_label(user),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        plm_id=plm_id,
        summary=summary,
        data=data,
    ))


def stamp_creator(row, user: Optional[CurrentUser]) -> None:
    if user is None:
        return
    if getattr(row, "created_by_role", None) is None:
        row.created_by_role = _role(user)
    if getattr(row, "created_by_name", None) is None:
        row.created_by_name = actor_label(user)
