from calendar import monthrange
from datetime import date
from typing import Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from app.features.admin_config.models import Buyer, Supplier
from app.features.analytics.models import SupplierMonthlyInput
from app.features.fabric_inspection.models import FabricInspectionRoll, FabricInspectionRollDefect, FabricInspectionReport
from app.features.plm.models import Plm, PlmColor, PlmRoll
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


def _quality_rows(db: Session, party, join_on, *, buyer_id=None, supplier_id=None, date_from=None, date_to=None):
    q = (
        db.query(
            party.id,
            party.name,
            func.count(FabricInspectionRoll.id),
            func.sum(case((FabricInspectionRoll.is_pass.is_(True), 1), else_=0)),
            func.sum(case((FabricInspectionRoll.is_pass.is_(False), 1), else_=0)),
        )
        .select_from(FabricInspectionRoll)
        .join(FabricInspectionReport, FabricInspectionReport.id == FabricInspectionRoll.report_id)
        .join(Plm, Plm.id == FabricInspectionReport.plm_id)
        .join(party, party.id == join_on)
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if date_from:
        q = q.filter(FabricInspectionReport.inspection_date >= date_from)
    if date_to:
        q = q.filter(FabricInspectionReport.inspection_date <= date_to)
    out = []
    for _, name, total, passed, failed in q.group_by(party.id, party.name).all():
        total = int(total or 0)
        out.append({
            "name": name,
            "total": total,
            "passed": int(passed or 0),
            "failed": int(failed or 0),
            "pass_rate": round(int(passed or 0) / total * 100, 1) if total else None,
        })
    out.sort(key=lambda r: (r["pass_rate"] is None, -(r["pass_rate"] or 0), r["name"]))
    return out


def supplier_quality_rows(db: Session, *, buyer_id=None, supplier_id=None, date_from=None, date_to=None):
    return _quality_rows(db, Supplier, Plm.supplier_id, buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to)


def buyer_quality_rows(db: Session, *, buyer_id=None, supplier_id=None, date_from=None, date_to=None):
    return _quality_rows(db, Buyer, Plm.buyer_id, buyer_id=buyer_id, supplier_id=supplier_id, date_from=date_from, date_to=date_to)


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

    party = Supplier if group == "supplier" else Buyer
    join_on = Plm.supplier_id if group == "supplier" else Plm.buyer_id
    roll_counts = (
        db.query(PlmColor.plm_id.label("plm_id"), func.count(PlmRoll.id).label("rolls"))
        .join(PlmRoll, PlmRoll.plm_color_id == PlmColor.id)
        .group_by(PlmColor.plm_id)
        .subquery()
    )
    q = (
        db.query(
            party,
            func.count(Plm.id),
            func.coalesce(func.sum(Plm.allocated_quantity), 0),
            func.coalesce(func.sum(roll_counts.c.rolls), 0),
        )
        .select_from(Plm)
        .join(party, party.id == join_on)
        .outerjoin(roll_counts, roll_counts.c.plm_id == Plm.id)
    )
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if date_from:
        q = q.filter(Plm.plm_date >= date_from)
    if date_to:
        q = q.filter(Plm.plm_date <= date_to)
    for party_obj, plms, allocated, rolls in q.group_by(party.id).all():
        row = party_row(party_obj)
        row["plms"] += int(plms or 0)
        row["allocated"] += float(allocated or 0)
        row["rolls"] += int(rolls or 0)
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
