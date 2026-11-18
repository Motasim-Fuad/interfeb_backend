"""Maximum moisture limits (Aqua-boy, 100% scale) and the blend formula.

100% single fibre -> the table value.
Blend: ML(Blend) = ML(A) x A% / 100 + ML(B) x B% / 100 (+ ...), rounded half up
(69.35 -> 69, 69.50 -> 70).
"""
import math
import re
from typing import Iterable, Optional, Sequence, Tuple

# (aliases, maximum moisture %). Longer names first so "artificial leather" wins over "leather".
FIBER_LIMITS: Sequence[Tuple[Tuple[str, ...], float]] = (
    (("artificial leather", "pu", "polyurethane", "pu leather"), 30),
    (("packing paper", "packing carton", "paper", "carton"), 40),
    (("polyacrylinitril", "polyacrylonitrile", "acrylic"), 40),
    (("nylon", "polyamide"), 62),
    (("viscose", "rayon"), 45),
    (("elastane", "spandex", "lycra"), 38),
    (("eva", "peva"), 30),
    (("cotton",), 61),
    (("wool",), 14),
    (("polyester",), 38),
    (("linen",), 77),
    (("acetate",), 53),
    (("silk",), 45),
    (("leather",), 45),
)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def fiber_limit(name: Optional[str]) -> Optional[float]:
    key = _norm(name or "")
    if not key:
        return None
    for aliases, value in FIBER_LIMITS:
        if key in aliases:
            return value
    # "Viscose/Rayon", "Nylon Polyamide": every word names the same fibre.
    found = set()
    for token in key.split():
        hit = next((v for aliases, v in FIBER_LIMITS if token in aliases), None)
        if hit is None:
            return None
        found.add(hit)
    return found.pop() if len(found) == 1 else None


def _pct(value) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def blend_limit(fibers: Iterable[Tuple[Optional[str], object]]) -> Optional[int]:
    """fibers: (fiber name, percent) pairs. None when a fibre is unknown or % don't add to 100."""
    rows = [(name, pct) for name, pct in fibers if (name or "").strip()]
    if not rows:
        return None
    if len(rows) == 1 and _pct(rows[0][1]) is None:
        rows = [(rows[0][0], 100)]
    total_pct = 0.0
    total = 0.0
    for name, pct in rows:
        limit = fiber_limit(name)
        p = _pct(pct)
        if limit is None or p is None:
            return None
        total_pct += p
        total += limit * p / 100
    if abs(total_pct - 100) > 0.5:
        return None
    return round_half_up(total)


def material_moisture_limit(material) -> Optional[float]:
    """Accepted moisture typed on the Material, else the value computed from its composition."""
    from app.features.admin_config.service import parse_optional_float

    typed = parse_optional_float(getattr(material, "accepted_moisture", None))
    if typed is not None:
        return typed
    value = blend_limit(
        (getattr(material, f"fiber_type_{i}", None), getattr(material, f"fiber_{i}_pct", None)) for i in (1, 2, 3)
    )
    return float(value) if value is not None else None
