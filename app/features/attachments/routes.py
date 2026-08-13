from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, CurrentUser
from app.core.storage import storage, new_key
from app.features.attachments import models, schemas

router = APIRouter(prefix="/attachments", tags=["Attachments (photos - any module)"])


@router.post("", response_model=schemas.AttachmentOut)
async def upload_attachment(
    owner_type: str = Form(..., description="e.g. gsm_roll, skew_roll, fabric_inspection_roll..."),
    owner_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    content = await file.read()
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
        .all()
    )
