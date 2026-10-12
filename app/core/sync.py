"""Offline-sync conflict rule shared by every roll upsert.

The app stamps each roll with `client_updated_at` when the inspector saves it on
the phone (not when it reaches the server). If two copies of the same roll arrive
out of order, the one entered last wins; an older copy is ignored and the
response carries `X-Sync-Stale: 1` so the app drops it from its outbox.
Saves without a stamp (dashboard, older app builds) count as made "now", so a
phone copy queued before them can no longer overwrite them later.
"""
import functools
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Response
from sqlalchemy.exc import IntegrityError

SYNC_STALE_HEADER = "X-Sync-Stale"

# Phones with a clock running ahead would otherwise "win" against every later edit.
MAX_CLOCK_AHEAD = timedelta(minutes=2)


def as_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def client_stamp(value: Optional[datetime]) -> datetime:
    """Entry time used for last-write-wins, never later than the server clock."""
    now = datetime.utcnow()
    stamp = as_utc_naive(value)
    if stamp is None or stamp > now + MAX_CLOCK_AHEAD:
        return now
    return stamp


def is_stale(existing, client_updated_at: Optional[datetime]) -> bool:
    if existing is None:
        return False
    stored = getattr(existing, "client_updated_at", None)
    if stored is None:
        return False
    return client_stamp(client_updated_at) < min(stored, datetime.utcnow())


def mark_stale(response: Optional[Response]) -> None:
    if response is not None:
        response.headers[SYNC_STALE_HEADER] = "1"


CONFLICT_ATTEMPTS = 3  # a roll save inserts up to two unique rows (module roll + inspection_reports)


def retry_on_conflict(endpoint):
    """Two devices creating the same report/roll at once collide on its unique key.
    Replaying makes the loser update the row the winner just inserted."""

    @functools.wraps(endpoint)
    def wrapper(*args, **kwargs):
        db = kwargs.get("db")
        for attempt in range(CONFLICT_ATTEMPTS):
            try:
                return endpoint(*args, **kwargs)
            except IntegrityError:
                if db is None or attempt == CONFLICT_ATTEMPTS - 1:
                    raise
                db.rollback()

    return wrapper
