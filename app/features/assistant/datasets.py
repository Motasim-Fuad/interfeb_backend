"""Tabular datasets behind every list answer.

The chat shows a preview; the dashboard's full viewer, CSV export and the printable PDF
all page through the same dataset with search / filter / sort applied here."""
import re
from collections import defaultdict
from datetime import date
from typing import Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.features.admin_config.models import Buyer, DefectRangeConfig, DefectType, Material, Supplier, SupplierQcProfile
from app.features.analytics import insights
from app.features.analytics.insights import EVENT_AT, FAILED, PASSED, Plm, Report, Roll
from app.features.plm.models import InspectionSchedule, PlmColor, PlmRoll
from app.features.store.models import FabricReceive

MAX_ROWS = 5000


def _d(v: Optional[str]) -> Optional[date]:
    if not v:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _num(v) -> Optional[float]:
    """Master-data numbers are stored as text ("160", "58\"", "8%"); keep the first number."""
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


def col(key: str, label: str, kind: str = "text") -> dict:
    return {"key": key, "label": label, "kind": kind}


def _rate(p: int, f: int) -> Optional[float]:
    return round(p / (p + f) * 100, 1) if (p + f) else None


def _roll_stats_by(db: Session, group_col, args: dict) -> dict:
    q = insights._roll_query(db, group_col, func.count(Roll.id), PASSED, FAILED)
    q = insights._scope(q, date_from=_d(args.get("date_from")), date_to=_d(args.get("date_to")))
    return {k: (int(n or 0), int(p or 0), int(f or 0)) for k, n, p, f in q.group_by(group_col).all()}


def ds_buyers(db: Session, args: dict):
    plm_counts = dict(db.query(Plm.buyer_id, func.count(Plm.id)).group_by(Plm.buyer_id).all())
    stats = _roll_stats_by(db, Plm.buyer_id, args)
    rows = []
    for b in db.query(Buyer).order_by(Buyer.name).all():
        n, p, f = stats.get(b.id, (0, 0, 0))
        rows.append({"id": str(b.id), "name": b.name, "status": "Active" if b.is_active else "Inactive",
                     "plms": plm_counts.get(b.id, 0), "rolls": n, "failed": f, "pass_rate": _rate(p, f)})
    cols = [col("name", "Buyer"), col("status", "Status", "status"), col("plms", "PLMs", "number"),
            col("rolls", "Rolls inspected", "number"), col("failed", "Failed", "number"), col("pass_rate", "Pass rate", "percent")]
    return cols, rows


def ds_suppliers(db: Session, args: dict):
    plm_counts = dict(db.query(Plm.supplier_id, func.count(Plm.id)).group_by(Plm.supplier_id).all())
    profiles = {sid for (sid,) in db.query(SupplierQcProfile.supplier_id).filter(SupplierQcProfile.is_active.is_(True)).all()}
    scores = {r["supplier_id"]: r for r in insights.supplier_scorecard(
        db, date_from=_d(args.get("date_from")), date_to=_d(args.get("date_to")))}
    rows = []
    for s in db.query(Supplier).order_by(Supplier.name).all():
        sc = scores.get(str(s.id), {})
        rows.append({"id": str(s.id), "name": s.name, "unit": (s.default_measurement_unit or "").upper(),
                     "qc_profile": "Configured" if s.id in profiles else "Missing", "plms": plm_counts.get(s.id, 0),
                     "rolls": sc.get("rolls", 0), "failed": sc.get("failed", 0), "pass_rate": sc.get("pass_rate"),
                     "grade": sc.get("grade"),
                     "status": "Active" if s.is_active else "Inactive"})
    cols = [col("name", "Supplier"), col("unit", "Unit"), col("qc_profile", "QC profile", "status"), col("plms", "PLMs", "number"),
            col("rolls", "Rolls inspected", "number"), col("failed", "Failed", "number"), col("pass_rate", "Pass rate", "percent"),
            col("grade", "Grade", "grade"), col("status", "Status", "status")]
    return cols, rows


