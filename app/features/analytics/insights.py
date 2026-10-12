"""SQL-aggregated dashboard insights for the 4-point module.

Every number is computed in Postgres from the saved roll rows (which already hold the
backend-calculated total_points / is_pass), so clients only render.
Event time of a roll = inspected_at, falling back to created_at for older rows.
"""
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.features.admin_config.models import DefectType, Supplier
from app.features.analytics.models import SupplierMonthlyInput
from app.features.fabric_inspection.models import (
    FabricInspectionReport as Report,
    FabricInspectionRoll as Roll,
    FabricInspectionRollDefect as Defect,
)
from app.features.fabric_inspection.service import CM_PER_INCH, FOUR_POINT_CONSTANT, YARDS_PER_METER
from app.features.plm.models import InspectionSchedule, Plm, PlmColor, PlmRoll

EVENT_AT = func.coalesce(Roll.inspected_at, Roll.created_at)

_width_in = case((Report.measurement_unit_snapshot == "cm", Roll.width_actual / CM_PER_INCH), else_=Roll.width_actual)
_length_yd = case((Report.quantity_unit_snapshot == "meter", Roll.length_actual * YARDS_PER_METER), else_=Roll.length_actual)
AREA = case((and_(Roll.width_actual > 0, Roll.length_actual > 0), _width_in * _length_yd), else_=0)

PASSED = func.sum(case((Roll.is_pass.is_(True), 1), else_=0))
FAILED = func.sum(case((Roll.is_pass.is_(False), 1), else_=0))
POINTS = func.coalesce(func.sum(Roll.total_points), 0)
AREA_SUM = func.coalesce(func.sum(AREA), 0)


def _rate(passed: int, failed: int) -> Optional[float]:
    evaluated = passed + failed
    return round(passed / evaluated * 100, 1) if evaluated else None


def _density(points, area) -> Optional[float]:
    area = float(area or 0)
    return round(float(points or 0) * FOUR_POINT_CONSTANT / area, 2) if area > 0 else None


def _roll_query(db: Session, *entities):
    return (
        db.query(*entities)
        .select_from(Roll)
        .join(Report, Report.id == Roll.report_id)
        .join(Plm, Plm.id == Report.plm_id)
    )


