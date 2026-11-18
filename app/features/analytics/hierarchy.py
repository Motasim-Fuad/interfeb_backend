"""Parent -> child reporting for the Dashboard Analysis Report.

Supplier (or Buyer) -> Consignment -> PLM -> Roll, plus the monthly Mill vs Factory
correlation reports (supplier-wise and fabric-type-wise). Every number is computed live
from PLM roll planning (mill side) and the saved 4-point rolls (factory side).

Dates: the PLM date anchors every period filter, so a PLM and all its rolls always land
in the same week / month / year.
"""
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.clock import factory_today
from app.core.database import get_db
from app.features.admin_config.models import Buyer, Supplier
from app.features.analytics.service import month_bounds
from app.features.fabric_inspection.models import FabricInspectionReport, FabricInspectionRoll
from app.features.fabric_inspection.service import FOUR_POINT_CONSTANT, length_to_yards, width_to_inches
from app.features.plm.models import Plm, PlmColor, PlmRoll
from app.features.store.models import FabricReceive

router = APIRouter(prefix="/analytics", tags=["Analytics / Hierarchy reports"])

PERIODS = ("recent", "week", "month", "year", "all")


def resolve_period(period: str, year: Optional[int], month: Optional[int],
                   date_from: Optional[date], date_to: Optional[date]) -> tuple[Optional[date], Optional[date]]:
    """recent = last 30 days, week = this week (Mon-today), month/year = picked or current."""
    if date_from or date_to:
        return date_from, date_to
    today = factory_today()
    if period == "recent":
        return today - timedelta(days=29), today
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    if period == "month":
        return month_bounds(year or today.year, month or today.month)
    if period == "year":
        return month_bounds(year or today.year, None)
    return None, None


def _unit(plm) -> str:
    unit = plm.measurement_unit
    return unit.value if hasattr(unit, "value") else str(unit or "inch")


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _qc(plm, module: str, key: str) -> Optional[float]:
    section = (plm.qc_config or {}).get(module) if isinstance(plm.qc_config, dict) else None
    try:
        value = (section or {}).get(key)
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _area(width, length, mu: str, qu: str) -> Optional[float]:
    """Square inch-yards for the 4-point formula; None when a size is missing."""
    if width is None or length is None or float(width) <= 0 or float(length) <= 0:
        return None
    try:
        return width_to_inches(float(width), mu) * length_to_yards(float(length), qu)
    except ValueError:
        return None


def _avg_points(points: float, area: float) -> Optional[float]:
    return points * FOUR_POINT_CONSTANT / area if area > 0 else None


def _load(db: Session, *, period, year, month, date_from, date_to, supplier_id, buyer_id, q):
    """PLMs in scope with their consignment / buyer / supplier, planned rolls and 4-point rolls."""
    start, end = resolve_period(period, year, month, date_from, date_to)
    query = (
        db.query(Plm, FabricReceive, Buyer, Supplier)
        .outerjoin(FabricReceive, FabricReceive.id == Plm.fabric_receive_id)
        .outerjoin(Buyer, Buyer.id == Plm.buyer_id)
        .outerjoin(Supplier, Supplier.id == Plm.supplier_id)
    )
    if start:
        query = query.filter(Plm.plm_date >= start)
    if end:
        query = query.filter(Plm.plm_date <= end)
    if supplier_id:
        query = query.filter(Plm.supplier_id == supplier_id)
    if buyer_id:
        query = query.filter(Plm.buyer_id == buyer_id)
    if q and q.strip():
        term = q.strip().lower()
        query = query.filter(
            func.lower(Plm.plm_code).contains(term)
            | func.lower(func.coalesce(FabricReceive.consignment, Plm.consignment, "")).contains(term)
            | func.lower(func.coalesce(Supplier.name, "")).contains(term)
            | func.lower(func.coalesce(Buyer.name, "")).contains(term)
            | func.lower(func.coalesce(Plm.fabric_type, "")).contains(term)
        )
    records = query.order_by(Plm.plm_date.desc(), Plm.plm_code).all()
    plm_ids = [r[0].id for r in records]

    planned = defaultdict(list)  # plm_id -> [(PlmRoll, colour_code)]
    factory = defaultdict(dict)  # plm_id -> roll_no -> FabricInspectionRoll
    if plm_ids:
        for roll, color in (
            db.query(PlmRoll, PlmColor)
            .join(PlmColor, PlmColor.id == PlmRoll.plm_color_id)
            .filter(PlmColor.plm_id.in_(plm_ids))
            .order_by(PlmColor.colour_code, PlmRoll.roll_no)
            .all()
        ):
            planned[color.plm_id].append((roll, color.colour_code))
        for roll, plm_id in (
            db.query(FabricInspectionRoll, FabricInspectionReport.plm_id)
            .join(FabricInspectionReport, FabricInspectionReport.id == FabricInspectionRoll.report_id)
            .filter(FabricInspectionReport.plm_id.in_(plm_ids))
            .all()
        ):
            factory[plm_id][roll.roll_no.strip().lower()] = roll
    return records, planned, factory, (start, end)


