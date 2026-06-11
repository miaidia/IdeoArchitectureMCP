"""Analysis orchestration: the §27 shared use-cases (Phase 7 §C).

``run_quick_screening`` is the orchestration entry point shared by the MCP server and the
HTTP API (§27 — API and MCP call the same domain use-case to avoid logic drift). It
resolves the parcel (ULDK), computes geometry metrics (plot_geo), fetches the MVP risk
layers via injected connectors, overlays them into constraints + a buildable envelope v1
(plot_envelope), classifies red flags + a decision with hard-blocker dominance, and
assembles a schema-conforming :class:`~plot_domain.AnalysisResult`.

Connectors are INJECTED (:class:`~plot_agent.analysis.connectors.Connectors`) so tests
pass mocks and the run makes ZERO live network calls.
"""

from __future__ import annotations

from plot_agent.analysis.connectors import (
    BuildingsFetch,
    Connectors,
    NeighborBuildingsSource,
    RiskLayerFetch,
    RiskLayerSource,
    TerrainFetch,
    TerrainSource,
    WCSTerrainSource,
    WFSNeighborBuildingsSource,
    WFSRiskLayerSource,
)
from plot_agent.analysis.factory import (
    DEFAULT_SITE_CONTEXT_STORE,
    DEFAULT_STORE,
    AnalysisStore,
    SiteContextStore,
    build_default_connectors,
)
from plot_agent.analysis.full_due_diligence import run_full_due_diligence
from plot_agent.analysis.quick_screening import (
    RISK_LAYER_PROFILE_MAP,
    ScreeningInternals,
    run_quick_screening,
    run_screening_with_internals,
)

__all__ = [
    "BuildingsFetch",
    "Connectors",
    "NeighborBuildingsSource",
    "RiskLayerFetch",
    "RiskLayerSource",
    "ScreeningInternals",
    "TerrainFetch",
    "TerrainSource",
    "WCSTerrainSource",
    "WFSNeighborBuildingsSource",
    "WFSRiskLayerSource",
    "run_full_due_diligence",
    "run_quick_screening",
    "run_screening_with_internals",
    "RISK_LAYER_PROFILE_MAP",
    "build_default_connectors",
    "AnalysisStore",
    "SiteContextStore",
    "DEFAULT_STORE",
    "DEFAULT_SITE_CONTEXT_STORE",
]
