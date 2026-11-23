"""Rate limits per client address (fixed windows), shared by all API workers through Redis.

Used on sign-in, password reset, two-factor codes, OAuth registration and tokens, embed sessions and the
MCP server. If Redis cannot be reached the limit falls back to this process's memory, so an outage never
turns the limit off. RATE_LIMITS=false switches them off (tests only).
"""

import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

_lock = threading.Lock()
_memory: dict[str, tuple[int, int]] = {}  # key -> (window, count)
_redis = None


def _client():
    global _redis
    if _redis is None:
        import redis

        _redis = redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=0.3, socket_connect_timeout=0.3
        )
    return _redis


def hit(key: str, limit: int, seconds: int) -> bool:
    """Count one request; False when over the limit for this window."""
    window = int(time.time() // seconds)
    full = f"lumen:rl:{key}:{window}"
    if get_settings().cache_backend == "redis":
        try:
            r = _client()
            n = r.incr(full)
            if n == 1:
                r.expire(full, seconds + 1)
            return int(n) <= limit
        except Exception:  # noqa: BLE001 - fall back to memory
            pass
    with _lock:
        if len(_memory) > 50_000:
            _memory.clear()
        w, n = _memory.get(full, (window, 0))
        _memory[full] = (window, n + 1)
        return n + 1 <= limit


def reset() -> None:
    with _lock:
        _memory.clear()


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit(name: str, count: int, seconds: int, key: Callable[[Request], str] = client_key):
    """FastAPI dependency: at most `count` requests per `seconds` per client address."""

    def check(request: Request) -> None:
        if not get_settings().rate_limits:
            return
        if not hit(f"{name}:{key(request)}", count, seconds):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests. Wait a minute and try again.",
                headers={"Retry-After": str(seconds)},
            )

    return check
