"""
Read-only aggregation endpoints - computed live from the existing tables via query params,
not stored anywhere separately, so they're always in sync with live data.

Drill-down hierarchy: Supplier -> PLM -> Defect type/roll, matching the reporting flow
agreed on - "which supplier has the most defects, which of their PLMs, which defects
within that PLM."
"""
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import require_admin
from app.core.list_query import apply_page
from app.features.admin_config.models import Buyer, DefectType, Supplier
from app.features.analytics.models import SupplierMonthlyInput
from app.features.fabric_inspection.models import FabricInspectionRoll, FabricInspectionRollDefect, FabricInspectionReport
from app.features.plm.models import Plm
from app.features.store.models import FabricReceive
from app.features.inspection_report.models import InspectionReport
from app.features.factory_report.models import FactoryReport
from app.features.analytics import schemas

router = APIRouter(prefix="/analytics", tags=["Analytics / Ranking"])


def _base_defect_query(db: Session):
    return (
        db.query(FabricInspectionRollDefect, FabricInspectionRoll, FabricInspectionReport, Plm)
        .join(FabricInspectionRoll, FabricInspectionRoll.id == FabricInspectionRollDefect.roll_id)
        .join(FabricInspectionReport, FabricInspectionReport.id == FabricInspectionRoll.report_id)
        .join(Plm, Plm.id == FabricInspectionReport.plm_id)
    )


