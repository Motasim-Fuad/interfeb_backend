import secrets
from datetime import datetime, timedelta
from typing import Optional

from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------- Password / Access-code hashing (both use bcrypt) ----------
def hash_secret(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_secret(raw: str, hashed: str) -> bool:
    return pwd_context.verify(raw, hashed)


def generate_access_code(prefix: str = "IF") -> str:
    """Human-typeable code the admin dashboard generates, e.g. IF-7K2N9P"""
    return f"{prefix}-{secrets.token_hex(3).upper()}"


# ---------- JWT ----------
def create_token(subject: str, extra_claims: dict, expires_minutes: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    payload = {"sub": subject, "exp": expire, **extra_claims}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
