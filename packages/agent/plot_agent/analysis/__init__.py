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
    Connectors,
    RiskLayerFetch,
    RiskLayerSource,
    WFSRiskLayerSource,
)
from plot_agent.analysis.factory import (
    DEFAULT_STORE,
    AnalysisStore,
    build_default_connectors,
)
from plot_agent.analysis.quick_screening import (
    RISK_LAYER_PROFILE_MAP,
    run_quick_screening,
)

__all__ = [
    "Connectors",
    "RiskLayerFetch",
    "RiskLayerSource",
    "WFSRiskLayerSource",
    "run_quick_screening",
    "RISK_LAYER_PROFILE_MAP",
    "build_default_connectors",
    "AnalysisStore",
    "DEFAULT_STORE",
]