@router.get("/supplier-ranking", response_model=List[schemas.SupplierRankItem])
def supplier_ranking(
    buyer_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Top level of the drill-down: which supplier has the most defect points overall."""
    q = (
        db.query(
            Supplier.id.label("supplier_id"),
            Supplier.name.label("supplier_name"),
            func.sum(FabricInspectionRollDefect.count).label("total_count"),
            func.sum(FabricInspectionRollDefect.points_snapshot).label("total_points"),
        )
        .join(Plm, Plm.supplier_id == Supplier.id)
        .join(FabricInspectionReport, FabricInspectionReport.plm_id == Plm.id)
        .join(FabricInspectionRoll, FabricInspectionRoll.report_id == FabricInspectionReport.id)
        .join(FabricInspectionRollDefect, FabricInspectionRollDefect.roll_id == FabricInspectionRoll.id)
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if date_from:
        q = q.filter(FabricInspectionReport.inspection_date >= date_from)
    if date_to:
        q = q.filter(FabricInspectionReport.inspection_date <= date_to)

    q = q.group_by(Supplier.id, Supplier.name).order_by(func.sum(FabricInspectionRollDefect.points_snapshot).desc())
    return q.all()


@router.get("/plm-ranking", response_model=List[schemas.PlmRankItem])
def plm_ranking(
    supplier_id: UUID,
    db: Session = Depends(get_db),
):
    """Second level: within one supplier, which PLM has the most defect points."""
    q = (
        db.query(
            Plm.id.label("plm_id"),
            Plm.plm_code.label("plm_code"),
            func.sum(FabricInspectionRollDefect.count).label("total_count"),
            func.sum(FabricInspectionRollDefect.points_snapshot).label("total_points"),
        )
        .join(FabricInspectionReport, FabricInspectionReport.plm_id == Plm.id)
        .join(FabricInspectionRoll, FabricInspectionRoll.report_id == FabricInspectionReport.id)
        .join(FabricInspectionRollDefect, FabricInspectionRollDefect.roll_id == FabricInspectionRoll.id)
        .filter(Plm.supplier_id == supplier_id)
        .group_by(Plm.id, Plm.plm_code)
        .order_by(func.sum(FabricInspectionRollDefect.points_snapshot).desc())
    )
    return q.all()


@router.get("/defect-ranking", response_model=List[schemas.DefectRankItem])
def defect_ranking(
    supplier_id: Optional[UUID] = None,
    buyer_id: Optional[UUID] = None,
    plm_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """
    Third level: which defect type dominates - scope with supplier_id + buyer_id + date range
    for an overview, or with plm_id alone to see one specific PLM's defect breakdown.
    """
    q = (
        db.query(
            DefectType.id.label("defect_type_id"),
            DefectType.name.label("defect_type_name"),
            func.sum(FabricInspectionRollDefect.count).label("total_count"),
            func.sum(FabricInspectionRollDefect.points_snapshot).label("total_points"),
        )
        .join(FabricInspectionRollDefect, FabricInspectionRollDefect.defect_type_id == DefectType.id)
        .join(FabricInspectionRoll, FabricInspectionRoll.id == FabricInspectionRollDefect.roll_id)
        .join(FabricInspectionReport, FabricInspectionReport.id == FabricInspectionRoll.report_id)
        .join(Plm, Plm.id == FabricInspectionReport.plm_id)
    )

    if plm_id:
        q = q.filter(Plm.id == plm_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if date_from:
        q = q.filter(FabricInspectionReport.inspection_date >= date_from)
    if date_to:
        q = q.filter(FabricInspectionReport.inspection_date <= date_to)

    q = q.group_by(DefectType.id, DefectType.name).order_by(func.sum(FabricInspectionRollDefect.points_snapshot).desc())
    return q.all()


@router.get("/pass-fail-summary", response_model=schemas.PassFailSummary)
def pass_fail_summary(
    plm_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Fabric Inspection pass/fail rate - same pattern extends to any other module's rolls."""
    q = db.query(FabricInspectionRoll).join(FabricInspectionReport).join(Plm, Plm.id == FabricInspectionReport.plm_id)
    if plm_id:
        q = q.filter(FabricInspectionReport.plm_id == plm_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)

    rolls = q.all()
    total = len(rolls)
    passed = sum(1 for r in rolls if r.is_pass is True)
    failed = sum(1 for r in rolls if r.is_pass is False)

    return {
        "module": "fabric_inspection",
        "total_rolls": total,
        "passed": passed,
        "failed": failed,
        "pass_rate_percent": round((passed / total) * 100, 2) if total else None,
    }


@router.get("/inspection-rolls", response_model=schemas.InspectionRollPage)
def inspection_rolls(
    buyer_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    fabric_receive_id: Optional[UUID] = None,
    consignment: Optional[str] = None,
    plm_id: Optional[UUID] = None,
    plm_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    search: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    Dashboard reporting source for all current roll-level inspection modules.
    Context is joined from PLM -> Fabric Receive -> Buyer/Supplier, never duplicated.
    Filters support old-data review by buyer, supplier, consignment, PLM and month/date.
    """
    from app.features.analytics.service import month_bounds
    q = (
        db.query(InspectionReport, Plm, FabricReceive, Buyer, Supplier)
        .join(Plm, Plm.id == InspectionReport.plm_id)
        .outerjoin(FabricReceive, FabricReceive.id == Plm.fabric_receive_id)
        .outerjoin(Buyer, Buyer.id == Plm.buyer_id)
        .outerjoin(Supplier, Supplier.id == Plm.supplier_id)
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if fabric_receive_id:
        q = q.filter(Plm.fabric_receive_id == fabric_receive_id)
    if consignment:
        q = q.filter(func.lower(FabricReceive.consignment).contains(consignment.lower().strip()))
    if plm_id:
        q = q.filter(Plm.id == plm_id)
    if plm_code:
        q = q.filter(func.lower(Plm.plm_code).contains(plm_code.lower().strip()))
    if search:
        term = search.strip().lower()
        q = q.filter(
            func.lower(Plm.plm_code).contains(term)
            | func.lower(func.coalesce(FabricReceive.consignment, "")).contains(term)
            | func.lower(func.coalesce(Buyer.name, "")).contains(term)
            | func.lower(func.coalesce(Supplier.name, "")).contains(term)
        )
    if year and not date_from:
        date_from, date_to = month_bounds(year, month)
    if date_from:
        q = q.filter(func.date(InspectionReport.submitted_at) >= date_from)
    if date_to:
        q = q.filter(func.date(InspectionReport.submitted_at) <= date_to)

    q = q.order_by(InspectionReport.submitted_at.desc(), Plm.plm_code, InspectionReport.roll_number)
    total_matching = q.count()
    q = apply_page(q, limit=limit, offset=offset)
    records = q.all()
    plm_ids = {plm.id for _, plm, _, _, _ in records}
    factory_plm_ids = set()
    if plm_ids:
        factory_plm_ids = {row[0] for row in db.query(FactoryReport.plm_id).filter(FactoryReport.plm_id.in_(plm_ids)).distinct().all()}
    rows = []
    for master, plm, receive, buyer, supplier in records:
        flags = {
            "gsm": master.gsm_report_id is not None,
            "moisture": master.moisture_report_id is not None,
            "skew_bowing": master.skew_report_id is not None,
            "relaxation": master.relaxation_report_id is not None,
            "shade_check": master.shade_check_report_id is not None,
            "shrinkage": master.shrinkage_report_id is not None,
            "correlation": master.correlation_report_id is not None,
            "color_continuity": master.color_continuity_card_id is not None,
            "fabric_inspection": master.fabric_inspection_roll_id is not None,
            "factory_report": master.factory_report_id is not None or plm.id in factory_plm_ids,
        }
        rows.append({
            "inspection_report_id": master.id,
            "submitted_at": master.submitted_at,
            "buyer_id": plm.buyer_id,
            "buyer_name": buyer.name if buyer else "",
            "supplier_id": plm.supplier_id,
            "supplier_name": supplier.name if supplier else "",
            "fabric_receive_id": plm.fabric_receive_id,
            "consignment": receive.consignment if receive else "",
            "plm_id": plm.id,
            "plm_code": plm.plm_code,
            "plm_style": plm.plm_style,
            "color_name": master.color_name,
            "roll_number": master.roll_number,
            "measurement_unit": plm.measurement_unit or (receive.measurement_unit if receive else "inch") or "inch",
            **flags,
            "completed_modules": sum(1 for value in flags.values() if value),
            "total_modules": len(flags),
        })
    completed = sum(1 for row in rows if row["completed_modules"] == row["total_modules"])
    return {
        "total": total_matching,
        "completed_rolls": completed,
        "in_progress_rolls": len(rows) - completed,
        "rows": rows,
    }


@router.get("/analysis-overview", response_model=schemas.AnalysisOverview)
def analysis_overview(
    year: Optional[int] = None,
    month: Optional[int] = None,
    buyer_id: Optional[UUID] = None,
    supplier_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Chart source for the Analytics page: quality, defects and quantity in one payload."""
    from app.features.analytics.service import month_bounds, supplier_quality_rows, buyer_quality_rows, defect_rows, quantity_rows
    date_from, date_to = month_bounds(year, month)
    return {
        "supplier_quality": supplier_quality_rows(db, buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "buyer_quality": buyer_quality_rows(db, buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "supplier_defects": defect_rows(db, group="supplier", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "buyer_defects": defect_rows(db, group="buyer", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "supplier_quantity": quantity_rows(db, group="supplier", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "buyer_quantity": quantity_rows(db, group="buyer", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
    }


@router.get("/supplier-monthly-inputs", response_model=List[schemas.SupplierMonthlyInputOut])
def list_monthly_inputs(year: int, month: int, db: Session = Depends(get_db)):
    rows = (
        db.query(SupplierMonthlyInput)
        .options(joinedload(SupplierMonthlyInput.supplier))
        .filter(SupplierMonthlyInput.year == year, SupplierMonthlyInput.month == month)
        .all()
    )
    return [
        schemas.SupplierMonthlyInputOut(
            id=row.id,
            supplier_id=row.supplier_id,
            year=row.year,
            month=row.month,
            communication_rating=float(row.communication_rating) if row.communication_rating is not None else None,
            notes=row.notes,
            supplier_name=row.supplier.name if row.supplier else "",
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.put("/supplier-monthly-inputs", response_model=schemas.SupplierMonthlyInputOut)
def upsert_monthly_input(payload: schemas.SupplierMonthlyInputIn, db: Session = Depends(get_db), _=Depends(require_admin())):
    if payload.month < 1 or payload.month > 12:
        raise HTTPException(status_code=422, detail="month must be 1-12")
    row = (
        db.query(SupplierMonthlyInput)
        .filter(
            SupplierMonthlyInput.supplier_id == payload.supplier_id,
            SupplierMonthlyInput.year == payload.year,
            SupplierMonthlyInput.month == payload.month,
        )
        .first()
    )
    if row is None:
        row = SupplierMonthlyInput(**payload.model_dump())
        db.add(row)
    else:
        row.communication_rating = payload.communication_rating
        row.notes = payload.notes
    db.commit()
    db.refresh(row)
    supplier = db.query(Supplier).get(row.supplier_id)
    return schemas.SupplierMonthlyInputOut(
        id=row.id,
        supplier_id=row.supplier_id,
        year=row.year,
        month=row.month,
        communication_rating=float(row.communication_rating) if row.communication_rating is not None else None,
        notes=row.notes,
        supplier_name=supplier.name if supplier else "",
        created_at=row.created_at,
    )
