"""Phase 10 goldens — §60 nasłonecznienie (pvlib + 2.5D shadow engine).

Plan §10.3 cases: (b) a south-facing flat passes / a north-facing (courtyard-
bottom) flat fails; (g) śródmiejska halves the required hours (3 h → 1.5 h).
The partial-run fixture exploits the equinox property that a prism shadow's
northward extent is CONSTANT over the day (≈ H/tan(noon elevation) ≈ 21.3 m for
H = 16.5 m at Warsaw) while its east-west drift sweeps a finite obstructor's
shadow across the window — yielding a ~2.5 h morning run for a south window
20 m behind a 40 m-wide 5-kond. slab.
"""

from __future__ import annotations

import math

import pytest
from plot_planning.wt_validators import (
    RULE_WT60,
    ValidatorConfig,
    build_context,
    check_naslonecznienie,
    shadow_polygon,
)
from plot_planning.wt_validators.sun import longest_run_hours
from plot_rules import RuleStatus, load_rulesets
from shapely.geometry import Point, Polygon
from tests.wt_fixtures import NORTH, SOUTH, building, masterplan, parcel_square


@pytest.fixture(scope="module")
def registry():
    return load_rulesets("rulesets/PL")


# --------------------------------------------------------------------------- #
# Shadow formula unit sanity
# --------------------------------------------------------------------------- #
def test_shadow_polygon_formula() -> None:
    # elevation 45° → shadow length == height; azimuth 180° (sun due south) →
    # the shadow extends due NORTH by exactly H.
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    shadow = shadow_polygon(square, 12.0, 45.0, 180.0)
    minx, miny, maxx, maxy = shadow.bounds
    assert miny == pytest.approx(0.0)
    assert maxy == pytest.approx(10.0 + 12.0)
    assert minx == pytest.approx(0.0, abs=1e-9)
    assert maxx == pytest.approx(10.0, abs=1e-9)
    # The point just north of the footprint is covered; far north is not.
    assert shadow.covers(Point(5, 15))
    assert not shadow.covers(Point(5, 23))
    # elevation 30° → length = H/tan(30°) = H·√3.
    shadow2 = shadow_polygon(square, 12.0, 30.0, 180.0)
    assert shadow2.bounds[3] == pytest.approx(10.0 + 12.0 * math.sqrt(3.0))


def test_longest_run_is_continuous_and_conservative() -> None:
    # k consecutive sunlit samples span (k−1)·step — interval semantics.
    assert longest_run_hours([True] * 5, 15) == pytest.approx(1.0)
    # A gap breaks the run: 3h of scattered sun is NOT 3h continuous.
    flags = [True, True, False] * 8
    assert longest_run_hours(flags, 15) == pytest.approx(0.25)
    assert longest_run_hours([False] * 8, 15) == 0.0


# --------------------------------------------------------------------------- #
# (b) south-facing flat passes / north-facing flat fails
# --------------------------------------------------------------------------- #
def test_wt60_south_facing_flat_passes(registry) -> None:
    proposal = masterplan(
        [building("Poludniowy", 60, 60, 30, 12, 5, windowed_walls=[SOUTH])]
    )
    ctx = build_context(proposal, parcel_square())
    checks = [c for c in check_naslonecznienie(ctx, registry) if c.rule_id == RULE_WT60]
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.PASS
    # Open southern horizon at równonoc → far above the 3 h requirement.
    assert checks[0].geometry_evidence["insolation_hours_equinox"] >= 3.0


def test_wt60_north_facing_flat_fails(registry) -> None:
    # All windows on the NORTH wall (courtyard-bottom worst case): the sun never
    # enters the wall's facing half-plane at równonoc → 0 h → every window of
    # the segment fails → provably non-compliant (ust. 2 cannot save it).
    proposal = masterplan(
        [building("Polnocny", 60, 60, 30, 12, 5, windowed_walls=[NORTH])]
    )
    ctx = build_context(proposal, parcel_square())
    checks = [c for c in check_naslonecznienie(ctx, registry) if c.rule_id == RULE_WT60]
    assert len(checks) == 1
    assert checks[0].status is RuleStatus.FAIL
    assert checks[0].geometry_evidence["insolation_hours_equinox"] == pytest.approx(0.0)
    assert "Polnocny" in checks[0].message


