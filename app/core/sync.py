"""Offline-sync conflict rule shared by every roll upsert.

The app stamps each roll with `client_updated_at` when the inspector saves it on
the phone (not when it reaches the server). If two copies of the same roll arrive
out of order, the one entered last wins; an older copy is ignored and the
response carries `X-Sync-Stale: 1` so the app drops it from its outbox.
Rows saved without a stamp (older app builds, dashboard) always apply.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import Response

SYNC_STALE_HEADER = "X-Sync-Stale"


def as_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def is_stale(existing, client_updated_at: Optional[datetime]) -> bool:
    if existing is None:
        return False
    incoming = as_utc_naive(client_updated_at)
    stored = getattr(existing, "client_updated_at", None)
    return incoming is not None and stored is not None and incoming < stored


def mark_stale(response: Optional[Response]) -> None:
    if response is not None:
        response.headers[SYNC_STALE_HEADER] = "1"
