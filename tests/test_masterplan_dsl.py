"""Phase 9 masterplan DSL v2 tests (IMPLEMENTATION_PLAN_V2 §9.3).

Covers:
  * round-trip: a hand-written MasterplanProposal with ≥10 buildings (one L-shaped
    2-segment, one zabytek_do_remontu, hala podziemna, 2 stages) validates, renders
    and scores;
  * payload discrimination: ``buildings``/``schema_version`` → MasterplanProposal,
    everything else → byte-for-byte LayoutProposal;
  * ingest validation: non-finite coords / implausible EPSG:2180 extents rejected at
    parse; an invalid (bowtie) polygon is make_valid-repaired; buildings-within-parcel
    is a SCORING-time violation, never a parse rejection;
  * status semantics: existing/heritage buildings are exempt from the envelope guard
    but never from parcel containment.
"""

from __future__ import annotations

import pytest
from plot_agent.context import AnalysisContext
from plot_agent.drawing import (
    LayoutProposal,
    MasterplanProposal,
    parse_proposal,
    score_masterplan,
    validate_hard_masterplan,
)
from plot_planning import masterplan_metrics
from plot_reports import render_masterplan
from pydantic import ValidationError
from shapely.geometry import Polygon, mapping

PARCEL = Polygon([(0, 0), (200, 0), (200, 150), (0, 150)])
ENVELOPE = Polygon([(10, 10), (190, 10), (190, 140), (10, 140)])
INDICATORS = {"parking_per_mieszkanie": 1.0, "parking_per_100m2_uslug": 1.0}


def _rect(x: float, y: float, w: float, h: float) -> dict:
    return mapping(Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)]))


def _context() -> AnalysisContext:
    return AnalysisContext.with_loaded_rules(
        parcel=PARCEL, buildable_envelope=ENVELOPE, ruleset_dir="rulesets/PL"
    )


def ten_building_payload() -> dict:
    """≥10 buildings: L-shaped 2-segment (#1), zabytek (#9), hala podziemna, 2 stages."""
    buildings = [
        {
            "name": "Budynek 1",  # L-shaped: two wings with different floor counts
            "stage": 1,
            "underground_floors": 1,
            "segments": [
                {"rectangles": [{"x": 15, "y": 15, "w": 30, "h": 12}], "floors": 4,
                 "use": "mieszkalny", "ground_floor_use": "uslugowy"},
                {"rectangles": [{"x": 15, "y": 27, "w": 12, "h": 20}], "floors": 7,
                 "use": "mieszkalny"},
            ],
        },
        {
            "name": "Budynek 9 (zabytek)",
            "status": "zabytek_do_remontu",
            "segments": [
                {"polygon": _rect(160, 115, 25, 20), "floors": 2, "use": "uslugowy"}
            ],
        },
        {
            "name": "Budynek 10 (usługi)",
            "stage": 2,
            "segments": [{"polygon": _rect(160, 15, 25, 15), "floors": 2, "use": "uslugowy"}],
        },
    ]
    # Buildings 2..8: seven staged multifamily slabs along two rows.
    for i in range(7):
        x = 15 + i * 20
        buildings.append(
            {
                "name": f"Budynek {i + 2}",
                "stage": 1 if i < 4 else 2,
                "segments": [
                    {"rectangles": [{"x": x, "y": 60, "w": 14, "h": 12}], "floors": 3 + (i % 3),
                     "use": "mieszkalny"}
                ],
            }
        )
    return {
        "buildings": buildings,
        "roads": [
            {"centerline": {"type": "LineString", "coordinates": [[10, 52], [190, 52]]},
             "width_m": 6.0, "function": "kdw"},
            {"centerline": {"type": "LineString", "coordinates": [[10, 105], [190, 105]]},
             "width_m": 4.0, "function": "pozarowa"},
        ],
        "parking": [
            {"kind": "hala_podziemna", "polygon": _rect(15, 15, 60, 40), "spaces": 150,
             "serves_buildings": ["Budynek 1"]},
            {"kind": "naziemny", "polygon": _rect(120, 60, 30, 20), "spaces": 40},
        ],
        "greenery_polygons": [_rect(15, 115, 60, 25)],
        "playgrounds": [_rect(90, 115, 20, 15)],
        "retention": [_rect(120, 115, 15, 10)],
        "zabudowa_srodmiejska": False,
    }


