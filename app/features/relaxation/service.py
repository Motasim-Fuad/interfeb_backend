"""Fabric relaxation calculations from the original paper form.

The paper records before/after length/width and reports the percentage change. It does
not define an acceptance threshold, so this module intentionally does NOT manufacture a
PASS/FAIL result. Positive values mean expansion; negative values mean shrinkage.
"""
from typing import List
from app.features.relaxation.models import FabricRelaxationRoll


def change_pct(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or float(before) == 0:
        return None
    return ((float(after) - float(before)) / float(before)) * 100


def readings(roll: FabricRelaxationRoll) -> list[dict]:
    """Every after-relaxation reading with its change %. Older rolls saved before multi-reading
    support have only after_relax_*; they show as one reading."""
    raw = list(roll.after_readings or [])
    if not raw and any(v is not None for v in (roll.after_relax_time, roll.after_relax_length, roll.after_relax_width)):
        raw = [{"time": roll.after_relax_time, "length": roll.after_relax_length, "width": roll.after_relax_width}]
    out = []
    for r in raw:
        length = float(r["length"]) if r.get("length") is not None else None
        width = float(r["width"]) if r.get("width") is not None else None
        out.append({"time": r.get("time"), "length": length, "width": width,
                    "shrinkage_length_pct": change_pct(roll.before_relax_length, length),
                    "shrinkage_width_pct": change_pct(roll.before_relax_width, width)})
    return out


def compute_roll_fields(roll: FabricRelaxationRoll, standard: float | None = None) -> dict:
    """Roll result = the latest reading (end of relaxation)."""
    return {
        "shrinkage_length_pct": change_pct(roll.before_relax_length, roll.after_relax_length),
        "shrinkage_width_pct": change_pct(roll.before_relax_width, roll.after_relax_width),
        # Not "after_readings": that name is the stored column and must not be overwritten.
        "readings_view": readings(roll),
    }


def compute_report_stats(rolls: List[FabricRelaxationRoll], standard: float | None = None) -> dict:
    length_vals = []
    width_vals = []
    for roll in rolls:
        f = compute_roll_fields(roll)
        f.pop("readings_view")
        if f["shrinkage_length_pct"] is not None:
            length_vals.append(f["shrinkage_length_pct"])
        if f["shrinkage_width_pct"] is not None:
            width_vals.append(f["shrinkage_width_pct"])
    return {
        "avg_shrinkage_length": sum(length_vals) / len(length_vals) if length_vals else None,
        "avg_shrinkage_width": sum(width_vals) / len(width_vals) if width_vals else None,
    }
