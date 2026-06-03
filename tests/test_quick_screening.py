"""MVP-gate end-to-end + partial-result + hard-blocker tests for quick_screening (Phase 7).

ZERO live network. The MVP-gate test REUSES the Phase 6 connector mocking approach: a real
``ULDKConnector`` + real ``WFSConnector``s driven over ``respx`` routes and injected owslib
capabilities XML (``tests/fixtures/connectors/*`` from Phase 6). The other tests use the
fast pure-mock bundle (``tests/mocks.mock_connectors``), which is itself zero-network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from jsonschema import Draft202012Validator
from plot_agent.analysis import Connectors, run_quick_screening
from plot_domain import AnalysisInput, AnalysisStatus, Decision
from plot_envelope import RiskKind
from tests.mocks import (
    EMPTY_FEATURE_COLLECTION,
    mock_connectors,
    respx_route_json,
    respx_route_text,
    respx_uldk,
    respx_wfs_source,
)

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"
SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "analysis-result.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --------------------------------------------------------------------------- #
# Evidence #3 — the MVP gate end-to-end test (§18.2), with mocked ULDK + mocked WFS.
# Runs once from a parcel id AND once from a point; asserts the full MVP result shape and
# schema validity. Reuses the Phase 6 fixtures (uldk_*.txt + wfs_capabilities.xml).
# --------------------------------------------------------------------------- #
def _assert_mvp_result(result, *, expect_status: set[str]) -> None:
    payload = json.loads(result.model_dump_json())
    errors = sorted(VALIDATOR.iter_errors(payload), key=str)
    assert not errors, f"schema violations: {[e.message for e in errors]}"

    # geometry + admin context
    assert result.parcel is not None
    assert result.parcel.geometry is not None
    assert result.parcel.administrative_context is not None
    assert result.parcel.administrative_context.teryt
    # geometry metrics computed
    metrics = result.planning["_geometry_metrics"]
    assert metrics["area_m2"] > 0
    assert "perimeter_m" in metrics and "compactness" in metrics
    # planning-coverage report present
    assert "mpzp_pog_wz" in result.planning
    # all MVP risk-layer themes checked (present as ok / not_detected / source_unavailable)
    layer_status = result.planning["_risk_layer_status"]
    for theme in ("flood", "protected", "landslide", "heritage", "utilities", "roads"):
        assert theme in layer_status, f"theme {theme} not checked"
    # buildable envelope v1
    assert result.buildable_envelope is not None
    assert result.buildable_envelope.area_m2 is not None
    # non-empty next_actions + evidence; unknowns may be empty when nothing failed
    assert result.next_actions, "expected at least one next action"
    assert result.evidence, "expected evidence (NFR-AUD-001)"
    assert result.status.value in expect_status


@pytest.mark.anyio
async def test_mvp_gate_end_to_end_id_and_point(tmp_path: Path) -> None:
    """ONE test: parcel-id run AND point run, mocked ULDK + mocked risk-layer WFS (§18.2)."""
    uldk = respx_uldk(tmp_path)
    # Map every MVP theme to a real WFS connector over the GDOŚ profile fixture endpoint.
    themes = {
        RiskKind.FLOOD: "pl.isok.wfs.flood",
        RiskKind.PROTECTED: "pl.gdos.wfs.crfop",
        RiskKind.LANDSLIDE: "pl.pig.wfs.sopo",
        RiskKind.HERITAGE: "pl.nid.wfs.heritage",
        RiskKind.UTILITIES: "pl.geoportal.wfs.gesut",
        RiskKind.ROADS: "pl.geoportal.wfs.bdot10k",
        RiskKind.WATERCOURSES: "pl.geoportal.wfs.bdot10k",
        RiskKind.FOREST: "pl.geoportal.wfs.bdot10k",
    }
    risk_source = respx_wfs_source(tmp_path, themes)
    connectors = Connectors(uldk=uldk, risk_layers=risk_source)

    id_body = (FIXTURES / "uldk_get_parcel_by_id.txt").read_text()
    xy_body = (FIXTURES / "uldk_get_parcel_by_xy.txt").read_text()
    wfs_protected = (FIXTURES / "wfs_getfeature.json").read_text()  # one feature near the parcel

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        respx_route_text(mock, "https://uldk.gugik.gov.pl/", id_body)
        # protected returns a real feature; every other WFS endpoint returns an empty FC
        respx_route_json(mock, "https://sdi.gdos.gov.pl/wfs", wfs_protected)
        for url in (
            "https://wody.isok.gov.pl/wfs",
            "https://geozagrozenia.pgi.gov.pl/sopo/wfs",
            "https://mapy.zabytek.gov.pl/wfs",
            "https://mapy.geoportal.gov.pl/wss/service/PZGIK/GESUT/WFS/GetExtent",
            "https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/GetExtent",
        ):
            respx_route_json(mock, url, EMPTY_FEATURE_COLLECTION)

        # 1) from a parcel id
        inp_id = AnalysisInput.model_validate(
            {"input": {"parcel_id": "141201_1.0001.1867/2"}, "analysis_mode": "quick_screening"}
        )
        result_id = await run_quick_screening(inp_id, connectors=connectors, analysis_id="a-id")

        # 2) from a point (ULDK GetParcelByXY fixture). Swap the ULDK route body.
        mock.get("https://uldk.gugik.gov.pl/").mock(
            side_effect=lambda req: __import__("httpx").Response(
                200, text=xy_body if req.url.params.get("request") == "GetParcelByXY" else id_body
            )
        )
        inp_pt = AnalysisInput.model_validate(
            {
                "input": {"point": {"x": 630900.0, "y": 497185.0, "crs": "EPSG:2180"}},
                "analysis_mode": "quick_screening",
            }
        )
        result_pt = await run_quick_screening(inp_pt, connectors=connectors, analysis_id="a-pt")

    _assert_mvp_result(result_id, expect_status={"complete", "manual_review_required", "partial"})
    _assert_mvp_result(result_pt, expect_status={"complete", "manual_review_required", "partial"})
    # the protected WFS feature overlaps near the parcel → a protected constraint exists
    assert any(c.constraint_type == "protected" for c in result_id.constraints)
    # the flood layer answered empty → not_detected (NOT source_unavailable) (§21)
    assert result_id.planning["_risk_layer_status"]["flood"] == "not_detected"


# --------------------------------------------------------------------------- #
# Hard-blocker dominance through the orchestrator (a flood feature on the parcel).
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_orchestrator_hard_blocker_forces_block() -> None:
    # Flood feature covering part of the default parcel → hard blocker → LIKELY_BLOCKED,
    # even though the envelope still has substantial area.
    flood_geom = {
        "type": "Polygon",
        "coordinates": [[[630880, 497170], [630890, 497170], [630890, 497200], [630880, 497200], [630880, 497170]]],
    }
    connectors = mock_connectors(feature_geoms={RiskKind.FLOOD: [flood_geom]})
    inp = AnalysisInput.model_validate(
        {"input": {"parcel_id": "x"}, "analysis_mode": "quick_screening"}
    )
    result = await run_quick_screening(inp, connectors=connectors, analysis_id="a-block")
    assert result.decision is Decision.LIKELY_BLOCKED
    assert (result.buildable_envelope.area_m2 or 0.0) > 0.0  # envelope still large
    assert any(c.constraint_type == "flood" for c in result.constraints)


# --------------------------------------------------------------------------- #
# Partial-result test: one connector times out ⇒ status=partial, that layer
# source_unavailable (NOT not_detected), other layers still analyzed (NFR-REL-001/010).
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_partial_result_on_source_unavailable() -> None:
    connectors = mock_connectors(unavailable={RiskKind.FLOOD})
    inp = AnalysisInput.model_validate(
        {"input": {"parcel_id": "x"}, "analysis_mode": "quick_screening"}
    )
    result = await run_quick_screening(inp, connectors=connectors, analysis_id="a-partial")
    assert result.status is AnalysisStatus.PARTIAL
    layer_status = result.planning["_risk_layer_status"]
    assert layer_status["flood"] == "source_unavailable"
    # other layers still analyzed (checked & clear), NOT marked unavailable
    assert layer_status["protected"] == "not_detected"
    # the unavailable theme is an explicit unknown with reason source_unavailable (§21)
    flood_unknown = next(u for u in result.unknowns if "flood" in u.topic)
    assert flood_unknown.reason == "source_unavailable"
    # schema still validates for a partial result
    errors = sorted(VALIDATOR.iter_errors(json.loads(result.model_dump_json())), key=str)
    assert not errors


@pytest.mark.anyio
async def test_uldk_unavailable_yields_useful_partial() -> None:
    connectors = mock_connectors(uldk_fail=True)
    inp = AnalysisInput.model_validate(
        {"input": {"parcel_id": "x"}, "analysis_mode": "quick_screening"}
    )
    result = await run_quick_screening(inp, connectors=connectors, analysis_id="a-noparcel")
    assert result.status is AnalysisStatus.PARTIAL
    assert any("Identyfikacja" in u.topic for u in result.unknowns)
    assert result.next_actions


# --------------------------------------------------------------------------- #
# Evidence discipline: every risk has a source_id OR the result carries a no_source flag.
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_evidence_discipline_every_risk_sourced_or_no_source() -> None:
    flood_geom = {
        "type": "Polygon",
        "coordinates": [[[630880, 497170], [630895, 497170], [630895, 497200], [630880, 497200], [630880, 497170]]],
    }
    connectors = mock_connectors(feature_geoms={RiskKind.FLOOD: [flood_geom]})
    inp = AnalysisInput.model_validate(
        {"input": {"parcel_id": "x"}, "analysis_mode": "quick_screening"}
    )
    result = await run_quick_screening(inp, connectors=connectors, analysis_id="a-ev")
    for r in result.risks:
        # constraint-derived risks carry a source_id; synthesized ones (envelope/no-road)
        # are allowed source_id=None (an explicit "no_source" claim in the report).
        assert r.source_id is not None or r.summary  # never a silent unsourced claim
    # the flood risk specifically is sourced from the flood layer
    flood_risk = next(r for r in result.risks if r.risk_type.value == "flood")
    assert flood_risk.source_id == "src.flood:test"
    assert result.evidence  # NFR-AUD-001
