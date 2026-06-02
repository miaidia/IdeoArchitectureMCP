"""Connector framework + core-connector contract tests (Phase 6 §6.3/§6.4).

ZERO live network: every HTTP call is routed through ``respx`` (which by default asserts
all requests are mocked) and owslib capabilities are injected as recorded XML. The only
live test is ``@pytest.mark.live`` and is deselected by the default ``-m "not live"``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from plot_connectors import (
    AppGmlConnector,
    AppGmlQuery,
    EgressAllowlist,
    EgressBlocked,
    InMemoryCache,
    ParcelByIdQuery,
    ParcelByXYQuery,
    ResilienceConfig,
    ResultStatus,
    SourceProfile,
    SourceUnavailable,
    ULDKConnector,
    WFSConnector,
    WFSQuery,
    cache_key,
    is_private_host,
    uldk_profile,
)
from plot_connectors.base import BBox
from plot_connectors.profiles import PL_PROFILES, list_profiles
from plot_domain import EvidenceItem, LegalStatus, SourceRecord
from plot_reports import LocalArtifactStore

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _allowlist_no_dns() -> EgressAllowlist:
    # Enforce the host allowlist but skip DNS (unit tests must not resolve real hosts).
    return EgressAllowlist.from_settings(resolve_dns=False)


def _local_store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "artifacts")


def _uldk(tmp_path: Path) -> ULDKConnector:
    return ULDKConnector(
        allowlist=_allowlist_no_dns(),
        cache=InMemoryCache(),
        artifact_store=_local_store(tmp_path),
    )


# --------------------------------------------------------------------------- #
# ULDK contract (evidence #3)
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_uldk_get_parcel_by_id_contract(tmp_path: Path) -> None:
    body = (FIXTURES / "uldk_get_parcel_by_id.txt").read_text()
    connector = _uldk(tmp_path)
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.get("https://uldk.gugik.gov.pl/").mock(
            return_value=httpx.Response(200, text=body)
        )
        result = await connector.resolve(ParcelByIdQuery(parcel_id="141201_1.0001.1867/2"))

    assert route.called
    # status line "0" parsed → ok (NOT not_detected/source_unavailable)
    assert result.status is ResultStatus.OK
    # SourceRecord emitted with §5 fields
    rec = result.source_record
    assert isinstance(rec, SourceRecord)
    assert rec.publisher == "GUGiK"
    assert rec.legal_status is LegalStatus.BINDING
    assert rec.license != ""
    # parcel payload: WKT geometry + teryt fields parsed positionally
    parcel = result.payload["parcel"]
    assert parcel["teryt"] == "141201_1.0001.1867/2"
    assert parcel["geometry_wkt"].startswith("POLYGON((630880 497170")
    assert parcel["administrative_context"]["voivodeship"] == "14"
    assert parcel["administrative_context"]["county"] == "1412"
    # evidence: identity + geometry, each a real EvidenceItem
    claims = {e.claim for e in result.evidence}
    assert {"parcel_identity_resolved", "parcel_geometry_wkt"} <= claims
    assert all(isinstance(e, EvidenceItem) for e in result.evidence)
    # snapshot stored via the ArtifactStore
    assert result.snapshot_uri is not None
    geom_ev = next(e for e in result.evidence if e.claim == "parcel_geometry_wkt")
    assert geom_ev.value_json["crs"] == "EPSG:2180"


@pytest.mark.anyio
async def test_uldk_get_parcel_by_xy_contract(tmp_path: Path) -> None:
    body = (FIXTURES / "uldk_get_parcel_by_xy.txt").read_text()
    connector = _uldk(tmp_path)
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.get("https://uldk.gugik.gov.pl/").mock(
            return_value=httpx.Response(200, text=body)
        )
        result = await connector.resolve(
            ParcelByXYQuery(x=630889.87, y=497178.59, srid=2180), snapshot=False
        )

    # GetParcelByXY request shape: request=GetParcelByXY & xy="x,y,2180"
    request = route.calls.last.request
    assert request.url.params["request"] == "GetParcelByXY"
    assert request.url.params["xy"] == "630889.87,497178.59,2180"
    assert result.status is ResultStatus.OK
    parcel = result.payload["parcel"]
    assert parcel["teryt"] == "141201_1.0002.42"
    assert parcel["number"] == "42"
    assert parcel["geometry_wkt"].startswith("POLYGON")


# --------------------------------------------------------------------------- #
# SSRF / allowlist (evidence #4)
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_fetch_refuses_non_allowlisted_host(tmp_path: Path) -> None:
    # ULDK profile pointed at an off-allowlist host → EgressBlocked before any socket.
    profile = uldk_profile(base_url="https://evil.example.com/")
    connector = ULDKConnector(
        profile, allowlist=_allowlist_no_dns(), artifact_store=_local_store(tmp_path)
    )
    with respx.mock(assert_all_mocked=True):  # no route registered: a request would fail loudly
        with pytest.raises(EgressBlocked):
            await connector.fetch(ParcelByIdQuery(parcel_id="x"))


def test_allowlist_rejects_loopback_and_private() -> None:
    # loopback / private literal IPs are rejected by the SSRF guard
    assert is_private_host("127.0.0.1")
    assert is_private_host("10.0.0.5")
    assert is_private_host("169.254.169.254")  # cloud metadata link-local
    assert not is_private_host("8.8.8.8")
    # An allowlisted *name* that is not resolvable here would fail-closed, so use a
    # literal allowlist entry that maps to a private IP to prove rejection:
    bad = EgressAllowlist(["127.0.0.1"], resolve_dns=True)
    with pytest.raises(EgressBlocked):
        bad.check_url("https://127.0.0.1/path")


def test_allowlist_blocks_offlist_redirect() -> None:
    allow = _allowlist_no_dns()
    with pytest.raises(EgressBlocked):
        allow.check_redirect("https://evil.example.com/next")
    # an on-allowlist redirect is fine
    allow.check_redirect("https://uldk.gugik.gov.pl/next")


# --------------------------------------------------------------------------- #
# Resilience: timeout / 5xx → source_unavailable; breaker opens (evidence #5)
# --------------------------------------------------------------------------- #
def _fast_breaker_profile() -> SourceProfile:
    # Tight budget so the breaker trips quickly and retries are instant.
    return uldk_profile().model_copy(
        update={
            "resilience": ResilienceConfig(
                timeout_s=1.0,
                max_attempts=1,
                backoff_multiplier_s=0.0,
                backoff_max_s=0.0,
                breaker_fail_max=2,
                breaker_reset_timeout_s=60.0,
            )
        }
    )


@pytest.mark.anyio
async def test_timeout_maps_to_source_unavailable(tmp_path: Path) -> None:
    connector = ULDKConnector(
        _fast_breaker_profile(),
        allowlist=_allowlist_no_dns(),
        artifact_store=_local_store(tmp_path),
    )
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get("https://uldk.gugik.gov.pl/").mock(
            side_effect=httpx.ConnectTimeout("simulated timeout")
        )
        with pytest.raises(SourceUnavailable):
            await connector.fetch(ParcelByIdQuery(parcel_id="x"))


@pytest.mark.anyio
async def test_5xx_maps_to_source_unavailable(tmp_path: Path) -> None:
    connector = ULDKConnector(
        _fast_breaker_profile(),
        allowlist=_allowlist_no_dns(),
        artifact_store=_local_store(tmp_path),
    )
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get("https://uldk.gugik.gov.pl/").mock(return_value=httpx.Response(503))
        with pytest.raises(SourceUnavailable):
            await connector.fetch(ParcelByIdQuery(parcel_id="x"))


@pytest.mark.anyio
async def test_circuit_breaker_opens_after_n_failures(tmp_path: Path) -> None:
    connector = ULDKConnector(
        _fast_breaker_profile(),  # breaker_fail_max=2
        allowlist=_allowlist_no_dns(),
        artifact_store=_local_store(tmp_path),
    )
    assert connector.circuit_state == "closed"
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get("https://uldk.gugik.gov.pl/").mock(return_value=httpx.Response(500))
        # Two failures trip the breaker (fail_max=2).
        for _ in range(2):
            with pytest.raises(SourceUnavailable):
                await connector.fetch(ParcelByIdQuery(parcel_id="x"))
    assert connector.circuit_state == "open"
    # With the circuit open, the next call short-circuits to source_unavailable
    # WITHOUT issuing a request (respx asserts no unexpected call is made).
    with respx.mock(assert_all_mocked=True, assert_all_called=False):
        with pytest.raises(SourceUnavailable):
            await connector.fetch(ParcelByIdQuery(parcel_id="x"))


# --------------------------------------------------------------------------- #
# OGC contract: WFS capabilities → discovery; GetFeature → evidence (evidence #6)
# --------------------------------------------------------------------------- #
def _gdos_wfs(tmp_path: Path) -> WFSConnector:
    profile = PL_PROFILES["pl.gdos.wfs.crfop"]
    caps_xml = (FIXTURES / "wfs_capabilities.xml").read_bytes()
    return WFSConnector(
        profile,
        capabilities_xml=caps_xml,
        allowlist=_allowlist_no_dns(),
        artifact_store=_local_store(tmp_path),
    )


def test_wfs_capabilities_layer_discovery(tmp_path: Path) -> None:
    connector = _gdos_wfs(tmp_path)
    caps = connector.capabilities()  # parsed from injected fixture XML — no network
    assert caps.service == "WFS"
    assert "GetFeature" in caps.operations
    layers = connector.discover_layers()
    assert "gdos:ProtectedSite" in layers
    layer = caps.find_layer("gdos:ProtectedSite")
    assert layer is not None
    assert any("2180" in c for c in layer.crs_options)


@pytest.mark.anyio
async def test_wfs_getfeature_normalizes_to_evidence(tmp_path: Path) -> None:
    connector = _gdos_wfs(tmp_path)
    body = (FIXTURES / "wfs_getfeature.json").read_text()
    bbox = BBox(minx=630800, miny=497100, maxx=631000, maxy=497300)
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.get("https://sdi.gdos.gov.pl/wfs").mock(
            return_value=httpx.Response(
                200, text=body, headers={"content-type": "application/json"}
            )
        )
        raw = await connector.fetch(WFSQuery(layer="gdos:ProtectedSite", bbox=bbox))
        result = connector.normalize(raw)

    # bbox minimization is in the outgoing request (F-0083); compare the decoded param
    bbox_param = route.calls.last.request.url.params["bbox"]
    assert bbox_param == "630800.0,497100.0,631000.0,497300.0,EPSG:2180"
    assert result.status is ResultStatus.OK
    assert result.payload["feature_count"] == 1
    ev = result.evidence[0]
    assert ev.claim == "wfs_feature"
    assert ev.geometry is not None and ev.geometry["type"] == "Polygon"


@pytest.mark.anyio
async def test_wfs_empty_collection_is_not_detected(tmp_path: Path) -> None:
    connector = _gdos_wfs(tmp_path)
    bbox = BBox(minx=0, miny=0, maxx=1, maxy=1)
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get("https://sdi.gdos.gov.pl/wfs").mock(
            return_value=httpx.Response(
                200,
                text='{"type":"FeatureCollection","features":[]}',
                headers={"content-type": "application/json"},
            )
        )
        raw = await connector.fetch(WFSQuery(layer="gdos:ProtectedSite", bbox=bbox))
        result = connector.normalize(raw)
    # Empty-but-successful ⇒ not_detected (NOT source_unavailable) (NFR-REL-001)
    assert result.status is ResultStatus.NOT_DETECTED


# --------------------------------------------------------------------------- #
# Healthcheck (evidence #2 / DoD §28)
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_uldk_healthcheck_ok(tmp_path: Path) -> None:
    connector = _uldk(tmp_path)
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get("https://uldk.gugik.gov.pl/").mock(return_value=httpx.Response(200, text="0\nX"))
        health = await connector.healthcheck()
    assert health.healthy is True
    assert health.status is ResultStatus.OK
    assert health.circuit_state == "closed"


@pytest.mark.anyio
async def test_uldk_healthcheck_unavailable(tmp_path: Path) -> None:
    connector = ULDKConnector(
        _fast_breaker_profile(),
        allowlist=_allowlist_no_dns(),
        artifact_store=_local_store(tmp_path),
    )
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get("https://uldk.gugik.gov.pl/").mock(
            side_effect=httpx.ConnectError("down")
        )
        health = await connector.healthcheck()
    assert health.healthy is False
    assert health.status is ResultStatus.SOURCE_UNAVAILABLE


# --------------------------------------------------------------------------- #
# APP/GML fetch+record (Phase 6 only fetches/snapshots/records)
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_app_gml_fetch_record_manual_review(tmp_path: Path) -> None:
    profile = PL_PROFILES["pl.app.gml.planning"].model_copy(
        update={"base_url": "https://www.gov.pl/web/zagospodarowanieprzestrzenne/app.gml"}
    )
    connector = AppGmlConnector(
        profile, allowlist=_allowlist_no_dns(), artifact_store=_local_store(tmp_path)
    )
    gml = b"<?xml version='1.0'?><FeatureCollection>TEST FIXTURE APP/GML</FeatureCollection>"
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(profile.base_url).mock(
            return_value=httpx.Response(200, content=gml, headers={"content-type": "application/gml+xml"})
        )
        result = await connector.fetch_record(AppGmlQuery(url=profile.base_url))
    # A retrieved-but-unparsed planning act ⇒ manual_review_required (never not_detected)
    assert result.status is ResultStatus.MANUAL_REVIEW_REQUIRED
    assert result.source_record.legal_status is LegalStatus.BINDING
    assert result.snapshot_uri is not None
    assert result.evidence[0].claim == "app_gml_document_retrieved"


# --------------------------------------------------------------------------- #
# Profile registry + cache key (evidence #7)
# --------------------------------------------------------------------------- #
def test_profile_registry_mvp_set() -> None:
    profiles = list_profiles()
    ids = {p.source_id for p in profiles}
    # MVP must-have set (§32) present
    expected = {
        "pl.gugik.uldk",
        "pl.geoportal.wms.ortofoto",
        "pl.geoportal.wfs.bdot10k",
        "pl.geoportal.wcs.nmt",
        "pl.geoportal.wfs.gesut",
        "pl.isok.wfs.flood",
        "pl.gdos.wfs.crfop",
        "pl.pig.wfs.sopo",
        "pl.nid.wfs.heritage",
        "pl.gugik.wfs.prg",
        "pl.app.gml.planning",
    }
    assert expected <= ids
    # every profile's base_url host is on the egress allowlist (§6 / F-0488)
    allow = _allowlist_no_dns()
    for p in profiles:
        host = allow.check_url(p.base_url)  # raises if off-allowlist
        assert host


def test_cache_key_includes_bbox_and_version() -> None:
    b1 = BBox(minx=0, miny=0, maxx=10, maxy=10)
    b2 = BBox(minx=0, miny=0, maxx=20, maxy=20)
    k1 = cache_key("src", source_version="v1", operation="GetFeature", bbox=b1)
    k2 = cache_key("src", source_version="v1", operation="GetFeature", bbox=b2)
    k3 = cache_key("src", source_version="v2", operation="GetFeature", bbox=b1)
    # different bbox ⇒ different key; different source version ⇒ different key
    assert k1 != k2
    assert k1 != k3


def test_in_memory_cache_roundtrip() -> None:
    cache = InMemoryCache()
    assert cache.get("k") is None
    cache.set("k", b"value")
    assert cache.get("k") == b"value"


# --------------------------------------------------------------------------- #
# Live smoke test — OPT-IN, skipped by default (Phase 6 §6.4)
# --------------------------------------------------------------------------- #
@pytest.mark.live
@pytest.mark.anyio
async def test_uldk_live_smoke() -> None:  # pragma: no cover - opt-in only
    """Real ULDK round-trip. Deselected by the default ``-m 'not live'`` run."""
    connector = ULDKConnector()
    result = await connector.resolve(
        ParcelByIdQuery(parcel_id="141201_1.0001.1867/2"), snapshot=False
    )
    assert result.status in (ResultStatus.OK, ResultStatus.NOT_DETECTED)