def test_wt60_mixed_segment_is_undecidable_not_fail(registry) -> None:
    # Conservative all-windowed default: the north wall fails but the segment
    # has compliant windows — §60 ust. 2 (one room per dwelling) depends on the
    # unknown dwelling layout → WARNING (potential blocker), never silent pass
    # and never an over-claimed FAIL.
    proposal = masterplan([building("Mieszany", 60, 60, 30, 12, 5)])
    ctx = build_context(proposal, parcel_square())
    checks = [c for c in check_naslonecznienie(ctx, registry) if c.rule_id == RULE_WT60]
    assert checks
    assert any(c.status is RuleStatus.WARNING for c in checks)
    assert all(c.status is not RuleStatus.FAIL for c in checks)
    warning = next(c for c in checks if c.status is RuleStatus.WARNING)
    assert warning.trace["ust2_aggregation"]["segment_index"] == 0


def test_wt60_checks_carry_heuristic_basis(registry) -> None:
    # m6: §60 shadow heights derive from the floor_height_m heuristic (3.3 m) —
    # every §60 output must carry that basis entry in trace AND evidence.
    proposal = masterplan(
        [building("Poludniowy", 60, 60, 30, 12, 5, windowed_walls=[SOUTH])]
    )
    ctx = build_context(proposal, parcel_square())
    checks = [c for c in check_naslonecznienie(ctx, registry) if c.rule_id == RULE_WT60]
    assert checks
    expected = ValidatorConfig().basis_block("floor_height_m")
    for check in checks:
        assert check.trace["basis"] == expected
        assert check.geometry_evidence["basis"] == expected


def test_wt60_existing_buildings_not_charged(registry) -> None:
    # Pre-existing north-facing deficit is not attributable to the investment.
    proposal = masterplan(
        [building("Stary", 60, 60, 30, 12, 5, windowed_walls=[NORTH], status="istniejacy")]
    )
    ctx = build_context(proposal, parcel_square())
    assert check_naslonecznienie(ctx, registry) == []


# --------------------------------------------------------------------------- #
# (g) śródmiejska halves the required hours: ~2.5 h run fails 3 h, passes 1.5 h
# --------------------------------------------------------------------------- #
def _partial_sun_fixture(*, srodmiejska: bool):
    return masterplan(
        [
            # 40 m-wide 5-kond. slab (windowless usługi — no §60 of its own).
            building(
                "Blok poludniowy", 10, 10, 40, 14, 5,
                use="uslugowy", declare_windowless=True,
            ),
            # Narrow (10 m) flat 20 m behind it, windows ONLY on the south wall
            # (one sampled window axis at the slab's centerline).
            building("Blok polnocny", 25, 44, 10, 14, 5, windowed_walls=[SOUTH]),
        ],
        zabudowa_srodmiejska=srodmiejska,
    )


@pytest.mark.parametrize(
    ("srodmiejska", "expected"), [(False, RuleStatus.FAIL), (True, RuleStatus.PASS)]
)
def test_wt60_srodmiejska_halves_required_hours(
    registry, srodmiejska: bool, expected: RuleStatus
) -> None:
    ctx = build_context(_partial_sun_fixture(srodmiejska=srodmiejska), parcel_square())
    checks = [
        c
        for c in check_naslonecznienie(ctx, registry)
        if c.rule_id == RULE_WT60 and "Blok polnocny" in c.message
    ]
    assert len(checks) == 1
    check = checks[0]
    assert check.status is expected
    hours = check.geometry_evidence["insolation_hours_equinox"]
    # The geometric run is identical in both regimes — only the threshold halves.
    assert 1.5 <= hours < 3.0, f"fixture must yield a partial run, got {hours}"
    if srodmiejska:
        modifiers = next(
            e
            for e in check.trace["checks"]
            if e.get("name") == "pokoj-mieszkalny-3h-rownonoc"
        )["modifiers"]
        assert any(m.get("multiply") == 0.5 for m in modifiers)
