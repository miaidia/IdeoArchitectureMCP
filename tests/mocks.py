"""Reusable test fixtures for the Phase 7 analysis pipeline (ZERO live network).

Two flavours, both clearly marked TEST FIXTURES (no real source data):

* :func:`mock_connectors` — a fast, pure-Python :class:`Connectors` bundle (a mock ULDK
  + a mock :class:`RiskLayerSource`) for unit / MCP tests. Use the builders to control the
  parcel geometry and per-theme outcomes (ok-with-features / not_detected / unavailable).
* :func:`respx_uldk` / :func:`respx_wfs_source` — REUSE of the Phase 6 connector mocking
  approach: real :class:`ULDKConnector` / :class:`WFSConnector` driven over ``respx``
  routes + injected owslib capabilities XML (the Phase 6 fixtures). The MVP-gate
  end-to-end test uses these so it exercises the real connector code path with zero network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import respx
from plot_agent.analysis import Connectors
from plot_agent.analysis.connectors import RiskLayerFetch, RiskLayerSource, WFSRiskLayerSource
from plot_connectors import (
    BBox,
    InMemoryCache,
    NormalizedResult,
    ResultStatus,
    SourceUnavailable,
    ULDKConnector,
    WFSConnector,
    uldk_profile,
)
from plot_connectors.base.egress import EgressAllowlist
from plot_connectors.profiles import get_profile
from plot_domain import (
    EvidenceItem,
    Freshness,
    GeometryPrecision,
    LegalStatus,
    SourceRecord,
    SourceType,
)
from plot_envelope import RiskKind
from plot_reports import LocalArtifactStore

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"

# A small synthetic parcel (40 x 30 m, EPSG:2180) used by the mock ULDK — TEST FIXTURE.
DEFAULT_PARCEL_WKT = "POLYGON((630880 497170,630920 497170,630920 497200,630880 497200,630880 497170))"


def allowlist_no_dns() -> EgressAllowlist:
    """Phase 6 allowlist that enforces hosts but skips DNS (no real resolution in tests)."""
    return EgressAllowlist.from_settings(resolve_dns=False)


def _test_source_record(source_id: str, *, legal: LegalStatus, conf: float) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        source_type=SourceType.OFFICIAL_REGISTER,
        publisher="TEST FIXTURE",
        url_or_origin="test://fixture",
        retrieved_at=datetime.now(UTC),
        license="TEST FIXTURE",
        legal_status=legal,
        geometry_precision=GeometryPrecision.TOPOGRAPHIC,
        freshness=Freshness.CURRENT,
        confidence=conf,
        notes="TEST FIXTURE — not real source data.",
    )


# --------------------------------------------------------------------------- #
# Pure mock bundle
# --------------------------------------------------------------------------- #
@dataclass
class MockULDKConnector:
    """A mock ULDK that returns a fixed parcel (or a timeout). source_id mimics ULDK."""

    source_id: str = "pl.gugik.uldk"
    parcel_wkt: str = DEFAULT_PARCEL_WKT
    fail: bool = False

    async def resolve(self, query: Any, snapshot: bool = False) -> NormalizedResult:
        if self.fail:
            raise SourceUnavailable("TEST FIXTURE: ULDK timeout")
        rec = _test_source_record("pl.gugik.uldk:test", legal=LegalStatus.BINDING, conf=0.95)
        ev = EvidenceItem(
            id="ev:uldk:geom",
            analysis_id="",
            source_id="pl.gugik.uldk:test",
            subject_type="parcel",
            subject_id="parcel:test",
            claim="parcel_geometry_wkt",
            value_json={"crs": "EPSG:2180"},
            confidence=0.95,
        )
        return NormalizedResult(
            status=ResultStatus.OK,
            source_record=rec,
            evidence=[ev],
            payload={
                "parcel": {
                    "id": "parcel:141201_1.0001.1867/2",
                    "teryt": "141201_1.0001.1867/2",
                    "number": "1867/2",
                    "geometry_wkt": self.parcel_wkt,
                    "input_crs": "EPSG:2180",
                    "administrative_context": {
                        "teryt": "141201",
                        "commune": "TestGmina",
                        "county": "1412",
                        "voivodeship": "14",
                        "district": "0001",
                    },
                }
            },
        )


@dataclass
class MockRiskLayerSource(RiskLayerSource):
    """A mock risk-layer source with per-theme outcomes (TEST FIXTURE).

    ``feature_geoms`` maps a theme → list of GeoJSON geometries to return as ``ok``.
    ``unavailable`` is the set of themes that fail (``source_unavailable``). Any theme not
    listed in either is ``not_detected`` (checked & clear).
    """

    feature_geoms: dict[RiskKind, list[dict[str, Any]]] = field(default_factory=dict)
    unavailable: set[RiskKind] = field(default_factory=set)

    async def fetch(self, kind: RiskKind, bbox: BBox) -> RiskLayerFetch:
        if kind in self.unavailable:
            return RiskLayerFetch(
                kind=kind,
                status=ResultStatus.SOURCE_UNAVAILABLE,
                result=None,
                source_id=f"src.{kind.value}:test",
                detail="TEST FIXTURE: simulated timeout",
            )
        rec = _test_source_record(f"src.{kind.value}:test", legal=LegalStatus.INFORMATIVE, conf=0.8)
        geoms = self.feature_geoms.get(kind, [])
        if not geoms:
            return RiskLayerFetch(
                kind=kind,
                status=ResultStatus.NOT_DETECTED,
                result=NormalizedResult(status=ResultStatus.NOT_DETECTED, source_record=rec),
                source_id=rec.source_id,
            )
        evidence = [
            EvidenceItem(
                id=f"ev:{kind.value}:{i}",
                analysis_id="",
                source_id=rec.source_id,
                subject_type="wfs_feature",
                subject_id=f"{kind.value}#{i}",
                claim="wfs_feature",
                value_json={"layer": kind.value},
                confidence=0.8,
                geometry=g,
            )
            for i, g in enumerate(geoms)
        ]
        return RiskLayerFetch(
            kind=kind,
            status=ResultStatus.OK,
            result=NormalizedResult(status=ResultStatus.OK, source_record=rec, evidence=evidence),
            source_id=rec.source_id,
        )


def mock_connectors(
    *,
    parcel_wkt: str = DEFAULT_PARCEL_WKT,
    uldk_fail: bool = False,
    feature_geoms: dict[RiskKind, list[dict[str, Any]]] | None = None,
    unavailable: set[RiskKind] | None = None,
) -> Connectors:
    """Build a pure-mock :class:`Connectors` bundle (zero network)."""
    return Connectors(
        uldk=MockULDKConnector(parcel_wkt=parcel_wkt, fail=uldk_fail),  # type: ignore[arg-type]
        risk_layers=MockRiskLayerSource(
            feature_geoms=feature_geoms or {}, unavailable=unavailable or set()
        ),
    )


# --------------------------------------------------------------------------- #
# REUSE of Phase 6 connector mocking (respx + injected owslib XML)
# --------------------------------------------------------------------------- #
def respx_uldk(tmp_path: Path) -> ULDKConnector:
    """A real ULDKConnector wired with the Phase 6 allowlist + local artifact store.

    The caller wraps calls in ``respx.mock`` and registers the ULDK route returning the
    Phase 6 ``uldk_get_parcel_by_*.txt`` fixture (see the e2e test). No live network.
    """
    return ULDKConnector(
        uldk_profile(),
        allowlist=allowlist_no_dns(),
        cache=InMemoryCache(),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
    )


def respx_wfs_source(tmp_path: Path, themes: dict[RiskKind, str]) -> WFSRiskLayerSource:
    """A real :class:`WFSRiskLayerSource` over real WFS connectors (Phase 6 fixtures).

    ``themes`` maps a :class:`RiskKind` to a ``profiles.PL`` source_id. The connectors use
    the Phase 6 capabilities XML fixture so introspection needs no network; the caller
    registers respx routes for each profile's base_url returning a GetFeature GeoJSON
    body (the Phase 6 ``wfs_getfeature.json`` fixture or an empty FeatureCollection).
    """
    caps_xml = (FIXTURES / "wfs_capabilities.xml").read_bytes()
    connectors: dict[RiskKind, WFSConnector] = {}
    layer_names: dict[RiskKind, str] = {}
    for kind, source_id in themes.items():
        profile = get_profile(source_id)
        connectors[kind] = WFSConnector(
            profile,
            capabilities_xml=caps_xml,
            allowlist=allowlist_no_dns(),
            artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        )
        layer_names[kind] = "gdos:ProtectedSite"  # the layer present in the caps fixture
    return WFSRiskLayerSource(connectors=connectors, layer_names=layer_names)


def respx_route_text(mock: respx.MockRouter, url: str, body: str) -> Any:
    """Register a GET route returning ``body`` as text (Phase 6 pattern)."""
    return mock.get(url).mock(return_value=httpx.Response(200, text=body))


def respx_route_json(mock: respx.MockRouter, url: str, body: str) -> Any:
    """Register a GET route returning ``body`` as JSON (Phase 6 pattern)."""
    return mock.get(url).mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "application/json"})
    )


EMPTY_FEATURE_COLLECTION = '{"type":"FeatureCollection","features":[]}'