# --------------------------------------------------------------------------- #
# Round-trip: validate → render → score (plan §9.3 checklist item 1)
# --------------------------------------------------------------------------- #
def test_ten_building_round_trip_validates_renders_scores() -> None:
    payload = ten_building_payload()
    proposal = MasterplanProposal.model_validate(payload)
    assert len(proposal.buildings) == 10
    assert len(proposal.buildings[0].segments) == 2  # the L-shape
    assert any(b.status == "zabytek_do_remontu" for b in proposal.buildings)
    assert any(p.kind == "hala_podziemna" for p in proposal.parking)
    assert {b.stage for b in proposal.buildings if b.stage} == {1, 2}

    # Pydantic round-trip: dump → re-validate → identical dump.
    dumped = proposal.model_dump(mode="json")
    again = MasterplanProposal.model_validate(dumped)
    assert again.model_dump(mode="json") == dumped

    context = _context()
    metrics = masterplan_metrics(proposal, context.parcel_geom(), INDICATORS,
                                 registry=context.ruleset)
    assert metrics.totals["buildings"] == 10
    assert metrics.stage_table[-1]["etap"] == "SUMA"

    score = score_masterplan(proposal, context, metrics)
    assert score.valid is True
    assert score.violations == []
    assert 0.0 < score.total <= 1.0

    render = render_masterplan(proposal, context.parcel_geom(), metrics=metrics,
                               envelope=context.envelope_geom())
    assert render.mime_type == "image/png"
    assert render.data[:8] == b"\x89PNG\r\n\x1a\n"
    # Floor labels for every segment + a name per building (11 segments / 10 names).
    floors = [a for a in render.style_metadata["annotations"] if a["kind"] == "floors"]
    names = [a for a in render.style_metadata["annotations"] if a["kind"] == "name"]
    assert len(floors) == 11
    assert len(names) == 10


# --------------------------------------------------------------------------- #
# Discrimination (plan §9.1.1): buildings/schema_version vs legacy payloads
# --------------------------------------------------------------------------- #
def test_parse_proposal_discriminates_masterplan_vs_layout() -> None:
    masterplan = parse_proposal(ten_building_payload())
    assert isinstance(masterplan, MasterplanProposal)
    assert masterplan.schema_version == 2

    legacy = parse_proposal(
        {"program_type": "single_family", "rectangles": [{"x": 8, "y": 10, "w": 30, "h": 14}]}
    )
    assert isinstance(legacy, LayoutProposal)


def test_layout_proposal_rejects_buildings_key() -> None:
    """v1 stays extra=forbid: a buildings payload can never half-parse as v1."""
    with pytest.raises(ValidationError):
        LayoutProposal.model_validate({"program_type": "mixed", "buildings": []})


# --------------------------------------------------------------------------- #
# Ingest validation (plan §9.1.5): make_valid / finite / metric plausibility
# --------------------------------------------------------------------------- #
def _single_building(polygon: dict) -> dict:
    return {"buildings": [{"name": "B", "segments": [
        {"polygon": polygon, "floors": 2, "use": "mieszkalny"}]}]}


@pytest.mark.filterwarnings("ignore::RuntimeWarning")  # shapely warns while building the NaN ring
def test_non_finite_coordinates_rejected_at_parse() -> None:
    bad = {"type": "Polygon",
           "coordinates": [[[0, 0], [10, 0], [10, float("nan")], [0, 10], [0, 0]]]}
    with pytest.raises(ValidationError, match="non-finite"):
        MasterplanProposal.model_validate(_single_building(bad))


def test_implausible_extent_rejected_at_parse() -> None:
    # 50 km wide "building" — degree-like / wrong-CRS input must be rejected (EPSG:2180
    # metric plausibility, the PlacedRectangle bounds-check idiom).
    bad = _rect(0, 0, 50_000, 10)
    with pytest.raises(ValidationError, match="plausible site size"):
        MasterplanProposal.model_validate(_single_building(bad))


