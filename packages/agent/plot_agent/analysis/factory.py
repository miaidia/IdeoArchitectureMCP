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

from plot_connectors import ULDKConnector, WFSConnector
from plot_connectors.profiles import get_profile
from plot_domain import AnalysisResult
from plot_envelope import RiskKind

from plot_agent.analysis.connectors import Connectors, WFSRiskLayerSource
from plot_agent.analysis.quick_screening import RISK_LAYER_PROFILE_MAP


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