def ds_plms(db: Session, args: dict):
    q = db.query(Plm)
    if args.get("buyer_id"):
        q = q.filter(Plm.buyer_id == args["buyer_id"])
    if args.get("supplier_id"):
        q = q.filter(Plm.supplier_id == args["supplier_id"])
    if args.get("material_id"):
        q = q.filter(Plm.material_id == args["material_id"])
    if args.get("consignment_id"):
        q = q.filter(Plm.fabric_receive_id == args["consignment_id"])
    if args.get("schedule_status"):
        q = q.filter(Plm.id.in_(db.query(InspectionSchedule.plm_id).filter(InspectionSchedule.status == args["schedule_status"])))
    if args.get("date_from"):
        q = q.filter(Plm.plm_date >= _d(args["date_from"]))
    if args.get("date_to"):
        q = q.filter(Plm.plm_date <= _d(args["date_to"]))
    plms = q.order_by(Plm.plm_date.desc(), Plm.plm_code).limit(MAX_ROWS).all()
    ids = [p.id for p in plms]
    planned = dict(db.query(PlmColor.plm_id, func.count(PlmRoll.id)).join(PlmRoll, PlmRoll.plm_color_id == PlmColor.id)
                   .filter(PlmColor.plm_id.in_(ids)).group_by(PlmColor.plm_id).all()) if ids else {}
    insp = {k: v for k, v in _roll_stats_by(db, Plm.id, {}).items() if k in set(ids)}
    rows = []
    for p in plms:
        n, ps, f = insp.get(p.id, (0, 0, 0))
        rows.append({"id": str(p.id), "plm_code": p.plm_code, "plm_date": p.plm_date.isoformat(), "buyer": p.buyer_name,
                     "supplier": p.supplier_name, "consignment": p.consignment, "material": p.material_display_name,
                     "planned_rolls": int(planned.get(p.id, 0)), "inspected": n, "failed": f, "pass_rate": _rate(ps, f)})
    cols = [col("plm_code", "PLM"), col("plm_date", "Date", "date"), col("buyer", "Buyer"), col("supplier", "Supplier"),
            col("consignment", "Consignment"), col("material", "Material"), col("planned_rolls", "Planned rolls", "number"),
            col("inspected", "Inspected", "number"), col("failed", "Failed", "number"), col("pass_rate", "Pass rate", "percent")]
    return cols, rows


def ds_rolls(db: Session, args: dict):
    q = insights._roll_query(db, Roll, Plm.plm_code, Plm.id, func.coalesce(Supplier.name, ""), func.coalesce(Buyer.name, ""))
    q = q.outerjoin(Supplier, Supplier.id == Plm.supplier_id).outerjoin(Buyer, Buyer.id == Plm.buyer_id)
    q = insights._scope(q, buyer_id=args.get("buyer_id"), supplier_id=args.get("supplier_id"), plm_id=args.get("plm_id"),
                        date_from=_d(args.get("date_from")), date_to=_d(args.get("date_to")))
    if args.get("is_pass") is not None:
        q = q.filter(Roll.is_pass.is_(bool(args["is_pass"])))
    if args.get("disposition"):
        q = q.filter(Roll.disposition == args["disposition"])
    rows = []
    for roll, code, plm_id, supplier, buyer in q.order_by(EVENT_AT.desc()).limit(MAX_ROWS).all():
        rows.append({"id": str(roll.id), "plm_id": str(plm_id), "plm_code": code, "supplier": supplier, "buyer": buyer,
                     "roll_no": roll.roll_no, "colour": roll.colour_code, "points": roll.total_points,
                     "pts_100": float(roll.percent_points) if roll.percent_points is not None else None,
                     "result": "PASS" if roll.is_pass else "FAIL" if roll.is_pass is False else "PENDING",
                     "disposition": (roll.disposition or "pending").title(), "inspector": roll.inspector_name,
                     "inspected_at": (roll.inspected_at or roll.created_at).isoformat(timespec="minutes")})
    cols = [col("plm_code", "PLM"), col("supplier", "Supplier"), col("roll_no", "Roll"), col("colour", "Colour"),
            col("points", "Points", "number"), col("pts_100", "Pts/100 yd²", "number"), col("result", "Result", "status"),
            col("disposition", "Decision", "status"), col("inspector", "Inspector"), col("inspected_at", "Inspected", "datetime")]
    return cols, rows


