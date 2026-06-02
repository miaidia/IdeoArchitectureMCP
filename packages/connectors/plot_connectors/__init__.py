"""External data connectors for the Plot Analyzer (Phase 6, §8.2 F-0041–0096).

Public surface:

* Framework (``plot_connectors.base``): the :class:`Connector` ABC + :class:`SourceProfile`
  (the §20.3 fetch/normalize/snapshot/healthcheck contract), resilience (httpx async
  client, tenacity retry, pybreaker circuit breaker), the :class:`EgressAllowlist` +
  SSRF guard (F-0488/0489), the pluggable :class:`Cache` (in-memory default), OGC
  GetCapabilities introspection (capabilities injectable), and the
  :class:`ResultStatus`/:class:`RawResult`/:class:`NormalizedResult`/:class:`HealthStatus`
  value objects.
* :class:`ULDKConnector` — the fully-implemented MVP-critical parcel resolver (F-0041).
* Generic OGC adapters: :class:`WFSConnector` / :class:`WMSConnector` / :class:`WCSConnector`.
* :class:`AppGmlConnector` — APP/GML fetch+snapshot+record (deep parse is Phase 8).
* ``profiles.PL`` — the MVP must-have source-profile registry (§32).

Decoupling rule (Phase 1.4 / §9.4): this package must NOT import ``plot_rules``. It may
depend on ``plot_domain`` / ``plot_shared`` / ``plot_geo`` / ``plot_reports``.
"""

from __future__ import annotations

from plot_connectors.app_gml import AppGmlConnector, AppGmlQuery
from plot_connectors.base import (
    BBox,
    Cache,
    Connector,
    ConnectorError,
    EgressAllowlist,
    EgressBlocked,
    HealthStatus,
    InMemoryCache,
    LicenseError,
    NormalizedResult,
    OGCCapabilities,
    OGCLayer,
    RawResult,
    RedisCache,
    ResilienceConfig,
    ResultStatus,
    SourceProfile,
    SourceUnavailable,
    build_async_client,
    cache_key,
    default_allowlist,
    guarded_call,
    introspect_capabilities,
    is_private_host,
    make_breaker,
    make_retryer,
)
from plot_connectors.uldk import (
    ParcelByIdQuery,
    ParcelByXYQuery,
    ULDKConnector,
    uldk_profile,
)
from plot_connectors.wcs import WCSConnector, WCSQuery
from plot_connectors.wfs import WFSConnector, WFSQuery
from plot_connectors.wms import WMSConnector, WMSQuery

__version__ = "0.1.0"

__all__ = [
    # framework value objects
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
    # framework primitives
    "Cache",
    "Connector",
    "EgressAllowlist",
    "InMemoryCache",
    "OGCCapabilities",
    "OGCLayer",
    "RedisCache",
    "ResilienceConfig",
    "SourceProfile",
    "build_async_client",
    "cache_key",
    "default_allowlist",
    "guarded_call",
    "introspect_capabilities",
    "is_private_host",
    "make_breaker",
    "make_retryer",
    # connectors
    "ULDKConnector",
    "ParcelByIdQuery",
    "ParcelByXYQuery",
    "uldk_profile",
    "WFSConnector",
    "WFSQuery",
    "WMSConnector",
    "WMSQuery",
    "WCSConnector",
    "WCSQuery",
    "AppGmlConnector",
    "AppGmlQuery",
]
