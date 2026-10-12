from datetime import date, datetime, time
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_admin
from app.core.list_query import paginate
from app.features.audit import models, schemas

router = APIRouter(prefix="/audit-logs", tags=["Audit Log"])


@router.get("", response_model=List[schemas.AuditLogOut])
def list_audit_logs(
    response: Response,
    entity_type: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    plm_id: Optional[UUID] = None,
    action: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: Optional[int] = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _=Depends(require_admin()),
):
    q = db.query(models.AuditLog)
    if entity_type:
        q = q.filter(models.AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.filter(models.AuditLog.entity_id == entity_id)
    if plm_id:
        q = q.filter(models.AuditLog.plm_id == plm_id)
    if action:
        q = q.filter(models.AuditLog.action == action)
    if date_from:
        q = q.filter(models.AuditLog.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.filter(models.AuditLog.created_at <= datetime.combine(date_to, time.max))
    q = q.order_by(models.AuditLog.created_at.desc())
    return paginate(q, response, limit, offset, default=50, maximum=500).all()
