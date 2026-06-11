"""Phase 10 goldens — §19/§21 parking, §39 PBC, §40 plac zabaw, droga pożarowa.

Plan §10.3 cases: (c) a 25-stall surface lot 8 m from a windowed wall fails
(11–60 bracket → 10 m) and passes at 12 m; (d) a 120-mieszkanie zespół without
a playground fails §40 with the required-area message (0.5 m²/mieszkanie →
60 m²) and passes with an adequate one; (g) śródmiejska halves the §40 area
(at least 20 m²); (f) an 18+ m building without a fire road fails and passes
with a compliant one.
"""

from __future__ import annotations

import pytest
from plot_planning.wt_validators import (
    RULE_PPOZ_DROGA,
    RULE_WT19,
    RULE_WT21,
    RULE_WT39,
    RULE_WT40,
    build_context,
    check_fire_road,
    check_parking_distances,
    check_pbc_playground,
)
from plot_rules import RuleStatus, load_rulesets
from tests.wt_fixtures import (
    SOUTH,
    building,
    checks_for,
    gj_rect,
    masterplan,
    parcel_square,
)


@pytest.fixture(scope="module")
def registry():
    return load_rulesets("rulesets/PL")


# --------------------------------------------------------------------------- #
# (c) §19 — surface parking distances by stall-count bracket
# --------------------------------------------------------------------------- #
def _parking_plan(gap_m: float, *, spaces: int = 25):
    # Windowed wall = the building's SOUTH wall at y=50; the lot sits gap_m south
    # of it, 32x20 m (≥ 25 stalls x 25 m² → no plausibility warning), ≥6 m from
    # every parcel boundary (the 11–60 bracket needs 6 m).
    return masterplan(
        [building("Mieszkalny", 50, 50, 40, 14, 4, windowed_walls=[SOUTH])],
        parking=[
            {
                "kind": "naziemny",
                "polygon": gj_rect(50, 50 - gap_m - 20, 32, 20),
                "spaces": spaces,
            }
        ],
    )


def test_wt19_25_stalls_8m_from_windows_fails(registry) -> None:
    ctx = build_context(_parking_plan(8.0), parcel_square())
    checks = checks_for(check_parking_distances(ctx, registry), RULE_WT19)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.FAIL
    assert checks[0].geometry_evidence["distance_to_windows_m"] == pytest.approx(8.0)
    # The binding bracket (11–60 stalls → 10 m) came from the YAML select table.
    entry = next(
        e
        for e in checks[0].trace["checks"]
        if e.get("name") == "odleglosc-od-okien-osobowe"
    )
    assert entry["status"] == "fail"
    assert entry["target_value"] == pytest.approx(10.0)


def test_wt19_25_stalls_12m_from_windows_passes(registry) -> None:
    ctx = build_context(_parking_plan(12.0), parcel_square())
    checks = checks_for(check_parking_distances(ctx, registry), RULE_WT19)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.PASS


def test_wt19_underground_and_built_in_parking_exempt(registry) -> None:
    # §19 ust. 1 governs open-air stalls/open garages; hala podziemna i garaż
    # wbudowany are out of its distance regime (documented scope decision).
    proposal = masterplan(
        [building("Mieszkalny", 50, 50, 40, 14, 4)],
        parking=[
            {"kind": "hala_podziemna", "polygon": gj_rect(50, 20, 30, 20), "spaces": 80},
            {"kind": "wbudowany", "polygon": gj_rect(50, 50, 10, 10), "spaces": 10},
        ],
    )
    ctx = build_context(proposal, parcel_square())
    assert check_parking_distances(ctx, registry) == []


def test_wt21_implausible_stall_count_warns(registry) -> None:
    # 25 stalls declared on ~160 m² (needs ~625 m² at ~25 m²/stall heuristic).
    proposal = masterplan(
        [building("Mieszkalny", 50, 50, 40, 14, 4, windowed_walls=[SOUTH])],
        parking=[
            {"kind": "naziemny", "polygon": gj_rect(50, 22, 16, 10), "spaces": 25}
        ],
    )
    ctx = build_context(proposal, parcel_square())
    warnings = checks_for(check_parking_distances(ctx, registry), RULE_WT21)
    assert len(warnings) == 1
    assert warnings[0].status is RuleStatus.WARNING
    assert warnings[0].severity == "soft"
    assert "industry_heuristic" in warnings[0].message


# --------------------------------------------------------------------------- #
# (d) §40 — 120-mieszkanie zespół playground brackets (+ §39 PBC)
# --------------------------------------------------------------------------- #
def _zespol(playgrounds: list[dict] | None, *, greenery_big: bool, srodmiejska=False):
    # 40x28 m x 8 kond. mieszkalny → PUM = 1120·8·0.7 = 6272 m² → 120 mieszkań
    # (Phase 9 estimate, ⌊6272/52⌋) → §40 bracket 101–300 → 0.5 m²/mieszkanie = 60 m².
    greenery = gj_rect(10, 100, 180, 60) if greenery_big else gj_rect(100, 100, 60, 60)
    return masterplan(
        [building("Zespol A", 10, 10, 40, 28, 8)],
        greenery_polygons=[greenery],
        playgrounds=playgrounds or [],
        zabudowa_srodmiejska=srodmiejska,
    )


