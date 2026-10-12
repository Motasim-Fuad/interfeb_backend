from datetime import datetime
from typing import Optional, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AppLoginRequest(BaseModel):
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    label: Optional[str] = None


AccessCodeRole = Literal["worker", "inspection", "store"]


class AccessCodeCreate(BaseModel):
    role: AccessCodeRole
    label: str
    expires_at: Optional[datetime] = None


class AccessCodeCreated(BaseModel):
    """Returned ONCE at creation time - plain code is never retrievable again after this."""
    id: UUID
    role: str
    label: str
    plain_code: str


class AccessCodeStatusUpdate(BaseModel):
    is_active: bool


class AccessCodeOut(BaseModel):
    id: UUID
    role: str
    label: str
    is_active: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    setup_secret: str
