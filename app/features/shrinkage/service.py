"""
Mirrors ShrinkageMeasurement/ShrinkageRollData/ShrinkagePatternReportEntity getters from
Flutter - shrink/elongation values, Adjust Shrinkage %, per-roll pass/fail, report averages.
"""
from typing import List

from app.features.shrinkage.models import ShrinkageMeasurement, ShrinkageRoll


def adjust_pct(before: float, after: float) -> float:
    if not before:
        return 0.0
    return ((after - before) / before) * 100


def compute_measurement_fields(m: ShrinkageMeasurement) -> dict:
    before_length = float(m.before_length) if m.before_length is not None else None
    before_width = float(m.before_width) if m.before_width is not None else None
    after_length = float(m.after_length) if m.after_length is not None else None
    after_width = float(m.after_width) if m.after_width is not None else None

    shrink_length = (after_length - before_length) if before_length is not None and after_length is not None else None
    shrink_width = (after_width - before_width) if before_width is not None and after_width is not None else None

    return {
        "shrink_length": shrink_length,
        "shrink_width": shrink_width,
        "adjust_length_pct": adjust_pct(before_length, after_length) if before_length and after_length is not None else None,
        "adjust_width_pct": adjust_pct(before_width, after_width) if before_width and after_width is not None else None,
    }


def compute_roll_fields(roll: ShrinkageRoll, standard: float | None) -> dict:
    length_pcts = []
    width_pcts = []
    for m in roll.measurements:
        f = compute_measurement_fields(m)
        if f["adjust_length_pct"] is not None:
            length_pcts.append(f["adjust_length_pct"])
        if f["adjust_width_pct"] is not None:
            width_pcts.append(f["adjust_width_pct"])

    avg_length_pct = sum(length_pcts) / len(length_pcts) if length_pcts else None
    avg_width_pct = sum(width_pcts) / len(width_pcts) if width_pcts else None

    return {"avg_length_pct": avg_length_pct, "avg_width_pct": avg_width_pct}


def compute_report_stats(rolls: List[ShrinkageRoll], standard: float | None) -> dict:
    roll_fields = [compute_roll_fields(r, standard) for r in rolls]
    length_vals = [f["avg_length_pct"] for f in roll_fields if f["avg_length_pct"] is not None]
    width_vals = [f["avg_width_pct"] for f in roll_fields if f["avg_width_pct"] is not None]

    return {
        "avg_shrinkage_length": sum(length_vals) / len(length_vals) if length_vals else None,
        "avg_shrinkage_width": sum(width_vals) / len(width_vals) if width_vals else None,
        "total_yds": sum(float(r.yds) for r in rolls if r.yds is not None),
    }
