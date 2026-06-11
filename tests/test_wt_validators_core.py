"""Phase 10 goldens — §12 setbacks, §13 przesłanianie, §271-273 fire separation.

Plan §10.3 cases: (a) two 5-kond. blocks at 7 m → §13 fail / spaced → pass;
(e) ZL pair at 6 m → §271 fail / 8 m → pass; (g) śródmiejska halves §13;
(i) §12 windowless brackets incl. the >4-kond. 5 m case; (l) editing the §271
base distance in a COPY of the ruleset flips the outcome — proving every
threshold is registry-sourced, never a Python literal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from plot_planning.wt_validators import (
    RULE_PPOZ_271,
    RULE_WT12,
    RULE_WT13,
    ValidatorConfig,
    build_context,
    check_boundary_setbacks,
    check_fire_separation,
    check_przeslanianie,
)
from plot_rules import RuleStatus, load_rulesets
from shapely.geometry import LineString, Polygon
from tests.wt_fixtures import (
    EAST,
    SOUTH,
    WEST,
    X0,
    Y0,
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
# (i) §12 — wall-plane setbacks from the parcel boundary
# --------------------------------------------------------------------------- #
def test_wt12_over4_kond_windowless_wall_at_4m_fails(registry) -> None:
    # 5-kond. multifamily, DECLARED windowless walls, west wall 4 m from the
    # boundary — the >4-kond. bracket requires 5 m (both windowed and windowless).
    proposal = masterplan(
        [building("Budynek W", 4, 40, 20, 14, 5, declare_windowless=True)]
    )
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_boundary_setbacks(ctx, registry), RULE_WT12)
    fails = [c for c in checks if c.status is RuleStatus.FAIL]
    assert len(fails) == 1, "exactly the west wall violates §12"
    fail = fails[0]
    assert "Budynek W" in fail.message
    assert fail.geometry_evidence is not None
    assert fail.geometry_evidence["edge_index"] == WEST
    assert fail.geometry_evidence["distance_to_boundary_m"] == pytest.approx(4.0)
    assert fail.geometry_evidence["windowed"] is False
    assert fail.geometry_evidence["is_multifamily_over_4_storeys"] is True
    assert fail.geometry_evidence["geometry"]["type"] == "LineString"


def test_wt12_3kond_windowless_wall_at_3m_passes(registry) -> None:
    proposal = masterplan(
        [building("Budynek N", 3, 40, 20, 14, 3, declare_windowless=True)]
    )
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_boundary_setbacks(ctx, registry), RULE_WT12)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.PASS
    assert checks[0].geometry_evidence["distance_to_boundary_m"] == pytest.approx(3.0)


def test_wt12_undeclared_windows_use_conservative_default(registry) -> None:
    # windowed_walls=None → ALL walls treated as windowed (4 m bracket for a
    # 3-kond. building) and the assumption is MARKED in the evidence (§10.4).
    proposal = masterplan([building("Budynek A", 3.5, 40, 20, 14, 3)])
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_boundary_setbacks(ctx, registry), RULE_WT12)
    fails = [c for c in checks if c.status is RuleStatus.FAIL]
    assert fails, "3.5 m windowed wall < 4 m must fail under the conservative default"
    assert all(c.geometry_evidence["assumed_windowed"] is True for c in fails)


def test_wt12_existing_buildings_are_not_evaluated(registry) -> None:
    proposal = masterplan(
        [building("Istniejacy", 1, 40, 20, 14, 5, status="istniejacy")]
    )
    ctx = build_context(proposal, parcel_square())
    assert check_boundary_setbacks(ctx, registry) == []


# --------------------------------------------------------------------------- #
# (a) §13 — two 5-kond. blocks: 7 m fail / 20 m pass
# --------------------------------------------------------------------------- #
def _two_blocks(gap_m: float, *, srodmiejska: bool = False):
    # 40x14 m slabs, 5 kondygnacji (16.5 m); wysokość przesłaniania = 15.5 m.
    return masterplan(
        [
            building("Blok A", 10, 10, 40, 14, 5),
            building("Blok B", 10, 24 + gap_m, 40, 14, 5),
        ],
        zabudowa_srodmiejska=srodmiejska,
    )


def test_wt13_two_5kond_blocks_at_7m_fail(registry) -> None:
    ctx = build_context(_two_blocks(7.0), parcel_square())
    checks = check_przeslanianie(ctx, registry)
    for name in ("Blok A", "Blok B"):
        mine = checks_for(checks, RULE_WT13, name)
        assert any(c.status is RuleStatus.FAIL for c in mine), name
    fail = next(c for c in checks if c.status is RuleStatus.FAIL)
    # Human message names both buildings of the offending pair.
    assert "Blok A" in fail.message and "Blok B" in fail.message
    assert fail.geometry_evidence["geometry"] is not None


def test_wt13_two_5kond_blocks_spaced_pass(registry) -> None:
    ctx = build_context(_two_blocks(20.0), parcel_square())
    checks = check_przeslanianie(ctx, registry)
    assert checks, "an explicit PASS per building — never a silent skip"
    assert all(c.status is RuleStatus.PASS for c in checks)


# (g) śródmiejska halves the §13 distance (15.5 m → 7.75 m) — 10 m flips.
@pytest.mark.parametrize(
    ("srodmiejska", "expected_fail"), [(False, True), (True, False)]
)
def test_wt13_srodmiejska_halves_required_distance(
    registry, srodmiejska: bool, expected_fail: bool
) -> None:
    ctx = build_context(
        _two_blocks(10.0, srodmiejska=srodmiejska), parcel_square()
    )
    checks = check_przeslanianie(ctx, registry)
    assert checks
    has_fail = any(c.status is RuleStatus.FAIL for c in checks)
    assert has_fail is expected_fail


# --------------------------------------------------------------------------- #
# M1 — §13 covers "przeslaniajaca czesc tego samego budynku" (own wings)
# --------------------------------------------------------------------------- #
def test_wt13_u_shaped_building_own_wing_obstructs(registry) -> None:
    # WT §13 ust. 1 pkt 1 explicitly names "przeslaniajaca czesc tego samego
    # budynku": two 5-kond. hotel wings of ONE building, 5 m apart, windowed
    # inner walls. Wysokosc przeslaniania = 16.5 − 1.0 = 15.5 m >> 5 m → FAIL
    # naming the building's OWN wing — never an affirmative false PASS.
    proposal = masterplan(
        [
            {
                "name": "Hotel U",
                "segments": [
                    {"polygon": gj_rect(10, 10, 10, 30), "floors": 5,
                     "use": "hotelowy", "windowed_walls": [EAST]},
                    {"polygon": gj_rect(25, 10, 10, 30), "floors": 5,
                     "use": "hotelowy", "windowed_walls": [WEST]},
                ],
            }
        ]
    )
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_przeslanianie(ctx, registry), RULE_WT13)
    fails = [c for c in checks if c.status is RuleStatus.FAIL]
    assert fails, "5 m between own wings must FAIL par. 13 (own-building part)"
    obstructors = {c.geometry_evidence["obstructor"] for c in fails}
    assert any(o.startswith("Hotel U") for o in obstructors), (
        "the named obstructor must be the building's OWN wing"
    )
    assert all(c.geometry_evidence["distance_m"] < 5.5 for c in fails)


def test_wt13_straight_single_segment_slab_passes_alone(registry) -> None:
    # M1 control: a straight single-segment slab is NOT obstructed by its own
    # footprint — the explicit no-obstructor PASS stays unchanged.
    proposal = masterplan([building("Slab", 60, 60, 40, 14, 5)])
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_przeslanianie(ctx, registry), RULE_WT13)
    assert checks
    assert all(c.status is RuleStatus.PASS for c in checks)


# --------------------------------------------------------------------------- #
# m5 — §12 facing-boundary distance must not overstate for oblique granice
# --------------------------------------------------------------------------- #
def test_wt12_oblique_boundary_distance_not_anti_conservative(registry) -> None:
    # 45°-slanted granica (SW corner cut). The corridor-clipped measure alone
    # reports 5.50 m for the south wall (≥ the 5 m bracket → false PASS); the
    # wall's true distance to the boundary stretch it faces is 5.5/√2 ≈ 3.89 m
    # (the nearest point lies diagonally off the wall's west end, just OUTSIDE
    # the perpendicular corridor) → must FAIL the >4-kond. 5 m bracket.
    parcel = Polygon(
        [
            (X0 + 0, Y0 + 40), (X0 + 40, Y0 + 0), (X0 + 160, Y0 + 0),
            (X0 + 160, Y0 + 160), (X0 + 0, Y0 + 160),
        ]
    )
    proposal = masterplan([building("Skos", 20, 25.5, 20, 10, 5)])
    ctx = build_context(proposal, parcel)
    checks = checks_for(check_boundary_setbacks(ctx, registry), RULE_WT12)
    fails = [c for c in checks if c.status is RuleStatus.FAIL]
    south = [c for c in fails if c.geometry_evidence["edge_index"] == SOUTH]
    assert south, "the south wall facing the 45° granica must FAIL, not pass at 5.5 m"
    reported = south[0].geometry_evidence["distance_to_boundary_m"]
    # Brute force: true min distance from the wall to boundary points lying in
    # the wall's outward half-plane (y < wall) — reported may never overstate it.
    wall_line = LineString([(X0 + 20, Y0 + 25.5), (X0 + 40, Y0 + 25.5)])
    ring = parcel.exterior
    samples = (ring.interpolate(i / 8000 * ring.length) for i in range(8001))
    true_min = min(
        float(wall_line.distance(p)) for p in samples if p.y < Y0 + 25.5
    )
    assert reported <= true_min + 1e-6
    assert reported == pytest.approx(5.5 / 2**0.5, abs=0.01)
    # The interpretation (corridor + faced-edge half-plane min) is in the trace.
    interp = south[0].trace["distance_interpretation"]
    assert interp["corridor_distance_m"] == pytest.approx(5.5, abs=0.01)


# --------------------------------------------------------------------------- #
# m6 — §13 outputs carry the heuristic basis entries they were built on
# --------------------------------------------------------------------------- #
def test_wt13_checks_carry_heuristic_basis(registry) -> None:
    ctx = build_context(_two_blocks(7.0), parcel_square())
    checks = checks_for(check_przeslanianie(ctx, registry), RULE_WT13)
    decided = [c for c in checks if c.status in (RuleStatus.FAIL, RuleStatus.PASS)]
    assert decided
    expected = ValidatorConfig().basis_block("floor_height_m", "window_sill_m")
    for check in decided:
        assert check.trace["basis"] == expected
        assert check.geometry_evidence["basis"] == expected


# --------------------------------------------------------------------------- #
# (e) §271 — ZL pair separations
# --------------------------------------------------------------------------- #
def _zl_pair(gap_m: float):
    # Low (1-kond.) ZL IV pair — §271 brackets do not depend on height.
    return masterplan(
        [
            building("ZL-1", 10, 10, 20, 10, 1),
            building("ZL-2", 10, 20 + gap_m, 20, 10, 1),
        ]
    )


def test_ppoz271_zl_pair_at_6m_fails(registry) -> None:
    ctx = build_context(_zl_pair(6.0), parcel_square())
    checks = checks_for(check_fire_separation(ctx, registry), RULE_PPOZ_271)
    fails = [c for c in checks if c.status is RuleStatus.FAIL]
    assert len(fails) == 1
    assert "ZL-1" in fails[0].message and "ZL-2" in fails[0].message
    assert fails[0].geometry_evidence["separation_distance_m"] == pytest.approx(6.0)


def test_ppoz271_zl_pair_at_8m_passes(registry) -> None:
    ctx = build_context(_zl_pair(8.0), parcel_square())
    checks = checks_for(check_fire_separation(ctx, registry), RULE_PPOZ_271)
    assert checks
    assert all(c.status is RuleStatus.PASS for c in checks)


def test_ppoz272_single_building_boundary_distance_still_checked(registry) -> None:
    # No pair at all → §272 (half distance to the unbuilt boundary, ZL → 4 m)
    # still decides; 3 m from the boundary fails. Never a silent skip.
    proposal = masterplan([building("Solo", 3, 40, 20, 10, 1)])
    ctx = build_context(proposal, parcel_square())
    checks = checks_for(check_fire_separation(ctx, registry), RULE_PPOZ_271)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.FAIL
    entry = next(
        e
        for e in checks[0].trace["checks"]
        if e.get("name") == "odleglosc-od-granicy-niezabudowanej"
    )
    assert entry["status"] == "fail"
    assert entry["input_value"] == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# (l) thresholds are REGISTRY-sourced: editing the YAML flips the outcome
# --------------------------------------------------------------------------- #
def test_ppoz271_base_distance_comes_from_the_ruleset(registry, tmp_path: Path) -> None:
    pair = _zl_pair(10.0)
    parcel = parcel_square()

    # Default corpus: 10 m ≥ 8 m → pass.
    ctx = build_context(pair, parcel)
    base = checks_for(check_fire_separation(ctx, registry), RULE_PPOZ_271)
    pair_checks = [c for c in base if "ZL-2" in c.message and "ZL-1" in c.message]
    assert all(c.status is RuleStatus.PASS for c in pair_checks)

    # Copy the corpus and raise the §271 ZL base bracket 8.0 → 12.0.
    rules_dir = tmp_path / "PL"
    shutil.copytree("rulesets/PL", rules_dir)
    yaml_path = rules_dir / "building-technical" / "ppoz-271-273-fire-separation.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    assert text.count("value: 8.0") == 1, "the base ZL bracket appears exactly once"
    yaml_path.write_text(text.replace("value: 8.0", "value: 12.0"), encoding="utf-8")

    modified = load_rulesets(rules_dir)
    ctx2 = build_context(pair, parcel)
    flipped = checks_for(check_fire_separation(ctx2, modified), RULE_PPOZ_271)
    pair_checks2 = [c for c in flipped if "ZL-2" in c.message and "ZL-1" in c.message]
    assert pair_checks2
    assert all(c.status is RuleStatus.FAIL for c in pair_checks2), (
        "the same 10 m pair must FAIL once the YAML demands 12 m — the validator "
        "reads the registry, not a literal"
    )


def test_missing_ruleset_is_never_a_silent_pass(tmp_path: Path) -> None:
    # An EMPTY registry → every validator reports the rule as unverifiable
    # (conservative mode → warning/potential blocker), never pass.
    empty = load_rulesets(tmp_path)
    ctx = build_context(_zl_pair(6.0), parcel_square())
    checks = check_fire_separation(ctx, empty)
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.WARNING
    assert "ruleset not loaded" in checks[0].message
