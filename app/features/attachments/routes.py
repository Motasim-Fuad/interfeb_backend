from typing import List
from uuid import UUID

import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, CurrentUser
from app.core.storage import storage, new_key
from app.features.attachments import models, schemas

router = APIRouter(prefix="/attachments", tags=["Attachments (photos - any module)"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_OWNER_TYPE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _looks_like_image(content: bytes) -> bool:
    head = content[:16]
    return (
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG")
        or head.startswith(b"GIF8")
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
        or head[4:8] == b"ftyp"
    )


@router.post("", response_model=schemas.AttachmentOut)
async def upload_attachment(
    owner_type: str = Form(..., description="e.g. gsm_roll, skew_roll, fabric_inspection_roll..."),
    owner_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if not _OWNER_TYPE.match(owner_type or ""):
        raise HTTPException(status_code=422, detail="Invalid owner_type")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than 10 MB")
    if not content:
        raise HTTPException(status_code=422, detail="Empty file")
    if not _looks_like_image(content):
        raise HTTPException(status_code=415, detail="Only image uploads are allowed")
    key = new_key(folder=owner_type, filename=file.filename)
    storage.save(content, key)
    url = storage.url_for(key)

    row = models.Attachment(
        owner_type=owner_type, owner_id=owner_id,
        storage_key=key, file_url=url, uploaded_by=user.label or user.role,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/by-owner/{owner_type}/{owner_id}", response_model=List[schemas.AttachmentOut])
def list_for_owner(owner_type: str, owner_id: UUID, db: Session = Depends(get_db)):
    return (
        db.query(models.Attachment)
        .filter(models.Attachment.owner_type == owner_type, models.Attachment.owner_id == owner_id)
        .order_by(models.Attachment.created_at)
        .limit(200)
        .all()
    )
