"""Prometheus metrics for the HTTP API (Phase 14B; F-0470, F-0526).

Registered on the SHARED process registry (``plot_shared.get_metrics_registry``)
so a combined API+MCP process exposes one scrape. Creation is idempotent —
test suites re-import app modules, and double registration on a
``CollectorRegistry`` raises ``ValueError``.
"""

from __future__ import annotations

from typing import Any

from plot_shared import get_metrics_registry
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

_requests: Any = None
_latency: Any = None


def _build(registry: CollectorRegistry) -> tuple[Any, Any]:
    requests = Counter(
        "plot_api_requests_total",
        "API requests by endpoint label and response status.",
        ["endpoint", "status"],
        registry=registry,
    )
    latency = Histogram(
        "plot_api_request_seconds",
        "API request latency by endpoint label.",
        ["endpoint"],
        registry=registry,
    )
    return requests, latency


def _ensure() -> None:
    global _requests, _latency
    if _requests is not None:
        return
    try:
        _requests, _latency = _build(get_metrics_registry())
    except ValueError:
        # Collectors already registered on the shared registry by a STALE copy
        # of this module (test re-imports). Observations from this copy go to a
        # private registry; the shared scrape stays owned by the first copy.
        _requests, _latency = _build(CollectorRegistry())


def observe_request(endpoint: str, status: int, seconds: float) -> None:
    _ensure()
    _requests.labels(endpoint=endpoint, status=str(status)).inc()
    _latency.labels(endpoint=endpoint).observe(seconds)


def metrics_payload() -> tuple[bytes, str]:
    """The Prometheus text exposition of the shared registry."""
    return generate_latest(get_metrics_registry()), CONTENT_TYPE_LATEST
