from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, joinedload

from app.core.sync import retry_on_conflict
from app.core.database import get_db
from app.core.deps import require_roles
from app.core.list_query import paginate
from app.features.plm.models import Plm, PlmColor, PlmRoll
from app.features.correlation import models, schemas, service
from app.features.fabric_inspection import models as inspection_models
from app.features.fabric_inspection.service import (
    resolve_defect_snapshot,
    FOUR_POINT_CONSTANT,
    width_to_inches,
    length_to_yards,
)
from app.features.inspection_report.service import upsert_inspection_report
from app.features.store.service import require_qc_float

router = APIRouter(prefix="/correlation", tags=["Fabric Correlation (Mill vs Factory)"])


def _query(db: Session):
    return db.query(models.FabricCorrelationReport).options(
        joinedload(models.FabricCorrelationReport.rolls).joinedload(models.FabricCorrelationRoll.defects)
    )


def _attach(report):
    for roll in report.rolls:
        for k, v in service.compute_roll_fields(
            roll, report.measurement_unit_snapshot, report.quantity_unit_snapshot
        ).items():
            setattr(roll, k, v)
        setattr(roll, "measurement_unit", report.measurement_unit_snapshot)
        setattr(roll, "quantity_unit", report.quantity_unit_snapshot)
    return report


def _ensure_report(db: Session, plm: Plm) -> models.FabricCorrelationReport:
    report = _query(db).filter(models.FabricCorrelationReport.plm_id == plm.id).first()
    if report:
        return report

    standard, _ = require_qc_float(db, plm, "correlation", "standard")
    scs, _ = require_qc_float(db, plm, "correlation", "scs")
    report = models.FabricCorrelationReport(
        plm_id=plm.id,
        tolerance_standard_id=None,
        tolerance_snapshot=None,
        standard_snapshot=standard,
        scs_snapshot=int(scs),
        measurement_unit_snapshot=plm.measurement_unit.value if hasattr(plm.measurement_unit, "value") else str(plm.measurement_unit),
        quantity_unit_snapshot=plm.quantity_unit,
        qc_setup_version_snapshot=plm.qc_setup_version or 1,
    )
    db.add(report)
    db.flush()
    return report


def _factory_roll_map(db: Session, plm_id: UUID):
    reports = (
        db.query(inspection_models.FabricInspectionReport)
        .options(joinedload(inspection_models.FabricInspectionReport.rolls).joinedload(inspection_models.FabricInspectionRoll.defects))
        .filter(inspection_models.FabricInspectionReport.plm_id == plm_id)
        .all()
    )
    out = {}
    for report in reports:
        for roll in report.rolls:
            out[(roll.colour_code or "", roll.roll_no)] = roll
            # Existing deployments historically treated roll_no as PLM-wide unique.
            out.setdefault(("", roll.roll_no), roll)
    return out


def _sync_from_sources(db: Session, report: models.FabricCorrelationReport, plm: Plm) -> None:
    """Build correlation rows from dashboard roll planning + factory 4-point results.

    The worker does not re-enter length/width, mill points, or factory defects. Dashboard
    owns marked dimensions + supplier/mill total points; 4-point inspection owns factory
    actual defect points. Correlation is a derived comparison table.
    """
    planned_rows = (
        db.query(PlmRoll, PlmColor)
        .join(PlmColor, PlmRoll.plm_color_id == PlmColor.id)
        .filter(PlmColor.plm_id == plm.id)
        .order_by(PlmColor.colour_code, PlmRoll.roll_no)
        .all()
    )
    factory = _factory_roll_map(db, plm.id)
    existing = {r.roll_no: r for r in report.rolls}
    live_roll_nos = set()

    for planned, color in planned_rows:
        live_roll_nos.add(planned.roll_no)
        row = existing.get(planned.roll_no)
        if row is None:
            row = models.FabricCorrelationRoll(report_id=report.id, roll_no=planned.roll_no)
            db.add(row)
            db.flush()

        source = factory.get((color.colour_code, planned.roll_no)) or factory.get(("", planned.roll_no))
        row.color_code = color.colour_code
        row.length_yards = planned.marked_length
        row.width = planned.marked_width
        row.standard = report.standard_snapshot
        row.scs = report.scs_snapshot
        row.mill_total_point = planned.supplier_total_point
        row.factory_total_points = int(source.total_points or 0) if source else 0
        row.factory_percent_points = source.percent_points if source else None
        row.factory_length = source.length_actual if source else None
        row.factory_width = source.width_actual if source else None
        row.roll_date = source.report.inspection_date if source and source.report else None

        # Mirror the factory 4-point defect snapshots so correlation history stays readable.
        # Rows are only rewritten when the source changed, so repeated GETs don't churn the table.
        def _sig(defects):
            return sorted(
                (str(d.defect_type_id), str(d.range_config_id), int(d.count or 0), int(d.points_snapshot or 0))
                for d in defects
            )

        if _sig(row.defects) == _sig(source.defects if source else []):
            continue
        for old in list(row.defects):
            db.delete(old)
        db.flush()
        if source:
            for defect in source.defects:
                db.add(models.FabricCorrelationRollDefect(
                    roll_id=row.id,
                    defect_type_id=defect.defect_type_id,
                    range_config_id=defect.range_config_id,
                    count=defect.count,
                    points_snapshot=defect.points_snapshot,
                    defect_type_name_snapshot=defect.defect_type_name_snapshot,
                    range_label_snapshot=defect.range_label_snapshot,
                ))

    # Remove stale derived rows if dashboard planning removed a roll before inspection.
    for row in list(report.rolls):
        if row.roll_no not in live_roll_nos:
            db.delete(row)

    db.flush()


