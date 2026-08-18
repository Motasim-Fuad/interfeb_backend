from datetime import date
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel

from app.features.attachments.schemas import AttachmentOut


class ResultIn(BaseModel):
    point_definition_id: UUID
    first_ok: Optional[bool] = None
    middle_ok: Optional[bool] = None
    last_ok: Optional[bool] = None


class ResultOut(ResultIn):
    id: UUID
    point_key: Optional[str] = None
    point_label: Optional[str] = None
    point_type: Optional[str] = None
    class Config: from_attributes = True


class RollIn(BaseModel):
    roll_no: str
    colour: Optional[str] = None
    cons: Optional[float] = None
    roll_date: Optional[date] = None
    remarks: Optional[str] = None
    results: List[ResultIn] = []


class RollOut(BaseModel):
    id: UUID
    roll_no: str
    colour: Optional[str] = None
    cons: Optional[float] = None
    remarks: Optional[str] = None
    results: List[ResultOut] = []
    attachments: List[AttachmentOut] = []
    class Config: from_attributes = True


class ReportCreate(BaseModel):
    plm_id: UUID
    comments: Optional[str] = None


class ReportUpdate(BaseModel):
    comments: Optional[str] = None


class ReportOut(BaseModel):
    id: UUID
    plm_id: UUID
    comments: Optional[str] = None
    points_snapshot: List[Dict[str, Any]] = []
    qc_setup_version_snapshot: int = 1
    rolls: List[RollOut] = []
    attachments: List[AttachmentOut] = []
    class Config: from_attributes = True
