"""Caching for provider responses.

Caching is not just a speed trick here -- most free geocoding and routing
tiers are fair-use, and repeatedly asking for the same road distance is the
fastest way to get an IP blocked. An in-process TTL cache is always on;
Redis is used instead when `REDIS_URL` is set, so several workers share one
cache.

Check each provider's terms before raising the TTL: some licences restrict how
long their responses may be stored.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class CacheBackend(Protocol):
    """Minimal cache interface."""

    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...
    def stats(self) -> dict[str, int]: ...


class MemoryCache:
    """Bounded LRU cache with per-entry expiry. Thread-safe enough for uvicorn."""

    def __init__(self, max_entries: int = 5_000) -> None:
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        expires_at, value = entry
        if expires_at < time.time():
            self._store.pop(key, None)
            self._misses += 1
            return None
        self._store.move_to_end(key)
        self._hits += 1
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": len(self._store)}


class RedisCache:
    """Redis-backed cache that degrades to a no-op if Redis is unreachable.

    A cache outage must never take the API down with it.
    """

    def __init__(self, url: str, namespace: str = "tripwise") -> None:
        import redis  # imported lazily so redis stays an optional dependency

        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._namespace = namespace
        self._hits = 0
        self._misses = 0
        self._available = True

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def get(self, key: str) -> Any | None:
        if not self._available:
            return None
        try:
            raw = self._client.get(self._key(key))
        except Exception as exc:  # noqa: BLE001 -- cache must not break requests
            logger.warning("Redis unavailable, falling back to no cache: %s", exc)
            self._available = False
            return None
        if raw is None:
            self._misses += 1
            return None
        self._hits += 1
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if not self._available:
            return
        try:
            self._client.setex(self._key(key), ttl_seconds, json.dumps(value, default=str))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis write failed, continuing without cache: %s", exc)
            self._available = False

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": -1}


_cache: CacheBackend | None = None


def get_cache(redis_url: str | None = None, max_entries: int = 5_000) -> CacheBackend:
    """Process-wide cache singleton."""
    global _cache
    if _cache is None:
        if redis_url:
            try:
                _cache = RedisCache(redis_url)
                logger.info("Provider cache: Redis")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redis cache unavailable (%s); using in-memory cache", exc)
                _cache = MemoryCache(max_entries)
        else:
            _cache = MemoryCache(max_entries)
            logger.info("Provider cache: in-memory")
    return _cache


def reset_cache() -> None:
    """Drop the singleton. Used by tests."""
    global _cache
    _cache = None
