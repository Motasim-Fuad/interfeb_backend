"""Four-point fabric inspection calculations and dynamic defect snapshots."""
from sqlalchemy.orm import Session

from app.features.admin_config import models as config_models
from app.features.fabric_inspection import models

FOUR_POINT_CONSTANT = 3600
CM_PER_INCH = 2.54
YARDS_PER_METER = 1.0936132983377078


def width_to_inches(width: float, measurement_unit: str) -> float:
    unit = (measurement_unit or "").strip().lower()
    if unit == "cm":
        return width / CM_PER_INCH
    if unit == "inch":
        return width
    raise ValueError("Unsupported measurement unit; configure supplier as inch or cm")


def length_to_yards(length: float, quantity_unit: str) -> float:
    unit = (quantity_unit or "").strip().lower()
    if unit == "meter":
        return length * YARDS_PER_METER
    if unit == "yds":
        return length
    raise ValueError("Unsupported quantity unit; configure consignment as yds or meter")


def score_roll(
    total_points: int,
    width_actual,
    length_actual,
    individual_standard: float | None,
    measurement_unit: str = "inch",
    quantity_unit: str = "yds",
) -> tuple[float | None, bool | None]:
    percent_points = None
    if width_actual and length_actual and float(width_actual) > 0 and float(length_actual) > 0:
        width_inches = width_to_inches(float(width_actual), measurement_unit)
        length_yards = length_to_yards(float(length_actual), quantity_unit)
        percent_points = (total_points * FOUR_POINT_CONSTANT) / float(width_inches * length_yards)
    is_pass = None
    if percent_points is not None and individual_standard is not None:
        is_pass = float(percent_points) <= float(individual_standard)
    return percent_points, is_pass


def recalculate_roll(
    db: Session,
    roll: models.FabricInspectionRoll,
    individual_standard: float | None,
    measurement_unit: str = "inch",
    quantity_unit: str = "yds",
):
    total_points = sum(d.points_snapshot for d in roll.defects)
    roll.total_points = total_points
    roll.percent_points, roll.is_pass = score_roll(
        total_points, roll.width_actual, roll.length_actual, individual_standard, measurement_unit, quantity_unit,
    )


def compute_report_stats(report: models.FabricInspectionReport) -> dict:
    rolls = list(report.rolls or [])
    total_points = sum(int(r.total_points or 0) for r in rolls)
    lengths = [float(r.length_actual) for r in rolls if r.length_actual is not None and float(r.length_actual) > 0]
    widths = [float(r.width_actual) for r in rolls if r.width_actual is not None and float(r.width_actual) > 0]
    total_length = sum(lengths)
    avg_width = sum(widths) / len(widths) if widths else None

    # For mixed-width rolls the exact inspected area is the SUM of each
    # roll's width x length, not average(width) x total(length). The latter
    # can bias the report when longer rolls have a different width.
    inspected_area_inch_yards = 0.0
    for r in rolls:
        if r.width_actual is None or r.length_actual is None:
            continue
        width = float(r.width_actual)
        length = float(r.length_actual)
        if width <= 0 or length <= 0:
            continue
        inspected_area_inch_yards += (
            width_to_inches(width, report.measurement_unit_snapshot)
            * length_to_yards(length, report.quantity_unit_snapshot)
        )

    overall_pct = (total_points * FOUR_POINT_CONSTANT) / inspected_area_inch_yards if inspected_area_inch_yards > 0 else None

    return {
        "total_defect_points": total_points,
        "total_length_actual": total_length,
        "avg_width_actual": avg_width,
        "overall_percent_points": overall_pct,
        "pass_count": sum(1 for r in rolls if r.is_pass is True),
        "fail_count": sum(1 for r in rolls if r.is_pass is False),
        "is_pass": (
            overall_pct <= float(report.average_standard_snapshot)
            if overall_pct is not None and report.average_standard_snapshot is not None
            else None
        ),
    }


def resolve_defect_snapshot(db: Session, defect_type_id, range_config_id, count: int, lookup: dict | None = None) -> dict:
    """Validate a dashboard-managed defect/range and freeze its human-readable meaning.

    IDs keep referential integrity. Snapshots keep historical reports stable if an admin
    later renames/deactivates a defect or range. Pass `lookup` (from load_defect_lookup)
    to avoid two queries per defect line.
    """
    if lookup is not None:
        defect_type = lookup["types"].get(defect_type_id)
        range_config = lookup["ranges"].get(range_config_id)
    else:
        defect_type = db.query(config_models.DefectType).get(defect_type_id)
        range_config = db.query(config_models.DefectRangeConfig).get(range_config_id)
    if not defect_type or not defect_type.is_active:
        raise ValueError("Invalid or inactive defect_type_id")
    if not range_config or not range_config.is_active:
        raise ValueError("Invalid or inactive range_config_id")
    if range_config.defect_type_id and range_config.defect_type_id != defect_type_id:
        raise ValueError("Range config does not belong to this defect type")
    if count < 0:
        raise ValueError("Defect count cannot be negative")
    return {
        "points": int(range_config.points) * int(count),
        "defect_type_name": defect_type.name,
        "range_label": range_config.range_label,
    }


def load_defect_lookup(db: Session, defect_type_ids, range_config_ids) -> dict:
    type_ids = set(defect_type_ids)
    range_ids = set(range_config_ids)
    types = db.query(config_models.DefectType).filter(config_models.DefectType.id.in_(type_ids)).all() if type_ids else []
    ranges = db.query(config_models.DefectRangeConfig).filter(config_models.DefectRangeConfig.id.in_(range_ids)).all() if range_ids else []
    return {"types": {t.id: t for t in types}, "ranges": {r.id: r for r in ranges}}


def add_defect_snapshot(db: Session, defect_type_id, range_config_id, count: int) -> int:
    return int(resolve_defect_snapshot(db, defect_type_id, range_config_id, count)["points"])
