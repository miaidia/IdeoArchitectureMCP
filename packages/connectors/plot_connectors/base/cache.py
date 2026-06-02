"""Pluggable connector cache (Phase 6 §6.1.A.5; F-0085–0089).

The default backend is **in-memory** so tests need no Redis. An optional Redis backend
lazy-imports ``redis`` only when selected. Cache keys are built by :func:`cache_key`
and include the bbox and the source *version* so a source update or a different extent
never returns stale data (F-0085, F-0090).

Snapshots of the raw blob are handled separately via the ``plot_reports`` ArtifactStore
(filesystem default) — this cache holds normalized/serialized values only.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from plot_connectors.base.models import BBox


def cache_key(
    source_id: str,
    *,
    source_version: str,
    operation: str,
    bbox: BBox | None = None,
    extra: str = "",
) -> str:
    """Build a stable cache key including source version + bbox (F-0085/0090).

    The key is ``<source_id>:<operation>:<short-hash>`` where the hash covers the source
    version, bbox extent, and any extra discriminator (e.g. layer name / parcel id).
    """
    parts = [source_id, source_version, operation, extra]
    if bbox is not None:
        parts.append(f"{bbox.crs}|{bbox.to_param()}")
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{source_id}:{operation}:{digest}"


@runtime_checkable
class Cache(Protocol):
    """Pluggable cache interface (sync get/set of bytes)."""

    def get(self, key: str) -> bytes | None:
        """Return cached bytes for ``key`` or ``None`` on miss."""
        ...

    def set(self, key: str, value: bytes, *, ttl_s: int | None = None) -> None:
        """Store ``value`` under ``key`` with an optional TTL in seconds."""
        ...


class InMemoryCache:
    """Process-local in-memory cache (the default; no Redis needed for tests).

    TTL is best-effort: an entry past its TTL is treated as a miss and evicted lazily on
    read. Suitable for a single process / test run, not for cross-worker sharing.
    """

    def __init__(self) -> None:
        # key -> (value, expiry_monotonic_or_None)
        self._store: dict[str, tuple[bytes, float | None]] = {}

    def get(self, key: str) -> bytes | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry is not None and _now() > expiry:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: bytes, *, ttl_s: int | None = None) -> None:
        expiry = _now() + ttl_s if ttl_s is not None else None
        self._store[key] = (value, expiry)

    def clear(self) -> None:
        """Drop all entries (test helper)."""
        self._store.clear()


class RedisCache:  # pragma: no cover - requires a live Redis
    """Optional Redis-backed cache; lazy-imports ``redis`` only when constructed.

    The default backend is :class:`InMemoryCache`; switch to Redis explicitly in
    production where cross-worker cache sharing / warming is wanted (F-0087/0088).
    """

    def __init__(self, url: str, *, namespace: str = "plot:conn:") -> None:
        import redis  # lazy: only needed for the Redis backend

        self._client = redis.Redis.from_url(url)
        self._ns = namespace

    def get(self, key: str) -> bytes | None:
        value = self._client.get(self._ns + key)
        return value if isinstance(value, bytes) else None

    def set(self, key: str, value: bytes, *, ttl_s: int | None = None) -> None:
        self._client.set(self._ns + key, value, ex=ttl_s)


def _now() -> float:
    """Monotonic clock for TTL accounting."""
    import time

    return time.monotonic()