def _scope(q, *, buyer_id=None, supplier_id=None, date_from: Optional[date] = None, date_to: Optional[date] = None, plm_id=None):
    if buyer_id:
        q = q.filter(Plm.buyer_id == buyer_id)
    if supplier_id:
        q = q.filter(Plm.supplier_id == supplier_id)
    if plm_id:
        q = q.filter(Plm.id == plm_id)
    if date_from:
        q = q.filter(EVENT_AT >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.filter(EVENT_AT <= datetime.combine(date_to, time.max))
    return q


def kpis(db: Session, **f) -> dict:
    today = date.today()
    row = _scope(_roll_query(
        db,
        func.count(Roll.id),
        PASSED,
        FAILED,
        POINTS,
        AREA_SUM,
        func.sum(case((func.date(EVENT_AT) == today, 1), else_=0)),
        func.sum(case((Roll.disposition == "accepted", 1), else_=0)),
        func.sum(case((Roll.disposition == "hold", 1), else_=0)),
        func.sum(case((Roll.disposition == "rejected", 1), else_=0)),
        func.sum(case((Roll.disposition == "claim", 1), else_=0)),
        func.sum(case((and_(Roll.is_pass.is_(False), Roll.disposition == "pending"), 1), else_=0)),
    ), **f).one()
    total, passed, failed, points, area, today_count, accepted, hold, rejected, claim, pending = [x or 0 for x in row]

    sched = db.query(InspectionSchedule.status, func.count(InspectionSchedule.id)).join(Plm, Plm.id == InspectionSchedule.plm_id)
    if f.get("buyer_id"):
        sched = sched.filter(Plm.buyer_id == f["buyer_id"])
    if f.get("supplier_id"):
        sched = sched.filter(Plm.supplier_id == f["supplier_id"])
    by_status = dict(sched.group_by(InspectionSchedule.status).all())
    today_sched = sched.filter(InspectionSchedule.scheduled_date == today).with_entities(func.count(InspectionSchedule.id)).scalar() or 0

    return {
        "rolls_inspected": int(total),
        "passed": int(passed),
        "failed": int(failed),
        "pass_rate": _rate(int(passed), int(failed)),
        "total_points": int(points),
        "points_per_100_sq_yd": _density(points, area),
        "rolls_today": int(today_count),
        "accepted": int(accepted),
        "hold": int(hold),
        "rejected": int(rejected),
        "claim": int(claim),
        "pending_disposition": int(pending),
        "schedules_pending": int(by_status.get("pending", 0)),
        "schedules_in_progress": int(by_status.get("in_progress", 0)),
        "schedules_today": int(today_sched),
    }


def trend(db: Session, granularity: str = "day", **f) -> list[dict]:
    if granularity not in ("day", "week", "month"):
        granularity = "day"
    bucket = func.date_trunc(granularity, EVENT_AT).label("bucket")
    rows = (
        _scope(_roll_query(db, bucket, func.count(Roll.id), PASSED, FAILED, POINTS, AREA_SUM), **f)
        .group_by(bucket)
        .order_by(bucket)
        .all()
    )
    return [
        {
            "period": b.date().isoformat(),
            "rolls": int(n or 0),
            "passed": int(p or 0),
            "failed": int(fl or 0),
            "pass_rate": _rate(int(p or 0), int(fl or 0)),
            "total_points": int(pts or 0),
            "points_per_100_sq_yd": _density(pts, area),
        }
        for b, n, p, fl, pts, area in rows
    ]


def defect_pareto(db: Session, **f) -> list[dict]:
    rows = (
        _scope(
            _roll_query(db, Defect.defect_type_id, DefectType.name, func.max(Defect.defect_type_name_snapshot),
                        func.coalesce(func.sum(Defect.count), 0), func.coalesce(func.sum(Defect.points_snapshot), 0))
            .join(Defect, Defect.roll_id == Roll.id)
            .outerjoin(DefectType, DefectType.id == Defect.defect_type_id),
            **f,
        )
        .group_by(Defect.defect_type_id, DefectType.name)
        .order_by(func.coalesce(func.sum(Defect.points_snapshot), 0).desc())
        .all()
    )
    total_points = sum(int(r[4] or 0) for r in rows)
    running = 0
    out = []
    for type_id, type_name, snapshot_name, count, points in rows:
        running += int(points or 0)
        out.append({
            "defect_type_id": str(type_id),
            "defect_type_name": type_name or snapshot_name or "Unknown",
            "total_count": int(count or 0),
            "total_points": int(points or 0),
            "percent": round(int(points or 0) / total_points * 100, 1) if total_points else 0.0,
            "cumulative_percent": round(running / total_points * 100, 1) if total_points else 0.0,
        })
    return out


def _month_add(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def supplier_scorecard(db: Session, **f) -> list[dict]:
    """Score = 70% roll pass rate + 30% communication rating (scaled to 100).

    Without a communication rating in the period the score is the pass rate alone.
    Grade: A >= 90, B >= 75, C >= 60, else D.
    """
    rows = (
        _scope(
            _roll_query(db, Supplier.id, Supplier.name, func.count(Roll.id), PASSED, FAILED, POINTS, AREA_SUM)
            .join(Supplier, Supplier.id == Plm.supplier_id),
            **f,
        )
        .group_by(Supplier.id, Supplier.name)
        .all()
    )
    if not rows:
        return []

    comm_q = db.query(SupplierMonthlyInput.supplier_id, func.avg(SupplierMonthlyInput.communication_rating))
    df, dt = f.get("date_from"), f.get("date_to")
    if df:
        comm_q = comm_q.filter((SupplierMonthlyInput.year * 100 + SupplierMonthlyInput.month) >= df.year * 100 + df.month)
    if dt:
        comm_q = comm_q.filter((SupplierMonthlyInput.year * 100 + SupplierMonthlyInput.month) <= dt.year * 100 + dt.month)
    comm = {sid: float(avg) for sid, avg in comm_q.group_by(SupplierMonthlyInput.supplier_id).all() if avg is not None}

    end = dt or date.today()
    start = _month_add(date(end.year, end.month, 1), -5)
    bucket = func.date_trunc("month", EVENT_AT).label("bucket")
    trend_f = {k: v for k, v in f.items() if k not in ("date_from", "date_to")}
    trend_rows = (
        _scope(_roll_query(db, Plm.supplier_id, bucket, func.count(Roll.id), PASSED, FAILED), date_from=start, date_to=end, **trend_f)
        .group_by(Plm.supplier_id, bucket)
        .all()
    )
    trends: dict = {}
    for sid, b, n, p, fl in trend_rows:
        trends.setdefault(sid, {})[b.date().replace(day=1)] = (int(n or 0), _rate(int(p or 0), int(fl or 0)))
    months = [_month_add(start, i) for i in range(6)]

    out = []
    for sid, name, total, passed, failed, points, area in rows:
        pass_rate = _rate(int(passed or 0), int(failed or 0))
        rating = comm.get(sid)
        score = None
        if pass_rate is not None:
            score = round(pass_rate * 0.7 + (rating / 5 * 100) * 0.3, 1) if rating is not None else pass_rate
        grade = None if score is None else "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
        series = trends.get(sid, {})
        out.append({
            "supplier_id": str(sid),
            "supplier_name": name,
            "rolls": int(total or 0),
            "passed": int(passed or 0),
            "failed": int(failed or 0),
            "pass_rate": pass_rate,
            "total_points": int(points or 0),
            "points_per_100_sq_yd": _density(points, area),
            "communication_rating": round(rating, 2) if rating is not None else None,
            "score": score,
            "grade": grade,
            "trend": [
                {"period": m.isoformat(), "rolls": series.get(m, (0, None))[0], "pass_rate": series.get(m, (0, None))[1]}
                for m in months
            ],
        })
    out.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["supplier_name"]))
    return out


