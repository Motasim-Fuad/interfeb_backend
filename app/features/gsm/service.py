"""
Mirrors the calculation logic that used to live in Flutter's GSMCheckReportEntity getters
(avgActualGsmBefore, minGsmBefore, avgOverLessBeforePct, passBefore, etc.) - now computed
once here so the Flutter app, the future Admin Dashboard, and anyone else consuming this API
all get the same numbers instead of each re-implementing the math. The tolerance % is entered
in the app; without it there is no PASS/FAIL.
"""
from typing import List

from app.features.gsm.models import GsmCheckRoll


def over_less_pct(actual: float, standard: float) -> float:
    if not standard:
        return 0.0
    return ((actual - standard) / standard) * 100


def required_after(report) -> float:
    """After-wash Required GSM; reports without one use the before-wash value."""
    return float(report.standard_gsm_after or report.standard_gsm or 0)


def compute_report_stats(rolls: List[GsmCheckRoll], standard_gsm: float, tolerance_pct: float | None,
                         standard_gsm_after: float | None = None) -> dict:
    tolerance = tolerance_pct
    standard_after = standard_gsm_after or standard_gsm

    before_vals = [float(r.actual_gsm_before) for r in rolls if r.actual_gsm_before is not None]
    after_vals = [float(r.actual_gsm_after) for r in rolls if r.actual_gsm_after is not None]

    avg_gsm_before = sum(before_vals) / len(before_vals) if before_vals else None
    avg_gsm_after = sum(after_vals) / len(after_vals) if after_vals else None

    # Without a required GSM there is no deviation to judge; 0% would wrongly read as PASS.
    avg_over_less_before_pct = (
        sum(over_less_pct(v, standard_gsm) for v in before_vals) / len(before_vals)
        if before_vals and standard_gsm else None
    )
    avg_over_less_after_pct = (
        sum(over_less_pct(v, standard_after) for v in after_vals) / len(after_vals)
        if after_vals and standard_after else None
    )

    return {
        "avg_gsm_before": avg_gsm_before,
        "avg_gsm_after": avg_gsm_after,
        "min_gsm_before": min(before_vals) if before_vals else None,
        "max_gsm_before": max(before_vals) if before_vals else None,
        "min_gsm_after": min(after_vals) if after_vals else None,
        "max_gsm_after": max(after_vals) if after_vals else None,
        "avg_over_less_before_pct": avg_over_less_before_pct,
        "avg_over_less_after_pct": avg_over_less_after_pct,
        "pass_before": abs(avg_over_less_before_pct) <= tolerance if avg_over_less_before_pct is not None and tolerance is not None else None,
        "pass_after": abs(avg_over_less_after_pct) <= tolerance if avg_over_less_after_pct is not None and tolerance is not None else None,
    }
