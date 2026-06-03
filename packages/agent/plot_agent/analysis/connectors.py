"""Injected connector bundle for the orchestrator (Phase 7 §C).

The orchestrator never constructs connectors itself — they are INJECTED so tests pass
mocks and the run makes ZERO live network calls. Two things are injected:

* a :class:`~plot_connectors.ULDKConnector` (parcel resolve), and
* a :class:`RiskLayerSource` that, given a :class:`~plot_envelope.RiskKind` and a bbox,
  returns a :class:`~plot_connectors.NormalizedResult` (the fetched features, or a status
  of ``not_detected`` / ``source_unavailable``).

The production :class:`WFSRiskLayerSource` drives the generic WFS connector for each MVP
profile from ``profiles.PL`` (the registry). A ``source_unavailable`` from any layer never
fails the whole run — the orchestrator records it as an unknown and continues
(NFR-REL-001/010); the distinction from ``not_detected`` is preserved (§21).

This module imports ``plot_connectors`` + ``plot_envelope``: that is allowed for the
orchestration layer (Phase 7 §C). It keeps connectors↛rules / rules↛connectors intact.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from plot_connectors import (
    BBox,
    NormalizedResult,
    ResultStatus,
    SourceUnavailable,
    ULDKConnector,
    WFSConnector,
    WFSQuery,
)
from plot_connectors.base.connector import SourceProfile
from plot_envelope import RiskKind


@dataclass(frozen=True)
class RiskLayerFetch:
    """Outcome of fetching one risk theme (kept distinct: ok / not_detected / unavailable)."""

    kind: RiskKind
    status: ResultStatus
    result: NormalizedResult | None
    source_id: str
    detail: str = ""


class RiskLayerSource(ABC):
    """Abstract source of risk-layer features for a bbox (injectable for tests)."""

    @abstractmethod
    async def fetch(self, kind: RiskKind, bbox: BBox) -> RiskLayerFetch:
        """Fetch the features for ``kind`` within ``bbox``.

        Implementations MUST translate a transport failure / open circuit into a
        :class:`RiskLayerFetch` with ``status=SOURCE_UNAVAILABLE`` (never raise past here),
        so one dead source can never fail the whole analysis (NFR-REL-001/010).
        """


@dataclass
class WFSRiskLayerSource(RiskLayerSource):
    """Production risk-layer source: one WFS connector per theme, from ``profiles.PL``.

    ``connectors`` maps a :class:`RiskKind` to a configured :class:`WFSConnector`. Themes
    without a connector are reported ``source_unavailable`` (we never invent a clean
    answer for a theme we did not query, §21). ``layer_names`` maps a kind to the WFS
    feature-type to request (defaults to the profile's first declared layer).
    """

    connectors: dict[RiskKind, WFSConnector]
    layer_names: dict[RiskKind, str]
    max_features: int = 200

    async def fetch(self, kind: RiskKind, bbox: BBox) -> RiskLayerFetch:
        connector = self.connectors.get(kind)
        if connector is None:
            return RiskLayerFetch(
                kind=kind,
                status=ResultStatus.SOURCE_UNAVAILABLE,
                result=None,
                source_id="no_source",
                detail=f"no connector configured for theme {kind.value!r}",
            )
        layer = self.layer_names.get(kind) or _first_layer(connector.profile)
        query = WFSQuery(layer=layer, bbox=bbox, max_features=self.max_features)
        try:
            raw = await connector.fetch(query)
        except SourceUnavailable as exc:
            # Timeout / 5xx / open circuit → source_unavailable, NOT not_detected.
            return RiskLayerFetch(
                kind=kind,
                status=ResultStatus.SOURCE_UNAVAILABLE,
                result=None,
                source_id=connector.source_id,
                detail=str(exc),
            )
        result = connector.normalize(raw)
        return RiskLayerFetch(
            kind=kind,
            status=result.status,
            result=result,
            source_id=connector.source_id,
            detail="",
        )


def _first_layer(profile: SourceProfile) -> str:
    return profile.layers[0] if profile.layers else profile.source_id


@dataclass
class Connectors:
    """Injected connector bundle for ``run_quick_screening`` (Phase 7 §C).

    * ``uldk`` — parcel resolver (required).
    * ``risk_layers`` — a :class:`RiskLayerSource` for the MVP risk themes (required;
      tests inject a mock or a :class:`WFSRiskLayerSource` over respx-mocked connectors).
    """

    uldk: ULDKConnector
    risk_layers: RiskLayerSource