def _plm_rolls(plm, planned_rows, factory_rolls) -> list[dict]:
    """Planned roll (mill) joined with its 4-point roll (factory)."""
    mu, qu = _unit(plm), plm.quantity_unit or "yds"
    standard = _qc(plm, "correlation", "standard")
    rows, seen = [], set()
    for roll, colour in planned_rows:
        key = roll.roll_no.strip().lower()
        seen.add(key)
        rows.append(_roll_row(roll.roll_no, colour, roll, factory_rolls.get(key), mu, qu, standard))
    for key, fr in factory_rolls.items():  # inspected rolls that were never planned
        if key not in seen:
            rows.append(_roll_row(fr.roll_no, fr.colour_code or "", None, fr, mu, qu, standard))
    return rows


def _roll_row(roll_no, colour, planned, fr, mu, qu, standard) -> dict:
    mill_points = _f(planned.supplier_total_point) if planned else None
    mill_area = _area(planned.marked_width, planned.marked_length, mu, qu) if planned else None
    mill_avg = _avg_points(mill_points, mill_area) if mill_points is not None and mill_area else None
    factory_points = float(fr.total_points or 0) if fr else None
    factory_avg = _f(fr.percent_points) if fr else None
    return {
        "roll_no": roll_no,
        "colour": colour,
        "marked_width": _f(planned.marked_width) if planned else None,
        "marked_length": _f(planned.marked_length) if planned else None,
        "mill_points": mill_points,
        "mill_avg_points": mill_avg,
        "mill_pass": (mill_avg <= standard) if mill_avg is not None and standard is not None else None,
        "inspected": fr is not None,
        "fabric_inspection_roll_id": str(fr.id) if fr else None,
        "actual_width": _f(fr.width_actual) if fr else None,
        "actual_length": _f(fr.length_actual) if fr else None,
        "factory_points": factory_points,
        "factory_avg_points": factory_avg,
        "factory_pass": fr.is_pass if fr else None,
        "disposition": (fr.disposition or "pending") if fr else None,
        "disposition_note": fr.disposition_note if fr else None,
        "shade_band": fr.shade_band if fr else None,
        "inspected_at": fr.inspected_at.isoformat() if fr and fr.inspected_at else None,
        "_mill_area": mill_area,
        "_factory_area": _area(fr.width_actual, fr.length_actual, mu, qu) if fr else None,
        "_yards": length_to_yards(float(planned.marked_length), qu) if planned and planned.marked_length else 0.0,
        "_width_in": width_to_inches(float(planned.marked_width), mu) if planned and planned.marked_width else None,
    }


def _summary(rows: list[dict]) -> dict:
    inspected = [r for r in rows if r["inspected"]]
    factory_points = sum(r["factory_points"] or 0 for r in inspected)
    factory_area = sum(r["_factory_area"] or 0 for r in inspected)
    return {
        "roll_count": len(rows),
        "inspected": len(inspected),
        "passed": sum(1 for r in inspected if r["factory_pass"] is True),
        "failed": sum(1 for r in inspected if r["factory_pass"] is False),
        "avg_points": _avg_points(factory_points, factory_area),
    }


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


