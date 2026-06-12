"""Evidence-completeness sweep (Phase 16; F-0551, NFR-AUD-001, §18.2 last bullet).

"Każda teza ma źródło albo oznaczenie braku źródła" — swept over a FULL
due-diligence result (the quick-screening discipline test exists since Phase 7;
this is the full-DD gap): every constraint, evidence item and planning source
carries a source id; every risk is either sourced or explicitly rendered with
the ``no_source`` mark in the report; every unknown names its reason and next
action; the evidence pack's citations cover every evidence claim.

Zero network: the Phase 7/12 mock connector bundle (TEST FIXTURES).
"""

from __future__ import annotations

from typing import Any

import pytest
from plot_domain import AnalysisInput
from tests.mocks import mock_connectors
from tests.site_fixtures import building_feature, gj_box, synthetic_dem_bytes

pytest.importorskip("rasterio")

from plot_envelope import RiskKind  # noqa: E402
from plot_mcp_server import usecases  # noqa: E402

# Mock ULDK parcel frame (tests/mocks.py DEFAULT_PARCEL_WKT): 40 × 30 m.
PX0, PY0, PX1, PY1 = 630880.0, 497170.0, 630920.0, 497200.0


def _full_dd_result() -> Any:
    usecases.set_connectors(
        mock_connectors(
            feature_geoms={
                RiskKind.FLOOD: [gj_box(PX0 - 5, PY0 - 5, PX0 + 10, PY0 + 10)],
                RiskKind.ROADS: [gj_box(PX0 - 10, PY0 - 12, PX1 + 10, PY0 - 1)],
            },
            unavailable={RiskKind.LANDSLIDE},  # one degraded theme stays VISIBLE
            terrain_raster=synthetic_dem_bytes(
                west=PX0 - 60, north=PY1 + 60, width=200, height=200
            ),
            building_features=[
                building_feature(
                    gj_box(PX0, PY1 + 28, PX1, PY1 + 38), wysokosc=25.0, id="sasiad-N"
                )
            ],
        )
    )
    try:
        return usecases.parcel_analyze(
            AnalysisInput.model_validate(
                {
                    "input": {"parcel_id": "141201_1.0001.1867/2"},
                    "analysis_mode": "full_due_diligence",
                    "investment_goal": {"type": "multifamily"},
                }
            ),
            "test-ruleset",
        )
    finally:
        usecases.set_connectors(None)


@pytest.fixture(scope="module")
def full_result():
    return _full_dd_result()


def test_every_constraint_has_a_source(full_result) -> None:
    assert full_result.constraints, "full DD must produce constraints"
    for constraint in full_result.constraints:
        assert constraint.source_id, f"constraint {constraint.constraint_id} unsourced"


def test_every_evidence_item_is_a_complete_claim(full_result) -> None:
    assert full_result.evidence, "full DD must produce evidence"
    for item in full_result.evidence:
        assert item.source_id, f"evidence {item.id} has no source_id"
        assert item.claim, f"evidence {item.id} has no claim"
        assert item.subject_type and item.subject_id, f"evidence {item.id} has no subject"


def test_every_risk_sourced_or_marked_no_source_in_report(full_result) -> None:
    """A risk either cites its source record, or the rendered report carries the
    explicit ``no_source`` mark for it (never a silent unsourced claim)."""
    assert full_result.risks
    md = usecases.report_generate(full_result.analysis_id, "md")["content"]
    for risk in full_result.risks:
        assert risk.summary, f"risk {risk.id} has no human-readable claim"
        if risk.source_id:
            assert risk.source_id in md
        else:
            # the renderer marks the claim explicitly (formats.py red-flags block)
            assert "no_source" in md
    # The mark itself is exercised: synthesized risks (envelope/no-road class)
    # exist in this scenario OR every risk is sourced — both are legal; what is
    # ILLEGAL is an unsourced risk without the mark, asserted above.


def test_every_unknown_names_reason_and_next_action(full_result) -> None:
    assert full_result.unknowns, "the degraded landslide theme must surface as unknown"
    for unknown in full_result.unknowns:
        assert unknown.topic
        assert unknown.reason
        assert unknown.suggested_action
    topics = " ".join(u.topic for u in full_result.unknowns)
    assert "landslide" in topics or "osuwisk" in topics


def test_degraded_source_never_silently_clean(full_result) -> None:
    """The killed landslide source is reported as unavailable, not as 'clear'."""
    layer_status = (
        full_result.planning.get("_risk_layer_status", {})
        if isinstance(full_result.planning, dict)
        else {}
    )
    assert layer_status.get("landslide") == "source_unavailable"


def test_evidence_pack_citations_cover_every_evidence_claim(full_result) -> None:
    import json

    from plot_reports import get_artifact_store

    out = usecases.sources_collect(full_result.analysis_id)
    assert out.get("status") != "not_found"
    assert out["evidence_count"] == len(full_result.evidence)
    # The PERSISTED evidence pack (F-0407) carries the citation chain.
    pack = json.loads(
        get_artifact_store().get(f"analysis/{full_result.analysis_id}/evidence-pack.json")
    )
    cited_claims = {(c["claim"], c["source_id"]) for c in pack["citations"]}
    for item in full_result.evidence:
        assert (item.claim, item.source_id) in cited_claims
    # Every source referenced by evidence appears in the collected source records.
    record_ids = {s.get("source_id") for s in out["sources"]}
    for item in full_result.evidence:
        assert item.source_id in record_ids, f"evidence cites unknown source {item.source_id}"


def test_json_report_carries_the_same_evidence_counts(full_result) -> None:
    """The §22 JSON contract and the MD report describe the same evidence base
    (no format-dependent claims)."""
    out = usecases.report_generate(full_result.analysis_id, "json")
    payload = out["content"]  # render_json returns the §10.7 dict directly
    assert len(payload["evidence"]) == len(full_result.evidence)
    assert len(payload["risks"]) == len(full_result.risks)
    assert len(payload["unknowns"]) == len(full_result.unknowns)
