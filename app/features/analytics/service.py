from calendar import monthrange
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.features.admin_config.models import Buyer, Supplier
from app.features.analytics.models import SupplierMonthlyInput
from app.features.fabric_inspection.models import FabricInspectionRoll, FabricInspectionRollDefect, FabricInspectionReport
from app.features.plm.models import Plm, PlmColor
from app.features.store.models import FabricReceive


def month_bounds(year: Optional[int], month: Optional[int]) -> tuple[Optional[date], Optional[date]]:
    if not year:
        return None, None
    if month:
        last = monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last)
    return date(year, 1, 1), date(year, 12, 31)


def period_label(year: Optional[int], month: Optional[int]) -> str:
    if year and month:
        return date(year, month, 1).strftime("%B %Y")
    if year:
        return str(year)
    return "All periods"


def supplier_quality_rows(db: Session, *, buyer_id=None, supplier_id=None, date_from=None, date_to=None):
    q = (
        db.query(FabricInspectionRoll, Plm, Supplier)
        .join(FabricInspectionReport, FabricInspectionReport.id == FabricInspectionRoll.report_id)
        .join(Plm, Plm.id == FabricInspectionReport.plm_id)
        .join(Supplier, Supplier.id == Plm.supplier_id)
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if date_from:
        q = q.filter(FabricInspectionReport.inspection_date >= date_from)
    if date_to:
        q = q.filter(FabricInspectionReport.inspection_date <= date_to)
    grouped = {}
    for roll, plm, supplier in q.all():
        row = grouped.setdefault(str(supplier.id), {"name": supplier.name, "total": 0, "passed": 0, "failed": 0})
        row["total"] += 1
        if roll.is_pass is True:
            row["passed"] += 1
        elif roll.is_pass is False:
            row["failed"] += 1
    out = []
    for item in grouped.values():
        total = item["total"]
        item["pass_rate"] = round(item["passed"] / total * 100, 1) if total else None
        out.append(item)
    out.sort(key=lambda r: (r["pass_rate"] is None, -(r["pass_rate"] or 0), r["name"]))
    return out


def buyer_quality_rows(db: Session, *, buyer_id=None, supplier_id=None, date_from=None, date_to=None):
    q = (
        db.query(FabricInspectionRoll, Plm, Buyer)
        .join(FabricInspectionReport, FabricInspectionReport.id == FabricInspectionRoll.report_id)
        .join(Plm, Plm.id == FabricInspectionReport.plm_id)
        .join(Buyer, Buyer.id == Plm.buyer_id)
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if date_from:
        q = q.filter(FabricInspectionReport.inspection_date >= date_from)
    if date_to:
        q = q.filter(FabricInspectionReport.inspection_date <= date_to)
    grouped = {}
    for roll, plm, buyer in q.all():
        row = grouped.setdefault(str(buyer.id), {"name": buyer.name, "total": 0, "passed": 0, "failed": 0})
        row["total"] += 1
        if roll.is_pass is True:
            row["passed"] += 1
        elif roll.is_pass is False:
            row["failed"] += 1
    out = []
    for item in grouped.values():
        total = item["total"]
        item["pass_rate"] = round(item["passed"] / total * 100, 1) if total else None
        out.append(item)
    out.sort(key=lambda r: (r["pass_rate"] is None, -(r["pass_rate"] or 0), r["name"]))
    return out


def defect_rows(db: Session, *, group: str, buyer_id=None, supplier_id=None, date_from=None, date_to=None):
    name_col = Supplier.name if group == "supplier" else Buyer.name
    id_col = Supplier.id if group == "supplier" else Buyer.id
    join_model = Supplier if group == "supplier" else Buyer
    join_on = Plm.supplier_id if group == "supplier" else Plm.buyer_id
    q = (
        db.query(
            id_col.label("id"),
            name_col.label("name"),
            func.coalesce(func.sum(FabricInspectionRollDefect.count), 0).label("total_count"),
            func.coalesce(func.sum(FabricInspectionRollDefect.points_snapshot), 0).label("total_points"),
        )
        .join(Plm, join_on == join_model.id)
        .join(FabricInspectionReport, FabricInspectionReport.plm_id == Plm.id)
        .join(FabricInspectionRoll, FabricInspectionRoll.report_id == FabricInspectionReport.id)
        .join(FabricInspectionRollDefect, FabricInspectionRollDefect.roll_id == FabricInspectionRoll.id)
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if date_from:
        q = q.filter(FabricInspectionReport.inspection_date >= date_from)
    if date_to:
        q = q.filter(FabricInspectionReport.inspection_date <= date_to)
    rows = q.group_by(id_col, name_col).order_by(func.coalesce(func.sum(FabricInspectionRollDefect.points_snapshot), 0).desc()).all()
    return [{"name": r.name, "total_count": int(r.total_count or 0), "total_points": int(r.total_points or 0)} for r in rows]


def quantity_rows(db: Session, *, group: str, buyer_id=None, supplier_id=None, date_from=None, date_to=None):
    """Received = unique consignment lots. Allocated = factory PLM assignments.

    One consignment can feed many PLMs, so received is counted once per
    fabric_receive row. Unallocated lots still appear with allocated = 0.
    """
    grouped = {}

    def party_row(party):
        if party is None:
            return None
        return grouped.setdefault(str(party.id), {
            "name": party.name,
            "plms": 0,
            "allocated": 0.0,
            "received": 0.0,
            "rolls": 0,
        })

    receives = db.query(FabricReceive).options(
        joinedload(FabricReceive.buyer),
        joinedload(FabricReceive.supplier),
    )
    if buyer_id:
        receives = receives.filter(FabricReceive.buyer_id == buyer_id)
    if supplier_id:
        receives = receives.filter(FabricReceive.supplier_id == supplier_id)
    if date_from:
        receives = receives.filter(FabricReceive.received_at >= date_from)
    if date_to:
        receives = receives.filter(FabricReceive.received_at <= date_to)
    for rec in receives.all():
        row = party_row(rec.supplier if group == "supplier" else rec.buyer)
        if row is None:
            continue
        qty = rec.received_quantity if rec.received_quantity is not None else rec.fabric_length
        row["received"] += float(qty or 0)

    q = db.query(Plm).options(
        joinedload(Plm.buyer),
        joinedload(Plm.supplier),
        joinedload(Plm.colors).joinedload(PlmColor.rolls),
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if date_from:
        q = q.filter(Plm.plm_date >= date_from)
    if date_to:
        q = q.filter(Plm.plm_date <= date_to)
    for plm in q.all():
        row = party_row(plm.supplier if group == "supplier" else plm.buyer)
        if row is None:
            continue
        row["plms"] += 1
        row["allocated"] += float(plm.allocated_quantity or 0)
        row["rolls"] += sum(len(c.rolls) for c in (plm.colors or []))
    out = list(grouped.values())
    out.sort(key=lambda r: -(r["allocated"] or r["received"]))
    return out


def communication_rows(db: Session, *, year: Optional[int], month: Optional[int], supplier_id=None):
    q = db.query(SupplierMonthlyInput).options(joinedload(SupplierMonthlyInput.supplier))
    if year:
        q = q.filter(SupplierMonthlyInput.year == year)
    if month:
        q = q.filter(SupplierMonthlyInput.month == month)
    if supplier_id:
        q = q.filter(SupplierMonthlyInput.supplier_id == supplier_id)
    rows = []
    for item in q.order_by(SupplierMonthlyInput.year.desc(), SupplierMonthlyInput.month.desc()).all():
        rows.append({
            "name": item.supplier.name if item.supplier else "",
            "period": f"{item.month:02d}/{item.year}",
            "rating": float(item.communication_rating) if item.communication_rating is not None else None,
            "notes": item.notes or "",
        })
    rows.sort(key=lambda r: (-(r["rating"] or 0), r["name"]))
    return rows


def monthly_dashboard(db: Session, *, year: int, month: int, buyer_id=None, supplier_id=None):
    date_from, date_to = month_bounds(year, month)
    return {
        "quality": supplier_quality_rows(db, buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "defects": defect_rows(db, group="supplier", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "quantity": quantity_rows(db, group="supplier", buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to),
        "communication": communication_rows(db, year=year, month=month, supplier_id=supplier_id),
    }
