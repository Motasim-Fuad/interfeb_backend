import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from app.core.config import settings

# bcrypt only reads the first 72 bytes; passlib truncated silently, so keep doing that for old hashes.
_BCRYPT_MAX_BYTES = 72

# No 0/O/1/I/L so codes survive being read out loud or typed on a phone.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8  # ~40 bits: far beyond what the login rate limits allow to be guessed


# ---------- Password / Access-code hashing (both use bcrypt) ----------
def hash_secret(raw: str) -> str:
    return bcrypt.hashpw(raw.encode()[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode()


def verify_secret(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode()[:_BCRYPT_MAX_BYTES], hashed.encode())
    except (ValueError, TypeError):
        return False


def access_code_lookup(raw: str) -> str:
    return hashlib.sha256(raw.strip().upper().encode()).hexdigest()


def generate_access_code(prefix: str = "IF") -> str:
    """Human-typeable code the admin dashboard generates, e.g. IN-7KQ4M2XA"""
    return f"{prefix}-{''.join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))}"


# ---------- JWT ----------
def create_token(subject: str, extra_claims: dict, expires_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": subject, "iat": now, "exp": now + timedelta(minutes=expires_minutes), **extra_claims}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
