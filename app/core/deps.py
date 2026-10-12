from typing import Iterable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, sub: str, role: str, label: Optional[str] = None, is_admin: bool = False):
        self.sub = sub
        self.role = role
        self.label = label
        self.is_admin = is_admin


ACCESS_CODE_BLOCKED = "access_code_blocked"
ACCESS_CODE_REMOVED = "access_code_removed"


def _ensure_access_code_usable(db: Session, code_id: Optional[str]) -> None:
    """App tokens die with their access code: block/delete on the dashboard takes effect immediately."""
    from uuid import UUID

    from app.features.auth.models import AccessCode

    try:
        row = db.get(AccessCode, UUID(str(code_id)))
    except (TypeError, ValueError):
        row = None
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ACCESS_CODE_REMOVED)
    if not row.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ACCESS_CODE_BLOCKED)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    if not payload.get("is_admin", False):
        _ensure_access_code_usable(db, payload.get("sub"))

    return CurrentUser(
        sub=payload.get("sub"),
        role=payload.get("role"),
        label=payload.get("label"),
        is_admin=payload.get("is_admin", False),
    )


def require_roles(*roles: str):
    """Usage: Depends(require_roles('admin', 'inspection'))"""

    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.is_admin:
            return user
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not allowed to perform this action",
            )
        return user

    return _checker


def require_admin():
    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
        return user

    return _checker
