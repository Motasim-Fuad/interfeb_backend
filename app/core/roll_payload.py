"""Shared roll upsert helpers so Before/After wash data is never wiped by a None field."""
from datetime import date

from fastapi import HTTPException


def merge_roll_fields(roll, data: dict) -> None:
    """Apply payload fields, but keep existing values when the client sends None."""
    for key, value in data.items():
        if value is None and getattr(roll, key, None) is not None:
            continue
        setattr(roll, key, value)


def stamp_wash_dates(roll, data: dict, before_keys: tuple[str, ...], after_keys: tuple[str, ...]) -> None:
    """Fill missing before/after wash dates on first save so printable reports are not blank."""
    today = date.today()
    if any(data.get(key) is not None for key in before_keys):
        stored = getattr(roll, "before_wash_date", None) if roll is not None else None
        if not data.get("before_wash_date") and stored is None:
            data["before_wash_date"] = today
    if any(data.get(key) is not None for key in after_keys):
        stored = getattr(roll, "after_wash_date", None) if roll is not None else None
        if not data.get("after_wash_date") and stored is None:
            data["after_wash_date"] = today


def require_before_wash_first(existing, data: dict, before_keys: tuple[str, ...], after_keys: tuple[str, ...]) -> None:
    """After-wash values are allowed only after Before Wash exists for the same roll."""
    after_present = any(data.get(key) is not None for key in after_keys)
    if not after_present:
        return
    before_present = False
    for key in before_keys:
        incoming = data.get(key)
        stored = getattr(existing, key, None) if existing is not None else None
        if incoming is not None or stored is not None:
            before_present = True
            break
    if not before_present:
        raise HTTPException(
            status_code=422,
            detail="Enter Before Wash data for this roll before After Wash.",
        )