@router.get("/hierarchy")
def hierarchy(
    group: str = Query("supplier", pattern="^(supplier|buyer)$"),
    period: str = Query("recent", pattern="^(recent|week|month|year|all)$"),
    year: Optional[int] = None,
    month: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    supplier_id: Optional[UUID] = None,
    buyer_id: Optional[UUID] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Supplier/Buyer -> Consignment -> PLM with roll counts. Rolls load per PLM on demand."""
    records, planned, factory, (start, end) = _load(
        db, period=period, year=year, month=month, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, buyer_id=buyer_id, q=q,
    )
    groups: dict = {}
    for plm, receive, buyer, supplier in records:
        party = supplier if group == "supplier" else buyer
        gid = str(party.id) if party else "none"
        g = groups.setdefault(gid, {"id": gid, "name": party.name if party else "Unassigned", "consignments": {}})
        cid = str(receive.id) if receive else f"plm:{plm.id}"
        c = g["consignments"].setdefault(cid, {
            "id": cid,
            "consignment": (receive.consignment if receive else plm.consignment) or "-",
            "buyer_name": buyer.name if buyer else "",
            "supplier_name": supplier.name if supplier else "",
            "received_at": receive.received_at.isoformat() if receive and receive.received_at else None,
            "received_quantity": _f(receive.received_quantity) if receive else None,
            "quantity_unit": (receive.quantity_unit if receive else plm.quantity_unit) or "yds",
            "measurement_unit": _unit(plm),
            "plms": [],
        })
        s = _summary(_plm_rolls(plm, planned.get(plm.id, []), factory.get(plm.id, {})))
        c["plms"].append({
            "id": str(plm.id),
            "plm_code": plm.plm_code,
            "plm_date": plm.plm_date.isoformat() if plm.plm_date else None,
            "plm_style": plm.plm_style,
            "fabric_type": plm.fabric_type,
            "fabric_composition": plm.fabric_composition,
            "allocated_quantity": _f(plm.allocated_quantity),
            **s,
        })

    def roll_up(items):
        return {
            "roll_count": sum(i["roll_count"] for i in items),
            "inspected": sum(i["inspected"] for i in items),
            "passed": sum(i["passed"] for i in items),
            "failed": sum(i["failed"] for i in items),
        }

    out = []
    for g in groups.values():
        consignments = []
        for c in g["consignments"].values():
            consignments.append({**c, "plm_count": len(c["plms"]), **roll_up(c["plms"])})
        out.append({
            "id": g["id"], "name": g["name"],
            "consignment_count": len(consignments),
            "plm_count": sum(c["plm_count"] for c in consignments),
            **roll_up(consignments),
            "consignments": consignments,
        })
    out.sort(key=lambda g: g["name"].lower())
    return {
        "date_from": start.isoformat() if start else None,
        "date_to": end.isoformat() if end else None,
        "group": group,
        "totals": {"groups": len(out), "consignments": sum(g["consignment_count"] for g in out),
                   "plms": sum(g["plm_count"] for g in out), **roll_up(out)},
        "groups": out,
    }


@router.get("/plm-rolls/{plm_id}")
def plm_rolls(plm_id: UUID, db: Session = Depends(get_db)):
    plm = db.query(Plm).get(plm_id)
    if not plm:
        raise HTTPException(status_code=404, detail="PLM not found")
    planned = [
        (roll, color.colour_code)
        for roll, color in db.query(PlmRoll, PlmColor)
        .join(PlmColor, PlmColor.id == PlmRoll.plm_color_id)
        .filter(PlmColor.plm_id == plm_id)
        .order_by(PlmColor.colour_code, PlmRoll.roll_no)
        .all()
    ]
    factory = {
        r.roll_no.strip().lower(): r
        for r in db.query(FabricInspectionRoll)
        .join(FabricInspectionReport, FabricInspectionReport.id == FabricInspectionRoll.report_id)
        .filter(FabricInspectionReport.plm_id == plm_id)
        .all()
    }
    rows = _plm_rolls(plm, planned, factory)
    return {
        "plm_id": str(plm.id),
        "plm_code": plm.plm_code,
        "measurement_unit": _unit(plm),
        "quantity_unit": plm.quantity_unit or "yds",
        "accept_point": _qc(plm, "correlation", "standard"),
        "rolls": [_public(r) for r in rows],
    }


@router.get("/correlation-report")
def correlation_report(
    group: str = Query("supplier", pattern="^(supplier|fabric_type)$"),
    period: str = Query("month", pattern="^(recent|week|month|year|all)$"),
    year: Optional[int] = None,
    month: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    supplier_id: Optional[UUID] = None,
    buyer_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Supplier-wise / Fabric-type-wise Mill vs Factory report (paper: Monthly Correlation Report).

    Yards and width are converted to yds / inch so suppliers on cm or meter add up correctly.
    Mill and Factory pass = roll average points <= the PLM's correlation (accept) standard.
    """
    records, planned, factory, (start, end) = _load(
        db, period=period, year=year, month=month, date_from=date_from, date_to=date_to,
        supplier_id=supplier_id, buyer_id=buyer_id, q=None,
    )
    buckets: dict = {}
    for plm, _receive, _buyer, supplier in records:
        supplier_name = supplier.name if supplier else "Unassigned"
        fabric = (plm.fabric_type or "-").strip() or "-"
        key = supplier_name if group == "supplier" else fabric.lower()
        b = buckets.setdefault(key, {"suppliers": set(), "fabrics": set(), "compositions": set(), "months": set(),
                                     "standards": [], "rows": []})
        b["suppliers"].add(supplier_name)
        b["fabrics"].add(fabric)
        if plm.fabric_composition:
            b["compositions"].add(plm.fabric_composition)
        if plm.plm_date:
            b["months"].add(plm.plm_date.strftime("%b-%y"))
        std = _qc(plm, "correlation", "standard")
        if std is not None:
            b["standards"].append(std)
        b["rows"] += _plm_rolls(plm, planned.get(plm.id, []), factory.get(plm.id, {}))

    def side(rows, prefix):
        judged = [r for r in rows if r[f"{prefix}_pass"] is not None]
        passed = sum(1 for r in judged if r[f"{prefix}_pass"])
        points = sum(r[f"{prefix}_points"] or 0 for r in rows if r[f"{prefix}_points"] is not None)
        area = sum(r[f"_{prefix}_area"] or 0 for r in rows if r[f"{prefix}_points"] is not None)
        n = len(judged)
        return {
            "pass_qty": passed,
            "pass_pct": round(passed * 100 / n, 2) if n else None,
            "fail_qty": n - passed,
            "fail_pct": round((n - passed) * 100 / n, 2) if n else None,
            "total_avg_points": round(_avg_points(points, area), 2) if area else None,
            "total_points": points,
        }

    out = []
    for b in buckets.values():
        rows = b["rows"]
        widths = [r["_width_in"] for r in rows if r["_width_in"]]
        out.append({
            "supplier_name": " / ".join(sorted(b["suppliers"])),
            "month": ", ".join(sorted(b["months"])),
            "fabric_type": " / ".join(sorted(b["fabrics"])),
            "fabric_composition": " / ".join(sorted(b["compositions"])),
            "accept_point": round(sum(b["standards"]) / len(b["standards"]), 2) if b["standards"] else None,
            "total_roll": len(rows),
            "total_yds": round(sum(r["_yards"] for r in rows), 2),
            "avg_width_inch": round(sum(widths) / len(widths), 2) if widths else None,
            "mill": side(rows, "mill"),
            "factory": side(rows, "factory"),
        })
    out.sort(key=lambda r: (r["supplier_name"] if group == "supplier" else r["fabric_type"]).lower())
    return {
        "group": group,
        "date_from": start.isoformat() if start else None,
        "date_to": end.isoformat() if end else None,
        "rows": out,
    }
