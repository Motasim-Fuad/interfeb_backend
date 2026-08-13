from typing import List
from app.features.moisture.models import MoistureCheckRoll


def compute_report_stats(rolls: List[MoistureCheckRoll], acceptance: float | None) -> dict:
    values = []
    passed = 0
    failed = 0
    for r in rolls:
        for value in (r.swatch_rating_before, r.swatch_rating_after):
            if value is None:
                continue
            v = float(value)
            values.append(v)
            if acceptance is not None:
                if v <= acceptance:
                    passed += 1
                else:
                    failed += 1
    return {
        'avg_swatch': sum(values) / len(values) if values else None,
        'pass_count': passed,
        'fail_count': failed,
    }
