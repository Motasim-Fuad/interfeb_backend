from typing import List, Optional
from uuid import UUID

import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import CurrentUser, require_roles
from app.core.storage import storage, new_key
from app.features.attachments import models, schemas

router = APIRouter(prefix="/attachments", tags=["Attachments (photos - any module)"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_OWNER_TYPE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


_HEIF_BRANDS = {b"heic": "heic", b"heix": "heic", b"hevc": "heic", b"heim": "heic", b"heis": "heic",
                b"mif1": "heif", b"msf1": "heif", b"avif": "avif", b"avis": "avif"}


def _image_ext(content: bytes) -> Optional[str]:
    """Extension from the file's own bytes; the client's filename is never trusted."""
    head = content[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        return _HEIF_BRANDS.get(head[8:12])
    return None


@router.post("", response_model=schemas.AttachmentOut)
async def upload_attachment(
    owner_type: str = Form(..., description="e.g. gsm_roll, skew_roll, fabric_inspection_roll..."),
    owner_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("worker", "inspection")),
):
    if not _OWNER_TYPE.match(owner_type or ""):
        raise HTTPException(status_code=422, detail="Invalid owner_type")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than 10 MB")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than 10 MB")
    if not content:
        raise HTTPException(status_code=422, detail="Empty file")
    ext = _image_ext(content)
    if ext is None:
        raise HTTPException(status_code=415, detail="Only image uploads are allowed")
    key = new_key(folder=owner_type, filename=f"upload.{ext}")
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