def refresh_correlation_for_plm(db: Session, plm: Plm) -> models.FabricCorrelationReport:
    report = _ensure_report(db, plm)
    _sync_from_sources(db, report, plm)
    return report


@router.post("", response_model=schemas.ReportOut)
@retry_on_conflict
def create_report(payload: schemas.ReportCreate, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    report = _ensure_report(db, plm)
    _sync_from_sources(db, report, plm)
    db.commit()
    return _attach(_query(db).get(report.id))


@router.get("", response_model=List[schemas.ReportOut])
def list_reports(
    response: Response,
    plm_id: Optional[UUID] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    # A PLM-specific correlation view is derived on demand; no separate worker entry screen.
    if plm_id:
        plm = db.query(Plm).get(plm_id)
        if not plm:
            return []
        report = refresh_correlation_for_plm(db, plm)
        db.commit()
        return [_attach(_query(db).get(report.id))]

    # Global list returns stored rows; they are refreshed whenever a 4-point roll is saved.
    q = _query(db).order_by(models.FabricCorrelationReport.created_at.desc())
    return [_attach(r) for r in paginate(q, response, limit, offset, default=50, maximum=200).all()]


@router.get("/{report_id}", response_model=schemas.ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    r = _query(db).get(report_id)
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")
    plm = db.query(Plm).get(r.plm_id)
    if plm:
        _sync_from_sources(db, r, plm)
        db.commit()
        r = _query(db).get(report_id)
    return _attach(r)


# Backward-compatible manual endpoint. New app UI does not use it; derived sync on GET
# will always restore Dashboard + 4-point source-of-truth values.
@router.post("/{report_id}/rolls", response_model=schemas.RollOut)
@retry_on_conflict
def upsert_roll(report_id: UUID, payload: schemas.RollIn, db: Session = Depends(get_db), _=Depends(require_roles("inspection", "worker"))):
    report = db.query(models.FabricCorrelationReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    plm = db.query(Plm).get(report.plm_id)
    data = payload.model_dump(exclude={"defects", "standard", "scs"})
    data["standard"] = float(report.standard_snapshot) if report.standard_snapshot is not None else None
    data["scs"] = int(report.scs_snapshot) if report.scs_snapshot is not None else None
    roll = db.query(models.FabricCorrelationRoll).filter(
        models.FabricCorrelationRoll.report_id == report_id,
        models.FabricCorrelationRoll.roll_no == payload.roll_no,
    ).first()
    if roll:
        for k, v in data.items():
            setattr(roll, k, v)
    else:
        roll = models.FabricCorrelationRoll(report_id=report_id, **data)
        db.add(roll)
    db.flush()
    for old in list(roll.defects):
        db.delete(old)
    db.flush()
    total = 0
    try:
        for d in payload.defects:
            snap = resolve_defect_snapshot(db, d.defect_type_id, d.range_config_id, d.count)
            total += snap["points"]
            db.add(models.FabricCorrelationRollDefect(
                roll_id=roll.id,
                defect_type_id=d.defect_type_id,
                range_config_id=d.range_config_id,
                count=d.count,
                points_snapshot=snap["points"],
                defect_type_name_snapshot=snap["defect_type_name"],
                range_label_snapshot=snap["range_label"],
            ))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    roll.factory_total_points = total
    unit = report.measurement_unit_snapshot
    quantity_unit = report.quantity_unit_snapshot
    width_inches = width_to_inches(float(roll.width), unit) if roll.width else 0
    length_yards = length_to_yards(float(roll.length_yards), quantity_unit) if roll.length_yards else 0
    if width_inches > 0 and length_yards > 0:
        roll.factory_percent_points = (total * FOUR_POINT_CONSTANT) / (width_inches * length_yards)
    db.commit()
    db.refresh(roll)
    upsert_inspection_report(
        db,
        plm_id=report.plm_id,
        roll_number=roll.roll_no,
        module_field="correlation_report_id",
        module_row_id=roll.id,
    )
    db.commit()
    for k, v in service.compute_roll_fields(roll, unit, quantity_unit).items():
        setattr(roll, k, v)
    setattr(roll, "measurement_unit", unit)
    setattr(roll, "quantity_unit", quantity_unit)
    return roll
