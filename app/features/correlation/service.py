from app.features.fabric_inspection.service import FOUR_POINT_CONSTANT, width_to_inches, length_to_yards


def _average(total: float, length: float, width: float, measurement_unit: str, quantity_unit: str):
    if length <= 0 or width <= 0:
        return None
    width_inches = width_to_inches(width, measurement_unit)
    length_yards = length_to_yards(length, quantity_unit)
    return (total * FOUR_POINT_CONSTANT) / (length_yards * width_inches) if width_inches > 0 and length_yards > 0 else None


def compute_roll_fields(roll, measurement_unit: str, quantity_unit: str) -> dict:
    mill_avg = _average(float(roll.mill_total_point or 0), float(roll.length_yards or 0), float(roll.width or 0), measurement_unit, quantity_unit)
    factory_avg = _average(float(roll.factory_total_points or 0), float(getattr(roll, "factory_length", 0) or 0), float(getattr(roll, "factory_width", 0) or 0), measurement_unit, quantity_unit)
    standard = float(roll.standard) if roll.standard is not None else None
    return {
        "mill_average_point": mill_avg,
        "factory_average_point": factory_avg,
        "difference": abs(float(roll.mill_total_point or 0) - float(roll.factory_total_points or 0)),
        "mill_pass": mill_avg <= standard if mill_avg is not None and standard is not None else None,
        "factory_pass": factory_avg <= standard if factory_avg is not None and standard is not None else None,
    }