def test_invalid_bowtie_polygon_is_make_valid_repaired() -> None:
    bowtie = {"type": "Polygon",
              "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}
    proposal = MasterplanProposal.model_validate(_single_building(bowtie))
    geom = proposal.buildings[0].segments[0].geometry()
    assert geom.is_valid
    assert geom.area > 0


def test_bowtie_greenery_repair_reaches_consumers() -> None:
    """F1 regression: the make_valid repair of a loose-list polygon must not be thrown
    away after parse — ``greenery_geometry()``/``playgrounds_geometry()`` and the raw
    stored payload (read by the renderer / variant store) must all see the REPAIRED
    geometry, never the raw self-intersecting ring (whose shapely area is 0.0)."""
    # Bowtie (0,60)→(10,70)→(10,60)→(0,70): crossing at (5,65) → two 25 m² triangles.
    bowtie = {"type": "Polygon",
              "coordinates": [[[0, 60], [10, 70], [10, 60], [0, 70], [0, 60]]]}
    payload = _single_building(_rect(15, 15, 20, 10))
    payload["greenery_polygons"] = [bowtie]
    payload["playgrounds"] = [dict(bowtie)]
    payload["retention"] = [dict(bowtie)]
    proposal = MasterplanProposal.model_validate(payload)

    greenery = proposal.greenery_geometry()
    assert greenery.is_valid
    assert greenery.area == pytest.approx(50.0)  # 25 + 25, NOT 0.0
    playgrounds = proposal.playgrounds_geometry()
    assert playgrounds.is_valid
    assert playgrounds.area == pytest.approx(50.0)
    # The persisted payload (renderer + variant store read these dicts raw) is repaired.
    from plot_agent.context import to_shapely

    for stored in (proposal.greenery_polygons[0], proposal.playgrounds[0],
                   proposal.retention[0]):
        repaired = to_shapely(stored)
        assert repaired.is_valid
        assert repaired.area == pytest.approx(50.0)


def test_segment_requires_rectangles_or_polygon() -> None:
    with pytest.raises(ValidationError, match="rectangles or a GeoJSON polygon"):
        MasterplanProposal.model_validate(
            {"buildings": [{"name": "B", "segments": [{"floors": 2, "use": "mieszkalny"}]}]}
        )


# --------------------------------------------------------------------------- #
# Buildings-within-parcel: SCORING-time violation, not a parse rejection
# --------------------------------------------------------------------------- #
def test_building_outside_parcel_parses_but_scores_invalid() -> None:
    payload = _single_building(_rect(195, 140, 20, 20))  # spills past (200, 150)
    proposal = MasterplanProposal.model_validate(payload)  # parse MUST succeed
    context = _context()
    violations = validate_hard_masterplan(proposal, context)
    kinds = {v.kind for v in violations}
    assert "outside_parcel" in kinds

    metrics = masterplan_metrics(proposal, context.parcel_geom(), INDICATORS,
                                 registry=context.ruleset)
    score = score_masterplan(proposal, context, metrics)
    assert score.valid is False
    assert score.total <= 0.0  # hard-blocker dominance (§14.2)


def test_existing_building_exempt_from_envelope_but_not_parcel() -> None:
    # Inside the parcel but OUTSIDE the envelope (x<10 strip): a proposed building
    # violates; an existing/heritage one does not (it already stands).
    polygon = _rect(2, 2, 6, 6)
    proposed = MasterplanProposal.model_validate(_single_building(polygon))
    assert any(
        v.kind == "outside_envelope" for v in validate_hard_masterplan(proposed, _context())
    )

    heritage_payload = _single_building(polygon)
    heritage_payload["buildings"][0]["status"] = "zabytek_do_remontu"
    heritage = MasterplanProposal.model_validate(heritage_payload)
    assert validate_hard_masterplan(heritage, _context()) == []


def test_phase10_inter_building_hook_defaults_empty_and_blocks_on_fail() -> None:
    """The Phase 10 hook: empty by default; a failed hard RuleCheck-like → violation."""
    proposal = MasterplanProposal.model_validate(ten_building_payload())
    context = _context()
    metrics = masterplan_metrics(proposal, context.parcel_geom(), INDICATORS,
                                 registry=context.ruleset)
    ok = score_masterplan(proposal, context, metrics, inter_building_checks=[])
    assert ok.valid is True

    failed_check = {"status": "fail", "severity": "hard",
                    "message": "PL-WT-12: odległość od granicy < 4 m"}
    blocked = score_masterplan(proposal, context, metrics,
                               inter_building_checks=[failed_check])
    assert blocked.valid is False
    assert any(v.kind == "inter_building_rule" for v in blocked.violations)
