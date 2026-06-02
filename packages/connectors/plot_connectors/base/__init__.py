"""Connector framework (Phase 6 §6.1.A).

The shared adapter contract (§20.3) — ``fetch`` / ``normalize`` / ``snapshot`` /
``healthcheck`` — plus the resilience (httpx async client, tenacity retry,
pybreaker circuit breaker), security (egress allowlist + SSRF guard), cache, and
OGC-introspection building blocks every connector reuses.

This subpackage depends only on ``plot_domain`` / ``plot_shared`` / ``plot_geo`` /
``plot_reports`` and MUST NOT import ``plot_rules`` (Phase 1.4 / §9.4).
"""

from __future__ import annotations

from plot_connectors.base.cache import (
    Cache,
    InMemoryCache,
    RedisCache,
    cache_key,
)
from plot_connectors.base.connector import (
    Connector,
    SourceProfile,
)
from plot_connectors.base.egress import (
    EgressAllowlist,
    default_allowlist,
    is_private_host,
)
from plot_connectors.base.errors import (
    ConnectorError,
    EgressBlocked,
    LicenseError,
    SourceUnavailable,
)
from plot_connectors.base.models import (
    BBox,
    HealthStatus,
    NormalizedResult,
    RawResult,
    ResultStatus,
)
from plot_connectors.base.ogc import (
    OGCCapabilities,
    OGCLayer,
    introspect_capabilities,
)
from plot_connectors.base.resilience import (
    ResilienceConfig,
    build_async_client,
    guarded_call,
    make_breaker,
    make_retryer,
)

__all__ = [
    # models
    "BBox",
    "HealthStatus",
    "NormalizedResult",
    "RawResult",
    "ResultStatus",
    # errors
    "ConnectorError",
    "EgressBlocked",
    "LicenseError",
    "SourceUnavailable",
    # egress / ssrf
    "EgressAllowlist",
    "default_allowlist",
    "is_private_host",
    # resilience
    "ResilienceConfig",
    "build_async_client",
    "guarded_call",
    "make_breaker",
    "make_retryer",
    # cache
    "Cache",
    "InMemoryCache",
    "RedisCache",
    "cache_key",
    # ogc
    "OGCCapabilities",
    "OGCLayer",
    "introspect_capabilities",
    # connector base
    "Connector",
    "SourceProfile",
]
