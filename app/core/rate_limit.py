import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limiter(bucket: str, max_hits: int, window_seconds: int):
    """Per-process sliding window limiter for brute-force sensitive endpoints."""

    def _check(request: Request) -> None:
        key = f"{bucket}:{client_ip(request)}"
        now = time.monotonic()
        with _lock:
            q = _hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= max_hits:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many attempts. Please wait a minute and try again.",
                )
            q.append(now)

    return _check
