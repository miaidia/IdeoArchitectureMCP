"""Phase 9 capacity-engine tests (IMPLEMENTATION_PLAN_V2 §9.3).

Covers:
  * metrics golden: a fixture masterplan with KNOWN arithmetic → exact PUM / PUU /
    mieszkania / parking-balance numbers (hand-computed in comments);
  * per-stage rows sum to the SUMA row exactly (ROBYG exemplar consistency);
  * ground_floor_use split (usługi w parterze: parter → PUU, piętra → PUM);
  * basis metadata on every heuristic-derived metric (anti-pattern §0v2.4);
  * parking demand: a missing MPZP indicator → "unknown" + UnknownItem QUESTION, never
    a default number;
  * PBC: greenery + playground 30% credit arithmetic; the credit share AND the §40
    bracket are READ from the wt-40 ruleset (a tmp ruleset dir with edited values
    changes the outputs — proving no Python literal);
  * capacity_generate_scenarios: 4 scenarios ordered conservative<base<optimistic<max
    in PUM, sensitivity range present, missing indicators → partial + unknowns.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from plot_agent.drawing import MasterplanProposal
from plot_planning import (
    CapacityConfig,
    building_metrics,
    generate_capacity_scenarios,
    masterplan_metrics,
)
from plot_rules import load_rulesets
from shapely.geometry import Polygon, mapping

RULESET_DIR = "rulesets/PL"
WT40_YAML = Path(RULESET_DIR) / "building-technical" / "wt-40-plac-zabaw.yaml"


def _rect(x: float, y: float, w: float, h: float) -> dict:
    return mapping(Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)]))


def _golden_proposal() -> MasterplanProposal:
    """Masterplan with hand-computable arithmetic (config: 0.70 / 0.80 / 52.0).

    Building A (stage 1, mieszkalny, 1 underground floor):
        segment 20x30 = 600 m2, 5 floors -> PC 3000; PUM = 3000*0.70 = 2100.0;
        mieszkania = floor(2100/52) = 40; pc_podziemna = 600; GFA = 3600.
    Building B (stage 1, mieszkalny, usługi w parterze):
        segment 20x26 = 520 m2, 4 floors -> PC 2080;
        parter 520 -> PUU pool: PUU = 520*0.80 = 416.0;
        piętra 3x520 = 1560 -> PUM = 1560*0.70 = 1092.0; mieszkania = 1092/52 = 21.
    Building C (stage 2, uslugowy):
        segment 10x20 = 200 m2, 2 floors -> PC 400; PUU = 400*0.80 = 320.0.
    TOTALS: PUM = 3192.0; PUU = 736.0; PU = 3928.0; mieszkania = 61;
        powierzchnia zabudowy = 600+520+200 = 1320 (disjoint); parcel 100x100 = 10000;
        coverage = 0.132; PC nadziemna = 3000+2080+400 = 5480 -> intensywność 0.548.
    Parking (hala podziemna, 120 mp) vs MPZP demand:
        1.5/mieszkanie * 61 + 2.0/100 m2 PUU * 736 = 91.5 + 14.72 = 106.22.
    PBC: greenery 30x40 = 1200; playground 20x10 = 200 (disjoint);
        credit 0.30 (z wt-40) -> PBC = 1200 + 60 = 1260 -> ratio 0.126.
    Plac zabaw: 61 mieszkań -> §40 bracket 51-100 -> 50 m2 required; provided 200.
    """
    return MasterplanProposal.model_validate(
        {
            "buildings": [
                {
                    "name": "Budynek A",
                    "stage": 1,
                    "underground_floors": 1,
                    "segments": [
                        {"polygon": _rect(0, 0, 20, 30), "floors": 5, "use": "mieszkalny"}
                    ],
                },
                {
                    "name": "Budynek B",
                    "stage": 1,
                    "segments": [
                        {
                            "polygon": _rect(30, 0, 20, 26),
                            "floors": 4,
                            "use": "mieszkalny",
                            "ground_floor_use": "uslugowy",
                        }
                    ],
                },
                {
                    "name": "Budynek C",
                    "stage": 2,
                    "segments": [
                        {"polygon": _rect(60, 0, 10, 20), "floors": 2, "use": "uslugowy"}
                    ],
                },
            ],
            "parking": [
                {"kind": "hala_podziemna", "polygon": _rect(0, 40, 40, 20), "spaces": 120}
            ],
            "greenery_polygons": [_rect(60, 60, 30, 40)],
            "playgrounds": [_rect(0, 70, 20, 10)],
        }
    )


PARCEL = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
INDICATORS = {"parking_per_mieszkanie": 1.5, "parking_per_100m2_uslug": 2.0}


@pytest.fixture(scope="module")
def registry():
    return load_rulesets(RULESET_DIR)


@pytest.fixture(scope="module")
def golden_metrics(registry):
    return masterplan_metrics(_golden_proposal(), PARCEL, INDICATORS, registry=registry)


# --------------------------------------------------------------------------- #
# Metrics golden (hand-computed numbers)
# --------------------------------------------------------------------------- #
def test_totals_match_hand_computed(golden_metrics) -> None:
    t = golden_metrics.totals
    assert t["pum_m2"] == pytest.approx(3192.0)
    assert t["puu_m2"] == pytest.approx(736.0)
    assert t["pu_m2"] == pytest.approx(3928.0)
    assert t["mieszkania_estimate"] == 61
    assert t["powierzchnia_zabudowy_m2"] == pytest.approx(1320.0)
    assert t["coverage_ratio"] == pytest.approx(0.132)
    assert t["pc_nadziemna_m2"] == pytest.approx(5480.0)
    assert t["intensywnosc"] == pytest.approx(0.548)
    assert t["gfa_m2"] == pytest.approx(5480.0 + 600.0)  # 1 underground floor of A


def test_ground_floor_use_split(golden_metrics) -> None:
    """Usługi w parterze: parter → PUU, piętra → PUM (building B)."""
    b = next(m for m in golden_metrics.per_building if m.name == "Budynek B")
    assert b.pc_mieszkalna_m2 == pytest.approx(1560.0)  # 3 upper floors x 520
    assert b.pc_uslugowa_m2 == pytest.approx(520.0)  # parter
    assert b.pum_m2 == pytest.approx(1092.0)
    assert b.puu_m2 == pytest.approx(416.0)
    assert b.mieszkania_estimate == 21  # 1092 / 52 exactly


def test_mieszkalno_uslugowy_default_parter_assumption() -> None:
    """mieszkalno-uslugowy without ground_floor_use → parter→PUU + recorded assumption."""
    prop = MasterplanProposal.model_validate(
        {
            "buildings": [
                {
                    "name": "MU",
                    "segments": [
                        {"polygon": _rect(0, 0, 10, 10), "floors": 3, "use": "mieszkalno-uslugowy"}
                    ],
                }
            ]
        }
    )
    m = building_metrics(prop.buildings[0])
    assert m.pc_uslugowa_m2 == pytest.approx(100.0)  # parter
    assert m.pc_mieszkalna_m2 == pytest.approx(200.0)  # 2 upper floors
    assert any("ground_floor_use" in a for a in m.assumptions)


def test_stage_rows_sum_to_suma_exactly(golden_metrics) -> None:
    """Per-stage rows sum to the SUMA row exactly (exemplar 1257/63713/15576 contract)."""
    table = golden_metrics.stage_table
    suma = table[-1]
    assert suma["etap"] == "SUMA"
    stage_rows = table[:-1]
    for col in ("liczba_mieszkan", "pum_m2", "puu_m2", "pu_m2"):
        assert sum(r[col] for r in stage_rows) == pytest.approx(suma[col])
    # Hand-computed stage rows: stage 1 = A+B, stage 2 = C.
    row1 = next(r for r in stage_rows if r["etap"] == 1)
    assert (row1["liczba_mieszkan"], row1["pum_m2"], row1["puu_m2"]) == (61, 3192.0, 416.0)
    row2 = next(r for r in stage_rows if r["etap"] == 2)
    assert (row2["liczba_mieszkan"], row2["puu_m2"]) == (0, 320.0)
    assert suma["pu_m2"] == pytest.approx(golden_metrics.totals["pu_m2"])


def test_parking_balance_from_indicators(golden_metrics) -> None:
    """Demand = 1.5×61 + 2.0×736/100 = 106.22; supply 120 → balance +13.78."""
    p = golden_metrics.parking
    assert p["demand_spaces"] == pytest.approx(106.22)
    assert p["demand_basis"] == "planning_indicator"
    assert p["supply_spaces"] == 120
    assert p["balance"] == pytest.approx(13.78)
    assert p["within"] is True


def test_basis_metadata_on_every_heuristic_metric(golden_metrics) -> None:
    """Anti-pattern §0v2.4: PUM/PUU/mieszkania always carry basis metadata."""
    for b in golden_metrics.per_building:
        assert b.basis["pum_m2"] == "industry_heuristic"
        assert b.basis["puu_m2"] == "industry_heuristic"
        assert b.basis["mieszkania_estimate"] == "industry_heuristic"
        assert b.basis["powierzchnia_zabudowy_m2"] == "geometry_measured"
    assert golden_metrics.totals["basis"]["pum_m2"] == "industry_heuristic"
    assert "PN-ISO 9836" in golden_metrics.config_basis["measurement_standard"]
    assert golden_metrics.config_basis["basis"] == "industry_heuristic"


# --------------------------------------------------------------------------- #
# F1 regression: make_valid repair must reach the capacity engine
# --------------------------------------------------------------------------- #
# Bowtie (5,60)→(25,80)→(25,60)→(5,80): crossing at (15,70) → two 100 m² triangles.
BOWTIE_PLAYGROUND = {
    "type": "Polygon",
    "coordinates": [[[5, 60], [25, 80], [25, 60], [5, 80], [5, 60]]],
}


def test_bowtie_greenery_has_positive_area_in_metrics(registry) -> None:
    """F1(a): a parser-accepted bowtie greenery ring must contribute its REPAIRED area
    to the PBC balance (raw shapely area of the self-intersecting ring is 0.0)."""
    # Bowtie (0,60)→(10,70)→(10,60)→(0,70): two 25 m² triangles → 50 m² repaired.
    bowtie = {"type": "Polygon",
              "coordinates": [[[0, 60], [10, 70], [10, 60], [0, 70], [0, 60]]]}
    proposal = MasterplanProposal.model_validate(
        {
            "buildings": [{"name": "B", "segments": [
                {"polygon": _rect(10, 10, 20, 10), "floors": 3, "use": "mieszkalny"}]}],
            "greenery_polygons": [bowtie],
        }
    )
    metrics = masterplan_metrics(proposal, PARCEL, INDICATORS, registry=registry)
    assert metrics.pbc["greenery_m2"] == pytest.approx(50.0)  # repaired, not 0.0
    assert metrics.pbc["pbc_m2"] == pytest.approx(50.0)  # no playground → greenery only
    assert metrics.pbc["pbc_ratio"] == pytest.approx(0.005)  # 50 / 10 000


def test_bowtie_playground_overlapping_greenery_does_not_crash(registry) -> None:
    """F1(b): bowtie playground ∩ valid greenery used to raise an unhandled
    GEOSException (TopologyException) inside the PBC credit difference — the repaired
    geometry must flow through and the credit arithmetic must be hand-computable.

    Repaired playground = two 100 m² triangles (200 m² total); greenery rect
    (0,55)-(30,75) = 600 m²; playground part OUTSIDE greenery (above y=75) = two
    12.5 m² corner triangles = 25 m²; credit 0.30 → 7.5; PBC = 600 + 7.5 = 607.5.
    """
    proposal = MasterplanProposal.model_validate(
        {
            "buildings": [{"name": "B", "segments": [
                {"polygon": _rect(10, 10, 20, 10), "floors": 3, "use": "mieszkalny"}]}],
            "greenery_polygons": [_rect(0, 55, 30, 20)],
            "playgrounds": [BOWTIE_PLAYGROUND],
        }
    )
    metrics = masterplan_metrics(proposal, PARCEL, INDICATORS, registry=registry)
    assert metrics.playground["provided_m2"] == pytest.approx(200.0)
    assert metrics.pbc["playground_pbc_credit_m2"] == pytest.approx(7.5)
    assert metrics.pbc["pbc_m2"] == pytest.approx(607.5)


# --------------------------------------------------------------------------- #
# F2 regression: overlapping segments must not double-count PC/PUM
# --------------------------------------------------------------------------- #
def test_overlapping_segments_attribute_overlap_to_taller_segment() -> None:
    """F2: overlap is counted ONCE, attributed to the segment with MORE floors.

    Segment B (listed FIRST, 2 floors): 24×12 @ (12,0) → raw 288 m².
    Segment A (listed second, 4 floors): 24×12 @ (0,0) → raw 288 m².
    Overlap = 12×12 = 144 m² → PZ (union) = 288 + 288 − 144 = 432 m².
    Attribution by floors DESC (not list order): A (taller) keeps its full 288 m²;
    B owns 288 − 144 = 144 m².
    PC nadziemna = 288×4 + 144×2 = 1152 + 288 = 1440 m²
        (naive double-count would be 288×4 + 288×2 = 1728 m²).
    Both mieszkalny → pc_mieszkalna = 1440; PUM = 1440 × 0.70 = 1008.0;
    mieszkania = floor(1008 / 52) = 19.
    """
    proposal = MasterplanProposal.model_validate(
        {
            "buildings": [
                {
                    "name": "L",
                    "segments": [
                        {"rectangles": [{"x": 12, "y": 0, "w": 24, "h": 12}],
                         "floors": 2, "use": "mieszkalny"},
                        {"rectangles": [{"x": 0, "y": 0, "w": 24, "h": 12}],
                         "floors": 4, "use": "mieszkalny"},
                    ],
                }
            ]
        }
    )
    m = building_metrics(proposal.buildings[0])
    assert m.powierzchnia_zabudowy_m2 == pytest.approx(432.0)
    assert m.pc_nadziemna_m2 == pytest.approx(1440.0)  # NOT 1728
    assert m.pc_mieszkalna_m2 == pytest.approx(1440.0)
    assert m.pum_m2 == pytest.approx(1008.0)
    assert m.mieszkania_estimate == 19
    assert m.floors_by_segment == [2, 4]  # output order stays the payload order
    assert any("overlap" in a for a in m.assumptions)  # attribution explained


def test_disjoint_segments_have_no_overlap_assumption(golden_metrics) -> None:
    """F2: the non-overlapping case is unchanged — no overlap assumption recorded."""
    for b in golden_metrics.per_building:
        assert not any("overlap" in a for a in b.assumptions)


def test_cross_building_footprint_overlap_recorded_as_warning(registry) -> None:
    """F2: footprints of DISTINCT buildings overlapping is a drawing error → a metrics
    warning naming both buildings (not a stacking idiom, not a hard violation)."""
    proposal = MasterplanProposal.model_validate(
        {
            "buildings": [
                {"name": "B1", "segments": [
                    {"polygon": _rect(0, 0, 20, 10), "floors": 3, "use": "mieszkalny"}]},
                {"name": "B2", "segments": [
                    {"polygon": _rect(10, 0, 20, 10), "floors": 2, "use": "mieszkalny"}]},
            ]
        }
    )
    metrics = masterplan_metrics(proposal, PARCEL, INDICATORS, registry=registry)
    assert len(metrics.warnings) == 1
    assert "B1" in metrics.warnings[0] and "B2" in metrics.warnings[0]
    assert "100.00" in metrics.warnings[0]  # 10×10 m² overlap quantified
    assert metrics.to_dict()["warnings"] == metrics.warnings
    # The footprint UNION semantics for PZ stay: 200 + 200 − 100 = 300 m².
    assert metrics.totals["powierzchnia_zabudowy_m2"] == pytest.approx(300.0)


def test_no_cross_building_warning_when_disjoint(golden_metrics) -> None:
    assert golden_metrics.warnings == []


# --------------------------------------------------------------------------- #
# Zestawienie powierzchni: overlapping components are machine-readable (review F2)
# --------------------------------------------------------------------------- #
def test_zestawienie_overlap_machine_readable_with_warning(registry) -> None:
    """Review F2: a road corridor crossing zieleń double-counts the crossing in
    drogi AND PBC. Component semantics stay (Phase 9 contract — numbers are NOT
    silently renumbered), but the double count is emitted as ``overlap_m2`` and
    flagged in ``MasterplanMetrics.warnings``."""
    proposal = MasterplanProposal.model_validate(
        {
            "buildings": [
                {"name": "A", "segments": [
                    {"polygon": _rect(0, 0, 20, 30), "floors": 3, "use": "mieszkalny"}]},
            ],
            # Corridor y∈[40,60] (100×20 = 2000 m²) crosses greenery x∈[60,90]
            # (30×100 = 3000 m²) → 30×20 = 600 m² counted in BOTH components.
            "roads": [
                {"centerline": {"type": "LineString", "coordinates": [[0, 50], [100, 50]]},
                 "width_m": 20, "function": "kdw"},
            ],
            "greenery_polygons": [_rect(60, 0, 30, 100)],
        }
    )
    metrics = masterplan_metrics(proposal, PARCEL, INDICATORS, registry=registry)
    z = metrics.zestawienie
    # Documented component semantics unchanged (no silent renumbering)…
    assert z["drogi_i_utwardzenia_m2"] == pytest.approx(2000.0)
    assert metrics.pbc["greenery_m2"] == pytest.approx(3000.0)
    assert z["pbc_m2"] == pytest.approx(3000.0)
    # …and the double-counted ground area is machine-readable + warned.
    assert z["overlap_m2"] == pytest.approx(600.0, abs=1.0)
    assert z["basis"]["overlap_m2"] == "geometry_measured"
    assert any("zestawienie" in w and "600.00" in w for w in metrics.warnings)


def test_zestawienie_overlap_zero_when_disjoint(golden_metrics) -> None:
    """Disjoint golden components → overlap_m2 == 0 and NO overlap warning."""
    assert golden_metrics.zestawienie["overlap_m2"] == 0.0
    assert not any("zestawienie" in w for w in golden_metrics.warnings)


# --------------------------------------------------------------------------- #
# Parking: missing MPZP indicator → unknown + question, never a default
# --------------------------------------------------------------------------- #
def test_missing_parking_indicator_is_unknown_not_default(registry) -> None:
    metrics = masterplan_metrics(_golden_proposal(), PARCEL, {}, registry=registry)
    assert metrics.parking["demand_spaces"] == "unknown"
    assert metrics.parking["balance"] == "unknown"
    assert "parking_per_mieszkanie" in metrics.parking["missing_indicators"]
    unknown = next(u for u in metrics.unknowns if u.topic == "parking_demand")
    assert "gmin" in (unknown.suggested_action or "")  # the question for the gmina
    assert unknown.reason == "indicator_not_found"


# --------------------------------------------------------------------------- #
# PBC credit + §40 bracket: values come from the RULESET, not literals
# --------------------------------------------------------------------------- #
def test_pbc_credit_arithmetic_from_ruleset(golden_metrics) -> None:
    pbc = golden_metrics.pbc
    assert pbc["playground_credit_share"] == pytest.approx(0.30)  # read from wt-40 YAML
    assert pbc["playground_pbc_credit_m2"] == pytest.approx(60.0)  # 200 × 0.30
    assert pbc["pbc_m2"] == pytest.approx(1260.0)  # 1200 + 60
    assert pbc["pbc_ratio"] == pytest.approx(0.126)
    assert "Dz.U." in pbc["playground_credit_share_source"]


def test_playground_bracket_resolved_via_engine(golden_metrics) -> None:
    """61 mieszkań → §40 ust. 8 pkt 2 bracket (51-100) → 50 m² (from the select table)."""
    play = golden_metrics.playground
    assert play["required_m2"] == pytest.approx(50.0)
    assert play["required_basis"] == "ruleset"
    assert play["within"] is True  # 200 m² provided
    assert play["rule_check"]["rule_id"] == "PL-WT-40-PLAC-ZABAW-001"


def test_legal_values_follow_edited_tmp_ruleset(tmp_path: Path) -> None:
    """Edit the wt-40 values in a tmp ruleset dir → outputs follow (no Python literal).

    The 30% PBC-credit share becomes 50% and the 51-100-mieszkań bracket becomes 75 m²;
    both must flow through to the metrics, proving the engine reads the YAML.
    """
    src = WT40_YAML.read_text(encoding="utf-8")
    edited = src.replace("min_pbc_share: 0.30", "min_pbc_share: 0.50")
    edited = edited.replace("      value: 50.0", "      value: 75.0")
    assert edited != src
    ruleset_dir = tmp_path / "PL" / "building-technical"
    ruleset_dir.mkdir(parents=True)
    (ruleset_dir / "wt-40-plac-zabaw.yaml").write_text(edited, encoding="utf-8")

    registry = load_rulesets(tmp_path / "PL")
    metrics = masterplan_metrics(_golden_proposal(), PARCEL, INDICATORS, registry=registry)
    assert metrics.pbc["playground_credit_share"] == pytest.approx(0.50)
    assert metrics.pbc["playground_pbc_credit_m2"] == pytest.approx(100.0)  # 200 × 0.5
    assert metrics.pbc["pbc_m2"] == pytest.approx(1300.0)
    assert metrics.playground["required_m2"] == pytest.approx(75.0)


def test_missing_ruleset_yields_unknown_credit(tmp_path: Path) -> None:
    """No wt-40 rule loaded → PBC credit / bracket are UNKNOWN, never guessed."""
    empty = load_rulesets(tmp_path)  # empty registry
    metrics = masterplan_metrics(_golden_proposal(), PARCEL, INDICATORS, registry=empty)
    assert metrics.pbc["pbc_m2"] == "unknown"
    assert metrics.playground["required_m2"] == "unknown"
    topics = {u.topic for u in metrics.unknowns}
    assert "pbc_playground_credit" in topics
    assert "plac_zabaw_required_area" in topics


# --------------------------------------------------------------------------- #
# capacity_generate_scenarios (F-0202–0206)
# --------------------------------------------------------------------------- #
SCENARIO_INDICATORS = {
    "max_kondygnacje": 5,
    "max_coverage_ratio": 0.30,
    "max_intensity": 1.2,
    "parking_per_mieszkanie": 1.0,
}


def test_four_scenarios_ordered_by_pum() -> None:
    """Golden parcel (10 000 m²) + envelope (5 000 m²) + indicators → ordered PUM.

    conservative: min(2000, 3000)×3 floors ×0.65 = 3900; base: PC capped by
    intensywność 1.2 → 12000×0.70 = 8400; optimistic 12000×0.73 = 8760;
    max 12000×0.75 = 9000.
    """
    out = generate_capacity_scenarios(
        envelope_area_m2=5000.0,
        parcel_area_m2=10000.0,
        indicators=SCENARIO_INDICATORS,
    )
    assert [s.scenario_type for s in out.scenarios] == [
        "conservative", "base", "optimistic", "max",
    ]
    pums = [s.metrics["pum_m2"] for s in out.scenarios]
    assert pums == sorted(pums)
    assert len(set(pums)) == 4  # strictly increasing
    assert pums[0] == pytest.approx(3900.0)
    assert pums[1] == pytest.approx(8400.0)
    assert pums[3] == pytest.approx(9000.0)
    base = out.scenarios[1]
    assert any("max_intensity" in b for b in base.metrics["bounded_by"])  # citation
    assert base.metrics["basis"] == "industry_heuristic"
    assert base.metrics["parking_demand_spaces"] == pytest.approx(
        1.0 * base.metrics["mieszkania_estimate"]
    )


def test_scenarios_sensitivity_range_present() -> None:
    out = generate_capacity_scenarios(
        envelope_area_m2=5000.0, parcel_area_m2=10000.0, indicators=SCENARIO_INDICATORS
    )
    low, high = out.sensitivity["pum_range_m2"]
    base_pum = out.scenarios[1].metrics["pum_m2"]
    assert low < base_pum < high
    assert out.sensitivity["parameter"] == "pum_efficiency"


def test_scenarios_missing_parking_indicator_unknown_not_default() -> None:
    indicators = {k: v for k, v in SCENARIO_INDICATORS.items() if k != "parking_per_mieszkanie"}
    out = generate_capacity_scenarios(
        envelope_area_m2=5000.0, parcel_area_m2=10000.0, indicators=indicators
    )
    for s in out.scenarios:
        assert s.metrics["parking_demand_spaces"] == "unknown"
    unknown = next(u for u in out.unknowns if u.topic == "parking_demand")
    assert "gmin" in (unknown.suggested_action or "")


def test_scenarios_missing_floor_indicators_partial() -> None:
    out = generate_capacity_scenarios(
        envelope_area_m2=5000.0, parcel_area_m2=10000.0, indicators={}
    )
    assert all(s.metrics["status"] == "partial" for s in out.scenarios)
    assert all(s.metrics["pum_m2"] == "unknown" for s in out.scenarios)
    assert any(u.topic == "max_kondygnacje/max_height_m" for u in out.unknowns)


def test_config_defaults_are_the_documented_heuristics() -> None:
    cfg = CapacityConfig()
    assert (cfg.pum_efficiency, cfg.puu_efficiency) == (0.70, 0.80)
    assert (cfg.avg_mieszkanie_m2, cfg.floor_height_m) == (52.0, 3.3)


# --------------------------------------------------------------------------- #
# MCP use-case wiring (no-drawing mode behind capacity_generate_scenarios)
# --------------------------------------------------------------------------- #
def test_usecase_capacity_generate_scenarios_from_stored_analysis() -> None:
    from plot_agent.analysis import DEFAULT_STORE
    from plot_domain import AnalysisResult, BuildableEnvelope, Parcel
    from plot_mcp_server import usecases

    analysis_id = "cap-test-1"
    DEFAULT_STORE.put(
        AnalysisResult(
            analysis_id=analysis_id,
            status="complete",  # type: ignore[arg-type]
            decision="OK_WITH_RISKS",  # type: ignore[arg-type]
            parcel=Parcel(id="p1", area_m2=10000.0),
            buildable_envelope=BuildableEnvelope(id="e1", area_m2=5000.0),
        )
    )
    out = usecases.capacity_generate_scenarios(
        analysis_id,
        # planning_parse_document-shaped indicator list is accepted too.
        [{"name": "max_kondygnacje", "value": 5}, {"name": "max_coverage_ratio", "value": 0.3}],
    )
    assert out["status"] == "computed"
    assert len(out["scenarios"]) == 4
    assert out["scenarios"][0]["scenario_type"] == "conservative"
    assert out["config"]["basis"] == "industry_heuristic"
    assert "sensitivity" in out


def test_usecase_capacity_not_found() -> None:
    from plot_mcp_server import usecases

    out = usecases.capacity_generate_scenarios("missing-id")
    assert out["status"] == "not_found"
    assert out["scenarios"] == []