def ds_schedules(db: Session, args: dict):
    q = db.query(InspectionSchedule).join(Plm, Plm.id == InspectionSchedule.plm_id)
    if args.get("schedule_status"):
        q = q.filter(InspectionSchedule.status == args["schedule_status"])
    if args.get("supplier_id"):
        q = q.filter(Plm.supplier_id == args["supplier_id"])
    if args.get("buyer_id"):
        q = q.filter(Plm.buyer_id == args["buyer_id"])
    if args.get("plm_id"):
        q = q.filter(Plm.id == args["plm_id"])
    if args.get("date_from"):
        q = q.filter(InspectionSchedule.scheduled_date >= _d(args["date_from"]))
    if args.get("date_to"):
        q = q.filter(InspectionSchedule.scheduled_date <= _d(args["date_to"]))
    rows = [{"id": str(s.id), "plm_id": str(s.plm_id), "date": s.scheduled_date.isoformat(), "plm_code": s.plm_code,
             "buyer": s.buyer_name, "supplier": s.supplier_name, "status": s.status.replace("_", " ").title(),
             "assigned_to": s.assigned_to, "note": s.machine_note}
            for s in q.order_by(InspectionSchedule.scheduled_date).limit(MAX_ROWS).all()]
    cols = [col("date", "Date", "date"), col("plm_code", "PLM"), col("buyer", "Buyer"), col("supplier", "Supplier"),
            col("status", "Status", "status"), col("assigned_to", "Assigned to"), col("note", "Machine note")]
    return cols, rows


def ds_consignments(db: Session, args: dict):
    q = db.query(FabricReceive)
    if args.get("supplier_id"):
        q = q.filter(FabricReceive.supplier_id == args["supplier_id"])
    if args.get("buyer_id"):
        q = q.filter(FabricReceive.buyer_id == args["buyer_id"])
    if args.get("qc_status"):
        q = q.filter(FabricReceive.qc_setup_status == args["qc_status"])
    if args.get("date_from"):
        q = q.filter(FabricReceive.received_at >= _d(args["date_from"]))
    if args.get("date_to"):
        q = q.filter(FabricReceive.received_at <= _d(args["date_to"]))
    plm_counts = dict(db.query(Plm.fabric_receive_id, func.count(Plm.id)).group_by(Plm.fabric_receive_id).all())
    names_b = dict(db.query(Buyer.id, Buyer.name).all())
    names_s = dict(db.query(Supplier.id, Supplier.name).all())
    rows = [{"id": str(r.id), "consignment": r.consignment, "buyer": names_b.get(r.buyer_id), "supplier": names_s.get(r.supplier_id),
             "quantity": float(r.received_quantity or r.fabric_length or 0), "unit": (r.quantity_unit or "").upper(),
             "qc_setup": f"{r.qc_setup_status.title()} v{r.qc_setup_version}", "received_at": r.received_at.isoformat() if r.received_at else None,
             "plms": plm_counts.get(r.id, 0)}
            for r in q.order_by(FabricReceive.received_at.desc().nullslast()).limit(MAX_ROWS).all()]
    cols = [col("consignment", "Consignment"), col("buyer", "Buyer"), col("supplier", "Supplier"), col("quantity", "Received", "number"),
            col("unit", "Unit"), col("qc_setup", "QC setup", "status"), col("received_at", "Received", "date"), col("plms", "PLMs", "number")]
    return cols, rows


def ds_materials(db: Session, args: dict):
    rows = []
    for m in db.query(Material).order_by(Material.fabric_name).all():
        comp = " / ".join(f"{pct}% {fib}" for fib, pct in [(m.fiber_type_1, m.fiber_1_pct), (m.fiber_type_2, m.fiber_2_pct),
                                                             (m.fiber_type_3, m.fiber_3_pct)] if fib)
        rows.append({"id": str(m.id), "name": m.display_name, "code": m.fabric_code, "composition": comp or None,
                     "gsm": _num(m.gsm_expected), "gsm_tolerance": _num(m.gsm_tolerance), "width": _num(m.fabric_width_expected),
                     "moisture": _num(m.accepted_moisture), "status": "Active" if m.is_active else "Inactive"})
    cols = [col("name", "Material"), col("code", "Code"), col("composition", "Composition"), col("gsm", "GSM", "number"),
            col("gsm_tolerance", "GSM tol. %", "number"), col("width", "Width", "number"), col("moisture", "Max moisture %", "number"),
            col("status", "Status", "status")]
    return cols, rows


