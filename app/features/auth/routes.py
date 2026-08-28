from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_admin, CurrentUser
from app.core.security import (
    hash_secret, verify_secret, generate_access_code, create_token,
)
from app.core.config import settings
from app.features.auth import models, schemas

router = APIRouter(prefix="/auth", tags=["Auth"])


# ---------------- Admin auth ----------------
@router.post("/admin/register", response_model=schemas.AccessCodeOut, include_in_schema=True)
def register_admin(payload: schemas.AdminUserCreate, db: Session = Depends(get_db)):
    """
    Requires ADMIN_SETUP_SECRET (set in .env) - only someone who knows this secret can
    create an admin account, whether it's the first one or a later one. This route stays
    open (not behind a login) since there's no admin to log in as before the first one exists,
    but the secret keeps it from being public.
    """
    if payload.setup_secret != settings.ADMIN_SETUP_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid setup secret")

    existing = db.query(models.AdminUser).filter(models.AdminUser.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    admin = models.AdminUser(
        email=payload.email,
        password_hash=hash_secret(payload.password),
        full_name=payload.full_name,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return {
        "id": admin.id, "role": "admin", "label": admin.full_name,
        "is_active": True, "expires_at": None, "last_used_at": None, "created_at": admin.created_at,
    }


@router.post("/admin/login", response_model=schemas.TokenResponse)
def admin_login(payload: schemas.AdminLoginRequest, db: Session = Depends(get_db)):
    admin = db.query(models.AdminUser).filter(models.AdminUser.email == payload.email).first()
    if not admin or not verify_secret(payload.password, admin.password_hash) or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_token(
        subject=str(admin.id),
        extra_claims={"role": "admin", "is_admin": True, "label": admin.full_name},
        expires_minutes=settings.ADMIN_TOKEN_EXPIRE_MINUTES,
    )
    return {"access_token": token, "role": "admin", "label": admin.full_name}


# ---------------- Access codes (admin manages, app logs in) ----------------
@router.post("/access-codes", response_model=schemas.AccessCodeCreated)
def create_access_code(
    payload: schemas.AccessCodeCreate,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin()),
):
    plain_code = generate_access_code(prefix=payload.role[:2].upper())
    row = models.AccessCode(
        code_hash=hash_secret(plain_code),
        role=payload.role,
        label=payload.label,
        expires_at=payload.expires_at,
        created_by_admin_id=UUID(admin.sub),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "role": row.role, "label": row.label, "plain_code": plain_code}


@router.get("/access-codes", response_model=List[schemas.AccessCodeOut])
def list_access_codes(db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin())):
    return (
        db.query(models.AccessCode)
        .filter(models.AccessCode.is_active == True)  # noqa: E712
        .order_by(models.AccessCode.created_at.desc())
        .all()
    )


@router.delete("/access-codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_access_code(code_id: UUID, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin())):
    row = db.query(models.AccessCode).filter(models.AccessCode.id == code_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Access code not found")
    db.delete(row)
    db.commit()


@router.patch("/access-codes/{code_id}/deactivate", response_model=schemas.AccessCodeOut)
def deactivate_access_code(code_id: UUID, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin())):
    """Legacy: prefer DELETE. Kept so older dashboard builds still work."""
    row = db.query(models.AccessCode).filter(models.AccessCode.id == code_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Access code not found")
    row.is_active = False
    db.commit()
    db.refresh(row)
    return row


@router.post("/app/login", response_model=schemas.TokenResponse)
def app_login(payload: schemas.AppLoginRequest, db: Session = Depends(get_db)):
    """Flutter app calls this with the role-based code the admin generated."""
    candidates = db.query(models.AccessCode).filter(models.AccessCode.is_active == True).all()  # noqa: E712

    matched = None
    for c in candidates:
        if verify_secret(payload.code, c.code_hash):
            matched = c
            break

    if not matched:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or inactive code")

    if matched.expires_at and matched.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This code has expired")

    matched.last_used_at = datetime.utcnow()
    db.commit()

    token = create_token(
        subject=str(matched.id),
        extra_claims={"role": matched.role, "is_admin": False, "label": matched.label},
        expires_minutes=settings.APP_TOKEN_EXPIRE_MINUTES,
    )
    return {"access_token": token, "role": matched.role, "label": matched.label}
