"""Skew & Bowing calculations aligned with the supplied factory paper report."""
from typing import List, Optional

from app.features.skew_bowing.models import SkewBowingRoll
from app.features.fabric_inspection.service import width_to_inches


def bow_rule_value(ac: Optional[float], bd: Optional[float]) -> Optional[float]:
    """Measured skew amount: |AC - BD| in the supplier-owned measurement unit."""
    if ac is None or bd is None:
        return None
    return abs(float(ac) - float(bd))


def skew_percent(
    ac: Optional[float],
    bd: Optional[float],
    reference_fabric_length: Optional[float] = None,
    measurement_unit: str = "inch",
) -> Optional[float]:
    """Paper formula: |AC - BD| / (AC + BD) x 100.

    AC and BD use the same unit, so inch/cm cancels naturally and no extra reference
    length is required. Example from the supplied report: 0.5 / 147.8 x 100 = 0.33%.
    """
    if ac is None or bd is None:
        return None
    ac = float(ac); bd = float(bd)
    total = ac + bd
    if total == 0:
        return None
    return abs(ac - bd) / total * 100


def roll_is_pass(amount: Optional[float], standard: Optional[float]) -> Optional[bool]:
    """Compare the measured skew amount against the width/type acceptance table.

    The supplied paper form defines acceptance as an amount (for example 60-inch
    stripe = 1.50 inch), while the percentage is calculated separately for reporting.
    """
    if amount is None or standard is None:
        return None
    return amount <= standard


def compute_roll_fields(roll: SkewBowingRoll, standard: Optional[float], measurement_unit: str) -> dict:
    pct_before = skew_percent(roll.ac_value_before, roll.bd_value_before)
    pct_after = skew_percent(roll.ac_value_after, roll.bd_value_after)
    amount_before = bow_rule_value(roll.ac_value_before, roll.bd_value_before)
    amount_after = bow_rule_value(roll.ac_value_after, roll.bd_value_after)
    return {
        "skew_amount_before": amount_before,
        "skew_amount_after": amount_after,
        "skew_percent_before": pct_before,
        "skew_percent_after": pct_after,
        "is_pass_before": roll_is_pass(amount_before, standard),
        "is_pass_after": roll_is_pass(amount_after, standard),
    }


def compute_report_stats(rolls: List[SkewBowingRoll], standard: Optional[float], measurement_unit: str) -> dict:
    before_vals = [skew_percent(r.ac_value_before, r.bd_value_before) for r in rolls]
    before_vals = [v for v in before_vals if v is not None]
    after_vals = [skew_percent(r.ac_value_after, r.bd_value_after) for r in rolls]
    after_vals = [v for v in after_vals if v is not None]
    before_amounts = [bow_rule_value(r.ac_value_before, r.bd_value_before) for r in rolls]
    before_amounts = [v for v in before_amounts if v is not None]
    after_amounts = [bow_rule_value(r.ac_value_after, r.bd_value_after) for r in rolls]
    after_amounts = [v for v in after_amounts if v is not None]

    avg_before = sum(before_vals) / len(before_vals) if before_vals else None
    avg_after = sum(after_vals) / len(after_vals) if after_vals else None

    return {
        "avg_percent_before": avg_before,
        "avg_percent_after": avg_after,
        "pass_count_before": sum(1 for v in before_amounts if standard is not None and v <= standard),
        "fail_count_before": sum(1 for v in before_amounts if standard is not None and v > standard),
        "pass_count_after": sum(1 for v in after_amounts if standard is not None and v <= standard),
        "fail_count_after": sum(1 for v in after_amounts if standard is not None and v > standard),
        "is_pass_before": (all(v <= standard for v in before_amounts)) if (before_amounts and standard is not None) else None,
        "is_pass_after": (all(v <= standard for v in after_amounts)) if (after_amounts and standard is not None) else None,
    }
