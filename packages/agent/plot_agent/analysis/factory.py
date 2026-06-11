"""Production connector factory + in-memory analysis-run store (Phase 7 §C / §E).

* :func:`build_default_connectors` constructs the production :class:`Connectors` bundle
  from the ``profiles.PL`` registry — a real ULDK connector + a :class:`WFSRiskLayerSource`
  with one generic WFS connector per MVP risk theme. This is what the MCP server uses by
  default; tests inject their OWN mock bundle instead (so the default suite stays
  zero-network — these connectors are only EXERCISED against live services behind
  ``@pytest.mark.live`` or when Claude Code runs a real session).
* :class:`AnalysisStore` is a process-local store of completed :class:`AnalysisResult`s
  keyed by ``analysis_id`` (the MVP "in-memory/AnalysisRun store" — §E). Real persistence
  (PostGIS) lands in Phase 12.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from plot_connectors import ULDKConnector, WFSConnector
from plot_connectors.profiles import get_profile
from plot_connectors.wcs import WCSConnector
from plot_domain import AnalysisResult
from plot_envelope import RiskKind

from plot_agent.analysis.connectors import (
    Connectors,
    WCSTerrainSource,
    WFSNeighborBuildingsSource,
    WFSRiskLayerSource,
)
from plot_agent.analysis.quick_screening import RISK_LAYER_PROFILE_MAP

#: Phase 12 site-context profiles: terrain raster + neighbor buildings (§32 / §6).
TERRAIN_PROFILE_ID = "pl.geoportal.wcs.nmt"
BUILDINGS_PROFILE_ID = "pl.geoportal.wfs.bdot10k"


def build_default_connectors() -> Connectors:
    """Build the production connector bundle from ``profiles.PL`` (§32 MVP sources)."""
    uldk = ULDKConnector()
    wfs_connectors: dict[RiskKind, WFSConnector] = {}
    layer_names: dict[RiskKind, str] = {}
    for kind, source_id in RISK_LAYER_PROFILE_MAP.items():
        profile = get_profile(source_id)
        if profile.service != "WFS":
            # Only WFS themes are wired in v1; non-WFS themes surface as source_unavailable.
            continue
        wfs_connectors[kind] = WFSConnector(profile)
        if profile.layers:
            layer_names[kind] = profile.layers[0]
    return Connectors(
        uldk=uldk,
        risk_layers=WFSRiskLayerSource(connectors=wfs_connectors, layer_names=layer_names),
        # Phase 12 site-context sources (full_due_diligence): NMT coverage (WCS)
        # + BDOT10k neighbor buildings (WFS). Failures degrade to
        # source_unavailable per-theme, never failing the analysis (NFR-REL-001).
        terrain=WCSTerrainSource(connector=WCSConnector(get_profile(TERRAIN_PROFILE_ID))),
        buildings=WFSNeighborBuildingsSource(
            connector=WFSConnector(get_profile(BUILDINGS_PROFILE_ID))
        ),
    )


@dataclass
class AnalysisStore:
    """Process-local store of completed analyses (MVP; PostGIS persistence in Phase 12)."""

    _results: dict[str, AnalysisResult] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, result: AnalysisResult) -> None:
        with self._lock:
            self._results[result.analysis_id] = result

    def get(self, analysis_id: str) -> AnalysisResult | None:
        with self._lock:
            return self._results.get(analysis_id)

    def has(self, analysis_id: str) -> bool:
        with self._lock:
            return analysis_id in self._results


#: A module-level default store so the MCP server shares one instance across tool calls
#: within a process (it is reset on reload, which is fine for the dev/MVP gate).
DEFAULT_STORE = AnalysisStore()


#: Max analyses retained by :class:`SiteContextStore` (review m4): each typed
#: context can hold live float64 raster grids (terrain windows), so unbounded
#: growth is a memory leak in a long-lived server process.
SITE_CONTEXT_STORE_MAX = 16


@dataclass
class SiteContextStore:
    """Process-local store of typed Phase 12 site contexts, keyed by analysis_id.

    The serialized summary travels in ``AnalysisResult.planning['_site_context']``;
    this store keeps the TYPED object (with live geometries/grids) so the
    masterplan integration (earthworks per building, flood stage clipping,
    heritage interventions, zjazd check — plan delta 1) can consume it without
    re-fetching. Same lifecycle as :data:`DEFAULT_STORE`.

    Capped LRU (review m4): at most :data:`SITE_CONTEXT_STORE_MAX` analyses are
    retained — re-putting refreshes recency, the oldest entry is evicted beyond
    the cap (the contexts carry float64 grids). Durable persistence of site
    contexts (PostGIS/object storage) lands with Phase 13; an evicted analysis
    simply loses its masterplan site checks until re-analyzed.
    """

    max_entries: int = SITE_CONTEXT_STORE_MAX
    _contexts: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, analysis_id: str, context: Any) -> None:
        with self._lock:
            self._contexts.pop(analysis_id, None)  # refresh recency on re-put
            self._contexts[analysis_id] = context
            while len(self._contexts) > self.max_entries:
                self._contexts.pop(next(iter(self._contexts)))  # evict oldest

    def get(self, analysis_id: str) -> Any | None:
        with self._lock:
            return self._contexts.get(analysis_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._contexts)


#: Module-level default site-context store (Phase 12).
DEFAULT_SITE_CONTEXT_STORE = SiteContextStore()
