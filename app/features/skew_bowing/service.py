"""Skew & Bowing calculations.

Before wash: Result % = Measurement Variation / Length x 100
After wash:  Result % = 2 x (AC - BD) / (AC + BD) x 100
Both results are compared with the single STD entered in the app: Result <= STD = PASS.
"""
from typing import List, Optional

from app.features.skew_bowing.models import SkewBowingRoll

# Printed on the report only (no calculation uses it).
ACCEPTANCE_TABLE = (
    ('45"', '1.0"', '0.75"'),
    ('60"', '2.0"', '1.50"'),
    ('90"', '3.0"', '2.00"'),
)


def before_percent(length: Optional[float], variation: Optional[float]) -> Optional[float]:
    if length is None or variation is None:
        return None
    length = float(length)
    if length == 0:
        return None
    return abs(float(variation)) / length * 100


def after_percent(ac: Optional[float], bd: Optional[float]) -> Optional[float]:
    if ac is None or bd is None:
        return None
    ac = float(ac); bd = float(bd)
    total = ac + bd
    if total == 0:
        return None
    return abs(2 * (ac - bd) / total) * 100


def roll_before_percent(roll: SkewBowingRoll) -> Optional[float]:
    pct = before_percent(roll.length_before, roll.variation_before)
    if pct is None:
        # Older rolls saved before-wash AC/BD; keep them readable.
        pct = after_percent(roll.ac_value_before, roll.bd_value_before)
    return pct


def roll_after_percent(roll: SkewBowingRoll) -> Optional[float]:
    return after_percent(roll.ac_value_after, roll.bd_value_after)


def roll_is_pass(percent: Optional[float], standard: Optional[float]) -> Optional[bool]:
    if percent is None or standard is None:
        return None
    return percent <= standard


def compute_roll_fields(roll: SkewBowingRoll, standard: Optional[float], measurement_unit: str = "") -> dict:
    pct_before = roll_before_percent(roll)
    pct_after = roll_after_percent(roll)
    return {
        "skew_percent_before": pct_before,
        "skew_percent_after": pct_after,
        "is_pass_before": roll_is_pass(pct_before, standard),
        "is_pass_after": roll_is_pass(pct_after, standard),
    }


def compute_report_stats(rolls: List[SkewBowingRoll], standard: Optional[float], measurement_unit: str = "") -> dict:
    before_vals = [v for v in (roll_before_percent(r) for r in rolls) if v is not None]
    after_vals = [v for v in (roll_after_percent(r) for r in rolls) if v is not None]

    def counts(vals):
        if standard is None:
            return 0, 0, None
        passed = sum(1 for v in vals if v <= standard)
        failed = len(vals) - passed
        return passed, failed, (failed == 0) if vals else None

    pass_b, fail_b, ok_b = counts(before_vals)
    pass_a, fail_a, ok_a = counts(after_vals)
    return {
        "avg_percent_before": sum(before_vals) / len(before_vals) if before_vals else None,
        "avg_percent_after": sum(after_vals) / len(after_vals) if after_vals else None,
        "pass_count_before": pass_b,
        "fail_count_before": fail_b,
        "pass_count_after": pass_a,
        "fail_count_after": fail_a,
        "is_pass_before": ok_b,
        "is_pass_after": ok_a,
    }
