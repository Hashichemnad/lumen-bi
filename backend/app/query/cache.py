"""Query result cache.

The key is a hash of the final SQL, its bound values and the identity of every snapshot file it reads.
Row-level security is part of that SQL (the user's allowed values are bound values), so two people with
different access can never share a cached result, and a new sync (a new snapshot file) is never served old
results.
"""

import hashlib
import json
import logging
import threading
import time
import zlib
from collections import OrderedDict
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)
PREFIX = "lumen:q:"


def make_key(sql: str, params: list[Any], files: list[tuple[str, int, int]]) -> str:
    raw = json.dumps([sql, params, files], default=str, separators=(",", ":"))
    return PREFIX + hashlib.sha256(raw.encode()).hexdigest()


class MemoryCache:
    def __init__(self, max_items: int = 500):
        self.items: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self.max_items = max_items
        self.lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self.lock:
            item = self.items.get(key)
            if not item:
                return None
            if item[0] < time.time():
                del self.items[key]
                return None
            self.items.move_to_end(key)
            return item[1]

    def set(self, key: str, value: bytes, ttl: int) -> None:
        with self.lock:
            self.items[key] = (time.time() + ttl, value)
            self.items.move_to_end(key)
            while len(self.items) > self.max_items:
                self.items.popitem(last=False)

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


class RedisCache:
    def __init__(self, url: str):
        import redis

        self.client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)

    def get(self, key: str) -> bytes | None:
        return self.client.get(key)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self.client.set(key, value, ex=ttl)

    def clear(self) -> None:
        for key in self.client.scan_iter(PREFIX + "*", count=1000):
            self.client.delete(key)


_backend: MemoryCache | RedisCache | None = None
_memory = MemoryCache()


def backend() -> MemoryCache | RedisCache | None:
    global _backend
    mode = get_settings().cache_backend
    if mode == "off":
        return None
    if mode == "memory":
        return _memory
    if _backend is None:
        _backend = RedisCache(get_settings().redis_url)
    return _backend


def get(key: str) -> Any | None:
    cache = backend()
    if cache is None:
        return None
    try:
        raw = cache.get(key)
    except Exception as exc:  # a cache outage must never break queries
        log.warning("Query cache unavailable: %s", exc)
        return None
    return json.loads(zlib.decompress(raw)) if raw else None


def put(key: str, value: Any) -> None:
    cache = backend()
    if cache is None:
        return
    try:
        cache.set(key, zlib.compress(json.dumps(value, default=str).encode()), get_settings().cache_seconds)
    except Exception as exc:
        log.warning("Query cache unavailable: %s", exc)


def clear() -> None:
    cache = backend()
    if cache is not None:
        try:
            cache.clear()
        except Exception as exc:
            log.warning("Query cache unavailable: %s", exc)