def ds_defect_types(db: Session, args: dict):
    ranges = defaultdict(list)
    for r in db.query(DefectRangeConfig).filter(DefectRangeConfig.is_active.is_(True)).order_by(DefectRangeConfig.sort_order).all():
        ranges[r.defect_type_id].append(f"{r.range_label} = {r.points}")
    general = ranges.get(None, [])
    pareto = {r["defect_type_id"]: r for r in insights.defect_pareto(db, date_from=_d(args.get("date_from")), date_to=_d(args.get("date_to")))}
    rows = []
    for d in db.query(DefectType).order_by(DefectType.sort_order, DefectType.name).all():
        p = pareto.get(str(d.id), {})
        rows.append({"id": str(d.id), "name": d.name, "ranges": ", ".join(ranges.get(d.id) or general) or None,
                     "count": p.get("total_count", 0), "points": p.get("total_points", 0),
                     "status": "Active" if d.is_active else "Inactive"})
    cols = [col("name", "Defect type"), col("ranges", "Point ranges"), col("count", "Times found", "number"),
            col("points", "Total points", "number"), col("status", "Status", "status")]
    return cols, rows


def ds_defect_pareto(db: Session, args: dict):
    rows = insights.defect_pareto(db, buyer_id=args.get("buyer_id"), supplier_id=args.get("supplier_id"), plm_id=args.get("plm_id"),
                                  date_from=_d(args.get("date_from")), date_to=_d(args.get("date_to")))
    out = [{"id": r["defect_type_id"], "name": r["defect_type_name"], "count": r["total_count"], "points": r["total_points"],
            "percent": r["percent"], "cumulative": r["cumulative_percent"]} for r in rows]
    cols = [col("name", "Defect"), col("count", "Count", "number"), col("points", "Points", "number"),
            col("percent", "Share", "percent"), col("cumulative", "Cumulative", "percent")]
    return cols, out


def ds_scorecard(db: Session, args: dict):
    rows = insights.supplier_scorecard(db, buyer_id=args.get("buyer_id"), date_from=_d(args.get("date_from")), date_to=_d(args.get("date_to")))
    out = [{"id": r["supplier_id"], "name": r["supplier_name"], "rolls": r["rolls"], "failed": r["failed"], "pass_rate": r["pass_rate"],
            "pts_100": r["points_per_100_sq_yd"], "communication": r["communication_rating"], "score": r["score"], "grade": r["grade"]}
           for r in rows]
    cols = [col("name", "Supplier"), col("rolls", "Rolls", "number"), col("failed", "Failed", "number"), col("pass_rate", "Pass rate", "percent"),
            col("pts_100", "Pts/100 yd²", "number"), col("communication", "Comm. rating", "number"), col("score", "Score", "number"),
            col("grade", "Grade", "grade")]
    return cols, out


DATASETS: dict[str, tuple[str, Callable]] = {
    "buyers": ("Buyers", ds_buyers),
    "suppliers": ("Suppliers", ds_suppliers),
    "plms": ("PLMs", ds_plms),
    "rolls": ("Inspected rolls", ds_rolls),
    "schedules": ("Inspection schedule", ds_schedules),
    "consignments": ("Fabric receives / consignments", ds_consignments),
    "materials": ("Materials", ds_materials),
    "defect_types": ("Defect types", ds_defect_types),
    "defect_pareto": ("Defect Pareto", ds_defect_pareto),
    "scorecard": ("Supplier scorecard", ds_scorecard),
}


def _matches(row: dict, q: str, keys: list[str]) -> bool:
    return any(q in str(row.get(k) or "").lower() for k in keys)


def run_dataset(db: Session, name: str, args: dict, *, q: str = "", filters: Optional[dict] = None,
                sort: Optional[str] = None, desc: bool = False, limit: int = 50, offset: int = 0) -> dict:
    if name not in DATASETS:
        raise KeyError(name)
    title, fn = DATASETS[name]
    columns, rows = fn(db, args or {})
    keys = [c["key"] for c in columns]
    filters = {**((args or {}).get("match") or {}), **(filters or {})}
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if _matches(r, needle, keys)]
    for key, value in filters.items():
        if value in (None, ""):
            continue
        rows = [r for r in rows if str(r.get(key) or "").lower() == str(value).lower()]
    if sort and sort in keys:
        present = [r for r in rows if r.get(sort) not in (None, "")]
        missing = [r for r in rows if r.get(sort) in (None, "")]
        present.sort(key=lambda r: r[sort].lower() if isinstance(r[sort], str) else r[sort], reverse=desc)
        rows = present + missing
    facets = {}
    for c in columns:
        if c["kind"] in ("status", "grade"):
            facets[c["key"]] = sorted({str(r.get(c["key"])) for r in rows if r.get(c["key"]) not in (None, "")})
    total = len(rows)
    return {"name": name, "title": title, "columns": columns, "rows": rows[offset:offset + max(1, min(limit, MAX_ROWS))],
            "total": total, "facets": facets}
