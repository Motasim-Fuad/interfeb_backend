import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.cache import _redis_client

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """The proxy in front of the API appends the caller's address last; anything to its
    left came from the client and can be forged, so only the right-most entry is trusted."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _too_many(key: str, max_hits: int, window_seconds: int) -> bool:
    client = _redis_client()
    if client is not None:
        try:
            redis_key = f"interfab:rl:{key}:{int(time.time() // window_seconds)}"
            hits = client.incr(redis_key)
            if hits == 1:
                client.expire(redis_key, window_seconds + 1)
            return hits > max_hits
        except Exception:
            pass
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= max_hits:
            return True
        q.append(now)
        return False


def rate_limiter(bucket: str, max_hits: int, window_seconds: int, global_max_hits: int = 0):
    """Sliding/fixed window limiter for brute-force sensitive or expensive endpoints.
    Shared across workers through Redis when available. `global_max_hits` also caps the
    bucket as a whole, so rotating source addresses does not help an attacker."""

    def _check(request: Request) -> None:
        if _too_many(f"{bucket}:{client_ip(request)}", max_hits, window_seconds) or (
            global_max_hits and _too_many(f"{bucket}:*", global_max_hits, window_seconds)
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait a minute and try again.",
            )

    return _check
