from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_roles
from app.features.factory_report import models, schemas
from app.features.plm.models import Plm
from app.features.fabric_inspection import models as inspection_models
from app.features.fabric_inspection.service import compute_report_stats

router = APIRouter(prefix="/factory-report", tags=["Factory Report"])


def _four_point(db: Session, plm_id: UUID):
    return (
        db.query(inspection_models.FabricInspectionReport)
        .options(joinedload(inspection_models.FabricInspectionReport.rolls))
        .filter(inspection_models.FabricInspectionReport.plm_id == plm_id)
        .first()
    )


def _derive(report: models.FactoryReport, plm: Plm, source: inspection_models.FabricInspectionReport, standard: float | None):
    stats = compute_report_stats(source)
    rolls = list(source.rolls or [])
    widths = [float(r.width_actual) for r in rolls if r.width_actual is not None and float(r.width_actual) > 0]
    colors = sorted({r.colour_code for r in rolls if r.colour_code})
    actual_length = float(stats["total_length_actual"] or 0)

    report.supplier = plm.supplier.name if plm.supplier else ""
    report.job_no = plm.plm_code
    report.construction = plm.fabric_composition
    report.color = ", ".join(colors)
    report.order_qty = plm.allocated_quantity
    # This is actual inspected quantity from saved 4-point rolls; configured check qty is
    # used only when no actual roll length has been recorded yet.
    report.inspection_yardage = actual_length if actual_length > 0 else source.checked_qty
    report.inspection_pieces = len(rolls)
    report.actual_width = (sum(widths) / len(widths)) if widths else None
    report.total_point = float(stats["total_defect_points"] or 0)
    report.avg_shipment_point = stats["overall_percent_points"]
    report.standard_snapshot = standard
    report.is_pass = (
        float(report.avg_shipment_point) <= float(standard)
        if report.avg_shipment_point is not None and standard is not None
        else None
    )
    report.inspection_date = source.inspection_date
    report.inspection_stage = "4-Point Fabric Inspection"
    if report.inspection_yardage is not None and report.order_qty is not None and float(report.order_qty) > 0:
        report.inspection_percentage = (float(report.inspection_yardage) / float(report.order_qty)) * 100
    else:
        report.inspection_percentage = None


def _ensure_derived(db: Session, plm: Plm):
    source = _four_point(db, plm.id)
    if not source:
        return None
    standard = float(source.average_standard_snapshot) if source.average_standard_snapshot is not None else None
    report = db.query(models.FactoryReport).filter(models.FactoryReport.plm_id == plm.id).first()
    if not report:
        report = models.FactoryReport(plm_id=plm.id)
        db.add(report)
        db.flush()
    _derive(report, plm, source, standard)
    db.flush()
    return report


@router.post("", response_model=schemas.FactoryReportOut)
def create_or_update_report(
    payload: schemas.FactoryReportCreate,
    db: Session = Depends(get_db),
    _=Depends(require_roles("inspection", "worker")),
):
    """Compatibility endpoint; Factory Report is derived from the PLM's 4-point report."""
    plm = db.query(Plm).get(payload.plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    report = _ensure_derived(db, plm)
    if not report:
        raise HTTPException(status_code=422, detail="Complete at least one 4-point inspection roll before viewing Factory Report")
    db.commit()
    db.refresh(report)
    return report


@router.get("", response_model=List[schemas.FactoryReportOut])
def list_reports(plm_id: Optional[UUID] = None, db: Session = Depends(get_db)):
    if plm_id:
        plm = db.query(Plm).get(plm_id)
        if not plm:
            return []
        report = _ensure_derived(db, plm)
        if not report:
            return []
        db.commit()
        db.refresh(report)
        return [report]

    # Refresh reports that have a 4-point source before returning global list.
    for plm in db.query(Plm).all():
        try:
            _ensure_derived(db, plm)
        except ValueError:
            # PLMs with incomplete historical QC config should not break the list.
            continue
    db.commit()
    return db.query(models.FactoryReport).order_by(models.FactoryReport.created_at.desc()).all()


@router.get("/{report_id}", response_model=schemas.FactoryReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = db.query(models.FactoryReport).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    plm = db.query(Plm).get(report.plm_id)
    if plm:
        refreshed = _ensure_derived(db, plm)
        if refreshed:
            db.commit()
            db.refresh(refreshed)
            report = refreshed
    return report
