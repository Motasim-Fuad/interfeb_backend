from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from app.core.sync import client_stamp, is_stale, mark_stale, retry_on_conflict
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.list_query import bound_unscoped
from app.core.deps import require_roles
from app.features.shade_check import models, schemas
from app.features.inspection_report.service import upsert_inspection_report
from app.features.attachments.service import attach_photos_bulk, attach_stage_photos, get_attachments
from app.features.admin_config.models import ShadeCheckPointDefinition
from app.features.admin_config.service import ensure_default_shade_points
from app.features.plm.models import Plm

router = APIRouter(prefix="/shade-check", tags=["Shade Check"])


def _photos(db, row):
    row.attachments = get_attachments(db, "shade_check_roll", row.id)
    return row


def _hydrate(db, report):
    attach_photos_bulk(db, "shade_check_roll", report.rolls)
    attach_stage_photos(db, report, report_type="shade_check_report")
    return report


def _points_list_for_plm(db: Session, plm: Plm) -> list[dict]:
    qc = plm.qc_config or {}
    section = qc.get("shade_check") if isinstance(qc, dict) else {}
    points = section.get("points") if isinstance(section, dict) else []
    return [dict(raw) for raw in points if isinstance(raw, dict)] if isinstance(points, list) else []


def _snapshot_points(db: Session, report: models.ShadeCheckReport, plm: Plm) -> dict[str, dict]:
    points = report.points_snapshot or _points_list_for_plm(db, plm)
    out = {}
    for raw in points if isinstance(points, list) else []:
        if not isinstance(raw, dict):
            continue
        point_id = str(raw.get("id") or "")
        if point_id:
            out[point_id] = raw
    return out


@router.post("", response_model=schemas.ReportOut)
@retry_on_conflict
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    existing = db.query(models.ShadeCheckReport).options(
        joinedload(models.ShadeCheckReport.rolls).joinedload(models.ShadeCheckRoll.results)
    ).filter(models.ShadeCheckReport.plm_id == payload.plm_id).first()
    if existing:
        return _hydrate(db, existing)
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    points = _points_list_for_plm(db, plm)
    if not points:
        points = ensure_default_shade_points(db)
        qc = dict(plm.qc_config or {})
        shade = dict(qc.get("shade_check") or {})
        shade["points"] = points
        qc["shade_check"] = shade
        plm.qc_config = qc
    row = models.ShadeCheckReport(
        **payload.model_dump(),
        points_snapshot=points,
        qc_setup_version_snapshot=plm.qc_setup_version or 1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _hydrate(db, row)


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(response: Response, plm_id: Optional[UUID] = None, limit: Optional[int] = None, offset: int = 0, db: Session = Depends(get_db)):
    q = db.query(models.ShadeCheckReport).options(
        joinedload(models.ShadeCheckReport.rolls)
        .joinedload(models.ShadeCheckRoll.results)
        .joinedload(models.ShadeCheckResult.point_definition)
    )
    if plm_id:
        q = q.filter(models.ShadeCheckReport.plm_id == plm_id)
    reports = bound_unscoped(q.order_by(models.ShadeCheckReport.created_at.desc()), bool(plm_id), response, limit, offset).all()
    for report in reports:
        _hydrate(db, report)
    return reports


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    row = db.query(models.ShadeCheckReport).options(
        joinedload(models.ShadeCheckReport.rolls)
        .joinedload(models.ShadeCheckRoll.results)
        .joinedload(models.ShadeCheckResult.point_definition)
    ).get(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return _hydrate(db, row)


@router.patch("/{report_id}", response_model=schemas.ReportOut)
def update_report(report_id: UUID, payload: schemas.ReportUpdate, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    report = db.query(models.ShadeCheckReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if payload.comments is not None:
        report.comments = payload.comments
    db.commit()
    db.refresh(report)
    return report


@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
@retry_on_conflict
def upsert_roll(report_id: UUID, payload: schemas.RollIn, response: Response, db: Session = Depends(get_db), _=Depends(require_roles("inspection"))):
    report = db.query(models.ShadeCheckReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    plm = db.query(Plm).get(report.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")

    allowed = _snapshot_points(db, report, plm)
    if not allowed:
        raise HTTPException(status_code=422, detail="No Shade Check setup is finalized for this consignment")

    roll = db.query(models.ShadeCheckRoll).filter(
        models.ShadeCheckRoll.report_id == report_id,
        models.ShadeCheckRoll.roll_no == payload.roll_no,
    ).first()
    if is_stale(roll, payload.client_updated_at):
        mark_stale(response)
        return _photos(db, roll)
    data = payload.model_dump(exclude={"results"})
    data["client_updated_at"] = client_stamp(payload.client_updated_at)
    if roll:
        for key, value in data.items():
            setattr(roll, key, value)
    else:
        roll = models.ShadeCheckRoll(report_id=report_id, **data)
        db.add(roll)
    db.flush()

    resolved = []
    for result in payload.results:
        point_id = str(result.point_definition_id)
        snap = allowed.get(point_id)
        if not snap:
            raise HTTPException(status_code=422, detail="Shade Check point is not part of this consignment's finalized setup")
        # FK row is kept for integrity, but active/master state is deliberately irrelevant
        # after the consignment snapshot has been finalized.
        point = db.query(ShadeCheckPointDefinition).get(result.point_definition_id)
        if not point:
            raise HTTPException(status_code=422, detail="Shade Check point definition no longer exists")
        resolved.append((result, snap))

    for old in list(roll.results):
        db.delete(old)
    db.flush()
    for result, snap in resolved:
        values = result.model_dump()
        db.add(models.ShadeCheckResult(
            roll_id=roll.id,
            **values,
            point_key_snapshot=snap.get("check_key"),
            point_label_snapshot=snap.get("label"),
            point_type_snapshot=snap.get("check_type"),
        ))

    db.flush()
    db.refresh(roll)
    upsert_inspection_report(
        db,
        plm_id=report.plm_id,
        roll_number=roll.roll_no,
        module_field="shade_check_report_id",
        module_row_id=roll.id,
    )
    db.commit()
    return _photos(db, roll)
