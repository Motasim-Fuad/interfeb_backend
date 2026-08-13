from typing import Iterable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, sub: str, role: str, label: Optional[str] = None, is_admin: bool = False):
        self.sub = sub
        self.role = role
        self.label = label
        self.is_admin = is_admin


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

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
