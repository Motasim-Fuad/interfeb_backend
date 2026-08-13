"""
Prevents two people editing the same roll at the same time. Backed by Redis (not Postgres) -
locks are short-lived (TTL) and don't need to survive server restarts or be queried relationally.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.redis_client import redis_client
from app.core.deps import get_current_user, CurrentUser
from app.features.roll_lock.schemas import LockRequest, LockStatus

router = APIRouter(prefix="/roll-lock", tags=["Roll Lock (concurrency)"])


def _key(payload: LockRequest) -> str:
    return f"rolllock:{payload.module}:{payload.plm_id}:{payload.roll_no}"


@router.post("/acquire", response_model=LockStatus)
def acquire_lock(payload: LockRequest, user: CurrentUser = Depends(get_current_user)):
    key = _key(payload)
    holder = redis_client.get(key)

    if holder and holder != user.label:
        raise HTTPException(status_code=409, detail=f"Roll is currently being edited by {holder}")

    redis_client.set(key, user.label or user.role, ex=settings.ROLL_LOCK_TTL_SECONDS)
    return {"locked": True, "locked_by": user.label or user.role}


@router.post("/release", response_model=LockStatus)
def release_lock(payload: LockRequest, user: CurrentUser = Depends(get_current_user)):
    redis_client.delete(_key(payload))
    return {"locked": False, "locked_by": None}


@router.get("/status", response_model=LockStatus)
def lock_status(module: str, plm_id: str, roll_no: str):
    key = f"rolllock:{module}:{plm_id}:{roll_no}"
    holder = redis_client.get(key)
    return {"locked": bool(holder), "locked_by": holder}