def defect_heatmap(db: Session, max_suppliers: int = 10, max_defects: int = 10, **f) -> dict:
    max_suppliers = max(1, min(int(max_suppliers), 30))
    max_defects = max(1, min(int(max_defects), 30))
    type_name = func.coalesce(DefectType.name, Defect.defect_type_name_snapshot, "Unknown")
    rows = (
        _scope(
            _roll_query(db, Supplier.name, type_name, func.coalesce(func.sum(Defect.points_snapshot), 0), func.coalesce(func.sum(Defect.count), 0))
            .join(Supplier, Supplier.id == Plm.supplier_id)
            .join(Defect, Defect.roll_id == Roll.id)
            .outerjoin(DefectType, DefectType.id == Defect.defect_type_id),
            **f,
        )
        .group_by(Supplier.name, type_name)
        .all()
    )
    supplier_totals: dict[str, int] = {}
    defect_totals: dict[str, int] = {}
    for s, d, pts, _ in rows:
        supplier_totals[s] = supplier_totals.get(s, 0) + int(pts or 0)
        defect_totals[d] = defect_totals.get(d, 0) + int(pts or 0)
    suppliers = [s for s, _ in sorted(supplier_totals.items(), key=lambda kv: -kv[1])[:max_suppliers]]
    defects = [d for d, _ in sorted(defect_totals.items(), key=lambda kv: -kv[1])[:max_defects]]
    keep_s, keep_d = set(suppliers), set(defects)
    return {
        "suppliers": suppliers,
        "defect_types": defects,
        "cells": [
            {"supplier": s, "defect_type": d, "total_points": int(pts or 0), "total_count": int(cnt or 0)}
            for s, d, pts, cnt in rows if s in keep_s and d in keep_d
        ],
    }


def correlation_scatter(db: Session, limit: int = 1000, **f) -> list[dict]:
    limit = max(1, min(int(limit), 5000))
    rows = (
        _scope(
            _roll_query(db, Plm.id, Plm.plm_code, Roll.roll_no, Supplier.name, PlmRoll.supplier_total_point, Roll.total_points, Roll.percent_points)
            .outerjoin(Supplier, Supplier.id == Plm.supplier_id)
            .join(PlmColor, and_(PlmColor.plm_id == Plm.id, PlmColor.colour_code == Roll.colour_code))
            .join(PlmRoll, and_(PlmRoll.plm_color_id == PlmColor.id, PlmRoll.roll_no == Roll.roll_no)),
            **f,
        )
        .filter(PlmRoll.supplier_total_point.isnot(None))
        .order_by(EVENT_AT.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "plm_id": str(pid),
            "plm_code": code,
            "roll_no": roll_no,
            "supplier_name": supplier or "",
            "mill_points": float(mill or 0),
            "factory_points": int(factory or 0),
            "factory_percent_points": float(pct) if pct is not None else None,
        }
        for pid, code, roll_no, supplier, mill, factory, pct in rows
    ]


def disposition_summary(db: Session, **f) -> list[dict]:
    rows = _scope(_roll_query(db, Roll.disposition, func.count(Roll.id)), **f).group_by(Roll.disposition).all()
    counts = {d or "pending": int(n or 0) for d, n in rows}
    return [{"disposition": d, "count": counts.get(d, 0)} for d in ("pending", "accepted", "hold", "rejected", "claim")]


def default_trend_granularity(date_from: Optional[date], date_to: Optional[date]) -> str:
    if not date_from:
        return "month"
    span = ((date_to or date.today()) - date_from).days
    return "day" if span <= 31 else "week" if span <= 180 else "month"
