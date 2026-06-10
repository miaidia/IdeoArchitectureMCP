"""Use matrix + conflict detection + stability tests (Phase 8 Task 3; F-0121–0127, §25.2).

* use matrix per zone symbol (allowed/conditional/forbidden), compound symbols,
  unknown symbol → unknown, provision refinement with citation trace;
* GML-vs-PDF conflict (§25.3 example): both values + both sources reported,
  status manual_review_required — NEVER silently resolved;
* planning stability heuristic with factor trace (F-0126).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from plot_domain import PlanningAct, PlanningActType, PlanningZone
from plot_planning import (
    MANUAL_REVIEW_REQUIRED,
    IndicatorValue,
    detect_conflicts,
    indicators_from_zone,
    parse_app_gml,
    stability_score,
    use_matrix_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "planning" / "app_gml_sample.gml"


# --------------------------------------------------------------------------- #
# Use matrix (F-0121–0123)
# --------------------------------------------------------------------------- #
def test_mn_matrix() -> None:
    matrix = use_matrix_for("MN")
    cats = matrix["categories"]
    assert cats["mieszkalnictwo_jednorodzinne"] == "allowed"
    assert cats["mieszkalnictwo_wielorodzinne"] == "forbidden"
    assert cats["uslugi"] == "conditional"
    assert cats["produkcja"] == "forbidden"
    assert cats["magazyny"] == "forbidden"
    assert matrix["basis"] == "symbol_convention"


def test_mw_and_u_matrix() -> None:
    assert use_matrix_for("MW")["categories"]["mieszkalnictwo_wielorodzinne"] == "allowed"
    assert use_matrix_for("U")["categories"]["uslugi"] == "allowed"
    assert use_matrix_for("P")["categories"]["magazyny"] == "allowed"
    assert use_matrix_for("ZP")["categories"]["mieszkalnictwo_jednorodzinne"] == "forbidden"


def test_compound_symbol_combines_permissively() -> None:
    cats = use_matrix_for("MN/U")["categories"]
    # An MN/U zone allows both the MN and the U use.
    assert cats["mieszkalnictwo_jednorodzinne"] == "allowed"
    assert cats["uslugi"] == "allowed"
    assert cats["produkcja"] == "conditional"  # U brings conditional, MN forbidden → max
    # Numeric prefixes ("1MW") are convention noise, not a different symbol.
    assert use_matrix_for("1MW")["categories"]["mieszkalnictwo_wielorodzinne"] == "allowed"


def test_unknown_symbol_is_unknown_everywhere() -> None:
    matrix = use_matrix_for("XYZ9")
    assert set(matrix["categories"].values()) == {"unknown"}
    assert matrix["confidence"] == 0.0
    assert any("unknown symbol" in str(t.values()) for t in matrix["trace"])


def test_unrecognized_concatenated_remainder_is_unknown_not_truncated() -> None:
    """m4 regression: 'MNE' must not silently decompose to ['MN'] (dropping the
    unrecognized 'E') — an undecomposable symbol is unknown, never a guess."""
    matrix = use_matrix_for("MNE")
    assert set(matrix["categories"].values()) == {"unknown"}
    assert matrix["confidence"] == 0.0
    # Fully-decomposable concatenations still resolve.
    assert use_matrix_for("MNU")["categories"]["uslugi"] == "allowed"
    assert use_matrix_for("MNU")["categories"]["mieszkalnictwo_jednorodzinne"] == "allowed"


def test_provisions_refine_matrix_with_citation() -> None:
    provisions = ["zakaz lokalizacji obiektów produkcyjnych, składów i magazynów"]
    matrix = use_matrix_for("U", provisions)
    assert matrix["categories"]["produkcja"] == "forbidden"
    assert matrix["categories"]["magazyny"] == "forbidden"
    assert matrix["basis"] == "symbol_convention+provisions"
    refinements = [t for t in matrix["trace"] if t.get("step") == "provision_refinement"]
    assert refinements and all("citation" in r for r in refinements)


def test_dopuszcza_sie_lifts_to_conditional() -> None:
    matrix = use_matrix_for("MN", ["dopuszcza się usługi nieuciążliwe w parterach"])
    # Already conditional for MN — stays conditional (never silently 'allowed').
    assert matrix["categories"]["uslugi"] == "conditional"
    forbidden_before = use_matrix_for("MN")["categories"]["mieszkalnictwo_wielorodzinne"]
    assert forbidden_before == "forbidden"
    lifted = use_matrix_for("MN", ["dopuszcza się zabudowę wielorodzinną"])
    assert lifted["categories"]["mieszkalnictwo_wielorodzinne"] == "conditional"


# --------------------------------------------------------------------------- #
# GML-vs-PDF conflict (F-0125, §25.2/§25.3)
# --------------------------------------------------------------------------- #
def test_gml_vs_pdf_height_conflict_manual_review() -> None:
    parsed = parse_app_gml(FIXTURE.read_bytes(), municipality_id="146501")
    mw = next(z for z in parsed.zones if z.symbol == "MW")
    gml_values = [
        IndicatorValue(
            name=name, value=value, source_id=f"gml:{mw.id}", source_kind="gml", detail="MW"
        )
        for name, value in indicators_from_zone(mw).items()
    ]
    # The parsed PDF text disagrees: 12 m where the GML attribute says 16 m.
    pdf_values = [
        IndicatorValue(
            name="max_height_m", value=12.0, source_id="doc:uchwala", source_kind="document"
        )
    ]
    conflicts = detect_conflicts(gml_values, pdf_values)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.indicator == "max_height_m"
    # Both values + both sources reported (§25.2 rules 1-2).
    assert {conflict.value_a, conflict.value_b} == {16.0, 12.0}
    assert conflict.source_a == f"gml:{mw.id}"
    assert conflict.source_b == "doc:uchwala"
    assert conflict.severity == "high"
    # Never silently resolved: status is manual_review_required (§25.2 rule 4)…
    assert conflict.status == MANUAL_REVIEW_REQUIRED
    # …and the record proposes who can resolve it (rule 5).
    assert "gmina" in conflict.resolution_hint


def test_agreeing_sources_produce_no_conflict() -> None:
    a = [IndicatorValue(name="max_height_m", value=16.0, source_id="gml:z1", source_kind="gml")]
    b = [IndicatorValue(name="max_height_m", value=16.0, source_id="doc:u1", source_kind="document")]
    assert detect_conflicts(a, b) == []


def test_indicator_present_in_one_source_only_is_not_a_conflict() -> None:
    a = [IndicatorValue(name="max_height_m", value=16.0, source_id="gml:z1", source_kind="gml")]
    b = [IndicatorValue(name="min_pbc_ratio", value=0.25, source_id="doc:u1", source_kind="document")]
    assert detect_conflicts(a, b) == []


def test_gml_percent_attributes_normalized_to_fractions_no_false_conflict() -> None:
    """M6 regression: APP percent attributes (e.g. PBC '25') must compare in the
    parser's fraction unit (0.25) — agreeing sources produce NO conflict, and a
    real disagreement still does."""
    zone = PlanningZone(
        id="zone:m6",
        act_id="act:m6",
        symbol="MW",
        attributes={
            "minimalnyUdzialPowierzchniBiologicznieCzynnej": "25",
            "maksymalnaPowierzchniaZabudowy": "40",
        },
    )
    indicators = indicators_from_zone(zone)
    assert indicators["min_pbc_ratio"] == 0.25  # percent attribute → fraction
    assert indicators["max_coverage_ratio"] == 0.40
    gml_values = [
        IndicatorValue(name=name, value=value, source_id="gml:m6", source_kind="gml")
        for name, value in indicators.items()
    ]
    # GML "25" (percent) vs PDF 0.25 (fraction): the sources AGREE → no conflict.
    agreeing = [
        IndicatorValue(name="min_pbc_ratio", value=0.25, source_id="doc:u1", source_kind="document")
    ]
    assert detect_conflicts(gml_values, agreeing) == []
    # GML "25" vs PDF 0.30: a real disagreement is still reported.
    disagreeing = [
        IndicatorValue(name="min_pbc_ratio", value=0.30, source_id="doc:u1", source_kind="document")
    ]
    conflicts = detect_conflicts(gml_values, disagreeing)
    assert len(conflicts) == 1
    assert conflicts[0].indicator == "min_pbc_ratio"
    assert {conflicts[0].value_a, conflicts[0].value_b} == {0.25, 0.30}


# --------------------------------------------------------------------------- #
# Planning stability heuristic (F-0126)
# --------------------------------------------------------------------------- #
def _act(status: str, valid_from: date | None) -> PlanningAct:
    return PlanningAct(
        id="act:test",
        municipality_id="146501",
        act_type=PlanningActType.MPZP,
        title="t",
        status=status,
        valid_from=valid_from,
    )


def test_stability_in_force_vs_draft() -> None:
    today = date(2026, 6, 10)
    in_force = stability_score(_act("prawnie wiazacy lub realizowany", date(2021, 3, 25)), today=today)
    draft = stability_score(_act("projekt planu", date(2026, 1, 1)), today=today)
    assert in_force["score"] > draft["score"]
    assert in_force["basis"] == "heuristic"  # never presented as a legal judgment
    assert any(f["factor"] == "status" for f in in_force["factors"])
    assert any(f["factor"] == "age" for f in in_force["factors"])


def test_stability_unknown_date_traced_not_defaulted() -> None:
    result = stability_score(_act("obowiazujacy", None), today=date(2026, 6, 10))
    age_factors = [f for f in result["factors"] if f["factor"] == "age"]
    assert age_factors and "unknown" in age_factors[0]["note"]


def test_stability_old_act_penalized_with_trace() -> None:
    today = date(2026, 6, 10)
    old = stability_score(_act("obowiazujacy", date(2004, 1, 1)), today=today)
    fresh = stability_score(_act("obowiazujacy", date(2026, 1, 1)), today=today)
    assert old["score"] < fresh["score"]
    assert any(f.get("delta") == -0.1 for f in old["factors"])


def test_stability_repealed_status_never_scored_as_in_force() -> None:
    """M1 regression: 'nieobowiązujący' contains the in-force substring
    'obowiąz' — the repealed keywords must win, never the in-force base."""
    today = date(2026, 6, 10)
    repealed = stability_score(_act("nieobowiązujący (uchylony)", date(2010, 1, 1)), today=today)
    status_factor = next(f for f in repealed["factors"] if f["factor"] == "status")
    assert status_factor["note"] == "act repealed/archived"
    assert status_factor["score"] == 0.1
    assert repealed["score"] < 0.5
    in_force = stability_score(_act("obowiązujący", date(2010, 1, 1)), today=today)
    assert in_force["score"] > repealed["score"]
