"""Short-lived cache for read-heavy aggregate endpoints.

Uses Redis when reachable, otherwise a per-process TTL dict, so the API keeps working
on hosts without Redis. Values must be JSON-serialisable.
"""
import hashlib
import json
import threading
import time
from typing import Any, Callable

from app.core.config import settings

_local: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()
_redis = None
_redis_checked_at = 0.0
_REDIS_RETRY_SECONDS = 60


def _redis_client():
    global _redis, _redis_checked_at
    if _redis is not None:
        return _redis
    now = time.monotonic()
    if now - _redis_checked_at < _REDIS_RETRY_SECONDS:
        return None
    _redis_checked_at = now
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=0.3, socket_timeout=0.3)
        client.ping()
        _redis = client
    except Exception:
        _redis = None
    return _redis


def make_key(prefix: str, **params: Any) -> str:
    raw = json.dumps({k: str(v) for k, v in sorted(params.items()) if v is not None}, sort_keys=True)
    return f"interfab:{prefix}:{hashlib.sha1(raw.encode()).hexdigest()}"


def cached(key: str, ttl_seconds: int, producer: Callable[[], Any]) -> Any:
    client = _redis_client()
    if client is not None:
        try:
            hit = client.get(key)
            if hit is not None:
                return json.loads(hit)
        except Exception:
            pass
    else:
        with _lock:
            item = _local.get(key)
            if item and item[0] > time.monotonic():
                return json.loads(item[1])

    value = producer()
    payload = json.dumps(value, default=str)
    if client is not None:
        try:
            client.setex(key, ttl_seconds, payload)
        except Exception:
            pass
    else:
        with _lock:
            if len(_local) > 2000:
                _local.clear()
            _local[key] = (time.monotonic() + ttl_seconds, payload)
    return json.loads(payload)


def invalidate_prefix(prefix: str) -> None:
    full = f"interfab:{prefix}:"
    client = _redis_client()
    if client is not None:
        try:
            for k in client.scan_iter(match=f"{full}*", count=500):
                client.delete(k)
        except Exception:
            pass
    with _lock:
        for k in [k for k in _local if k.startswith(full)]:
            _local.pop(k, None)