def test_wt40_120_mieszkan_without_playground_fails_with_required_area(registry) -> None:
    proposal = _zespol(None, greenery_big=False)
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_pbc_playground(ctx, registry), RULE_WT40)
    assert len(checks) == 1
    check = checks[0]
    assert check.status is RuleStatus.FAIL
    assert "120 mieszkan" in check.message
    assert "wymagane >= 60.0 m2" in check.message  # 0.5 m²/mieszkanie z YAML select
    assert "powierzchnia-placu-zabaw" in check.message


def test_wt40_adequate_playground_passes(registry) -> None:
    # 80 m² ≥ 60 m², fully on greenery (PBC share 1.0), far from windows
    # (≥10 m), unshaded (≥2 h równonoc 10–16).
    proposal = _zespol([gj_rect(120, 120, 10, 8)], greenery_big=True)
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_pbc_playground(ctx, registry), RULE_WT40)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.PASS
    # The waste-distance gap stays RECORDED (no śmietnik element in the DSL).
    assert checks[0].geometry_evidence["waste_distance_component"] == "not_modeled"


def test_wt39_pbc_ratio_from_phase9_metrics(registry) -> None:
    # Small greenery: 3600 m² / 40 000 m² = 9% < statutory 25% → §39 FAIL;
    # big greenery: 10 800 m² = 27% → PASS. Balance REUSES masterplan_metrics.
    short = build_context(_zespol(None, greenery_big=False), parcel_square())
    ok = build_context(_zespol(None, greenery_big=True), parcel_square())
    fail = checks_for(check_pbc_playground(short, registry), RULE_WT39)
    good = checks_for(check_pbc_playground(ok, registry), RULE_WT39)
    assert len(fail) == 1 and fail[0].status is RuleStatus.FAIL
    assert len(good) == 1 and good[0].status is RuleStatus.PASS


def test_wt40_below_trigger_is_not_applicable(registry) -> None:
    # 20x10 m x 3 kond. → ⌊600·0.7/52⌋ = 8 mieszkań ≤ 20 → obligation never arose.
    proposal = masterplan(
        [building("Maly", 10, 10, 20, 10, 3)],
        greenery_polygons=[gj_rect(10, 100, 180, 60)],
    )
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_pbc_playground(ctx, registry), RULE_WT40)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.NOT_APPLICABLE


# (g) śródmiejska halves the §40 area (with the 20 m² floor): 40 m² flips.
@pytest.mark.parametrize(
    ("srodmiejska", "expected"), [(False, RuleStatus.FAIL), (True, RuleStatus.PASS)]
)
def test_wt40_srodmiejska_halves_required_area(
    registry, srodmiejska: bool, expected: RuleStatus
) -> None:
    proposal = _zespol(
        [gj_rect(120, 120, 8, 5)],  # 40 m²: < 60 full, ≥ max(30, 20) śródmiejska
        greenery_big=True,
        srodmiejska=srodmiejska,
    )
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_pbc_playground(ctx, registry), RULE_WT40)
    assert len(checks) == 1
    assert checks[0].status is expected


# --------------------------------------------------------------------------- #
# (f) droga pożarowa — 18+ m building
# --------------------------------------------------------------------------- #
def _tall_building(roads: list[dict]):
    # 6 kondygnacji × 3.3 m = 19.8 m > 12 m (średniowysoki) → fire road required.
    return masterplan(
        [building("Wysoki", 80, 60, 40, 14, 6)],
        roads=roads,
    )


def test_fire_road_missing_fails(registry) -> None:
    ctx = build_context(_tall_building([]), parcel_square())
    checks = checks_for(check_fire_road(ctx, registry), RULE_PPOZ_DROGA)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.FAIL
    assert "Wysoki" in checks[0].message
    assert "nie zawiera drogi" in checks[0].message


def test_fire_road_compliant_passes(registry) -> None:
    # Straight 4 m pozarowa road spanning the parcel (no dead end), corridor
    # edge 8 m from the building's southern long facade (legal band 5–15 m).
    road = {
        "centerline": {
            "type": "LineString",
            "coordinates": [[500_000.0, 500_050.0], [500_200.0, 500_050.0]],
        },
        "width_m": 4.0,
        "function": "pozarowa",
    }
    ctx = build_context(_tall_building([road]), parcel_square())
    checks = checks_for(check_fire_road(ctx, registry), RULE_PPOZ_DROGA)
    assert len(checks) == 1
    check = checks[0]
    assert check.status is RuleStatus.PASS
    assert check.geometry_evidence["min_edge_distance_m"] == pytest.approx(8.0)
    assert check.geometry_evidence["coverage_share"] == pytest.approx(1.0)


def test_fire_road_wide_kdw_counts_as_candidate(registry) -> None:
    # A kdw of legal width (≥4 m) along the long facade also satisfies the check.
    road = {
        "centerline": {
            "type": "LineString",
            "coordinates": [[500_000.0, 500_050.0], [500_200.0, 500_050.0]],
        },
        "width_m": 5.0,
        "function": "kdw",
    }
    ctx = build_context(_tall_building([road]), parcel_square())
    checks = checks_for(check_fire_road(ctx, registry), RULE_PPOZ_DROGA)
    assert checks[0].status is RuleStatus.PASS


def test_fire_road_not_required_below_12m(registry) -> None:
    # 3 kondygnacje = 9.9 m ≤ 12 m → explicit NOT_APPLICABLE, never silent.
    proposal = masterplan([building("Niski", 80, 60, 40, 14, 3)])
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_fire_road(ctx, registry), RULE_PPOZ_DROGA)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.NOT_APPLICABLE
    assert "niewymagana" in checks[0].message
