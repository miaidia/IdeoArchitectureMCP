"""WT/ppoż/PUM ruleset corpus golden tests (Phase 8, plan v2 §8.1.2 + §8.3).

Each YAML in rulesets/PL/building-technical (+ planning/parking-from-mpzp) is
loaded through the real loader and evaluated by the real engine against golden
inputs with hand-checked legal outcomes (values verified against the ISAP
primary texts — see each YAML's source_quote).

Also verifies the corpus-wide invariants: valid_from + Dz.U. source_reference on
every rule, śródmiejska threshold flips (§13/§60/§40), the no-national-parking
guard, and the hot-reload property (edited YAML value reflected on fresh load).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from plot_rules import EvaluationMode, RulesetRegistry, RuleStatus, evaluate, load_rulesets

REPO_ROOT = Path(__file__).resolve().parents[1]
RULESETS = REPO_ROOT / "rulesets" / "PL"

NEW_RULE_IDS = [
    "PL-WT-12-SETBACKS-001",
    "PL-WT-13-PRZESLANIANIE-001",
    "PL-WT-60-NASLONECZNIENIE-001",
    "PL-WT-19-PARKING-DISTANCES-001",
    "PL-WT-21-PARKING-DIMENSIONS-001",
    "PL-WT-39-PBC-001",
    "PL-WT-40-PLAC-ZABAW-001",
    "PL-PPOZ-271-273-FIRE-SEPARATION-001",
    "PL-PPOZ-DROGA-POZAROWA-001",
    "PL-PUM-PN-ISO-9836-001",
    "PL-PLAN-PARKING-MPZP-001",
]


@pytest.fixture(scope="module")
def registry() -> RulesetRegistry:
    return load_rulesets(RULESETS)


def rule(registry: RulesetRegistry, rule_id: str):  # noqa: ANN201 - test helper
    found = registry.get(rule_id)
    assert found is not None, f"{rule_id} not loaded"
    return found


# --------------------------------------------------------------------------- #
# Corpus invariants
# --------------------------------------------------------------------------- #
def test_corpus_loads_without_schema_errors(registry: RulesetRegistry) -> None:
    assert registry.errors == ()
    loaded = {r.id for r in registry.rules}
    assert set(NEW_RULE_IDS) <= loaded
    # The two pre-Phase-8 example rules still load (loader compatibility).
    assert "PL-WT-SETBACK-GRANICA-001" in loaded
    assert "PL-PLAN-MN-COVERAGE-001" in loaded


def test_every_rule_has_valid_from_and_dzu_source(registry: RulesetRegistry) -> None:
    for r in registry.rules:
        assert r.valid_from, f"{r.id} missing valid_from (§12.1)"
        assert r.source_reference, f"{r.id} missing source_reference (§12.1)"
    for rule_id in NEW_RULE_IDS:
        r = rule(registry, rule_id)
        assert "Dz.U." in str(r.source_reference), f"{rule_id} lacks a Dz.U. citation"
        assert r.raw.get("verification") == "isap_primary", rule_id
        assert r.raw.get("source_quote"), f"{rule_id} lacks source_quote (NFR-AUD-005)"


def test_amendment_changed_rules_valid_from_2024_08_01(registry: RulesetRegistry) -> None:
    for rule_id in ("PL-WT-12-SETBACKS-001", "PL-WT-39-PBC-001", "PL-WT-40-PLAC-ZABAW-001"):
        assert rule(registry, rule_id).valid_from == "2024-08-01"


def test_setback_exemplar_retired_in_favor_of_wt12(registry: RulesetRegistry) -> None:
    """W1 regression: the Phase 2 §12 exemplar is closed at 2024-07-31 so its
    scope does not double-report with PL-WT-12-SETBACKS-001 (valid from
    2024-08-01); the exemplar points at its successor."""
    exemplar = rule(registry, "PL-WT-SETBACK-GRANICA-001")
    assert exemplar.valid_to == "2024-07-31"
    assert "PL-WT-12-SETBACKS-001" in str(exemplar.raw.get("notes"))
    assert rule(registry, "PL-WT-12-SETBACKS-001").valid_from == "2024-08-01"


def test_sources_md_anchors_exist_for_exemplar_rules(registry: RulesetRegistry) -> None:
    """W2 regression: source_reference anchors of the exemplar rules resolve to
    real sources.md sections carrying Dz.U. citations."""
    for rule_id in ("PL-WT-SETBACK-GRANICA-001", "PL-PLAN-MN-COVERAGE-001"):
        ref = str(rule(registry, rule_id).source_reference)
        assert "sources.md#" in ref, f"{rule_id}: not a sources.md anchor reference"
        path_part, anchor = ref.split("#", 1)
        md_path = REPO_ROOT / path_part
        assert md_path.is_file(), f"{rule_id}: {path_part} does not exist"
        md = md_path.read_text(encoding="utf-8")
        assert f"## {anchor}" in md, f"{rule_id}: anchor '{anchor}' missing in {path_part}"
        assert "Dz.U." in md


# --------------------------------------------------------------------------- #
# §12 setbacks
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("windows", "over4", "distance", "expected"),
    [
        # Golden: 5-kond. wielorodzinny 4 m from boundary -> fail (needs 5 m).
        (True, True, 4.0, RuleStatus.FAIL),
        (True, True, 5.0, RuleStatus.PASS),
        (False, True, 4.0, RuleStatus.FAIL),  # 5 m applies to BOTH wall cases
        (False, True, 5.0, RuleStatus.PASS),
        # Golden: 3 m windowless wall single-family -> pass.
        (False, False, 3.0, RuleStatus.PASS),
        (False, False, 2.9, RuleStatus.FAIL),
        (True, False, 4.0, RuleStatus.PASS),
        (True, False, 3.5, RuleStatus.FAIL),
    ],
)
def test_wt12_setbacks(
    registry: RulesetRegistry,
    windows: bool,
    over4: bool,
    distance: float,
    expected: RuleStatus,
) -> None:
    check = evaluate(
        rule(registry, "PL-WT-12-SETBACKS-001"),
        {
            "wall_has_windows_or_doors": windows,
            "is_multifamily_over_4_storeys": over4,
            "distance_to_boundary_m": distance,
        },
    )
    assert check.status is expected
    assert check.severity == "hard"


def test_wt12_mpzp_reduced_setback_windowless_only(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-12-SETBACKS-001")
    # ust. 2: MPZP allows 1.5 m for windowless walls.
    check = evaluate(
        r,
        {
            "wall_has_windows_or_doors": False,
            "is_multifamily_over_4_storeys": False,
            "distance_to_boundary_m": 1.5,
            "mpzp_allows_reduced_setback": True,
        },
    )
    assert check.status is RuleStatus.PASS
    # ...but never for windowed walls (ust. 1 pkt 1/3 have no ust. 2 relaxation).
    check = evaluate(
        r,
        {
            "wall_has_windows_or_doors": True,
            "is_multifamily_over_4_storeys": False,
            "distance_to_boundary_m": 1.5,
            "mpzp_allows_reduced_setback": True,
        },
    )
    assert check.status is RuleStatus.FAIL


def test_wt12_missing_distance_never_silent_pass(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-12-SETBACKS-001")
    inputs = {"wall_has_windows_or_doors": True, "is_multifamily_over_4_storeys": True}
    assert evaluate(r, inputs, mode="optimistic").status is RuleStatus.UNKNOWN
    conservative = evaluate(r, inputs, mode="conservative")
    assert conservative.status is RuleStatus.WARNING
    assert conservative.trace["blocker_note"]
    assert evaluate(r, inputs, mode="strict").status is RuleStatus.FAIL


# --------------------------------------------------------------------------- #
# §13 przesłanianie
# --------------------------------------------------------------------------- #
def base_13(distance: float, **extra: object) -> dict[str, object]:
    return {
        "obstruction_distance_m": distance,
        "obstruction_height_m": 20.0,
        "obstruction_width_m": 14.0,
        "wysokosc_przeslaniania_m": 20.0,
        **extra,
    }


def test_wt13_height_based_distance(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-13-PRZESLANIANIE-001")
    assert evaluate(r, base_13(19.0)).status is RuleStatus.FAIL
    assert evaluate(r, base_13(20.0)).status is RuleStatus.PASS


def test_wt13_cap_35m_for_tall_obstructions(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-13-PRZESLANIANIE-001")
    tall = base_13(36.0, obstruction_height_m=80.0, wysokosc_przeslaniania_m=78.0)
    assert evaluate(r, tall).status is RuleStatus.PASS  # cap at 35 m
    closer = base_13(34.0, obstruction_height_m=80.0, wysokosc_przeslaniania_m=78.0)
    assert evaluate(r, closer).status is RuleStatus.FAIL


def test_wt13_narrow_obstruction_10m(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-13-PRZESLANIANIE-001")
    mast = base_13(10.0, obstruction_width_m=2.5, obstruction_height_m=60.0)
    assert evaluate(r, mast).status is RuleStatus.PASS
    assert evaluate(r, base_13(9.0, obstruction_width_m=2.5)).status is RuleStatus.FAIL


def test_wt13_srodmiejska_does_not_reduce_narrow_obstruction_10m(
    registry: RulesetRegistry,
) -> None:
    """B2 regression: ust. 4 halves ONLY the ust. 1 pkt 1 distances — the ust. 3
    narrow-obstruction (<=3 m) 10 m stands also in zabudowa śródmiejska."""
    r = rule(registry, "PL-WT-13-PRZESLANIANIE-001")
    mast_too_close = base_13(
        6.0, obstruction_width_m=2.5, obstruction_height_m=60.0, zabudowa_srodmiejska=True
    )
    assert evaluate(r, mast_too_close).status is RuleStatus.FAIL  # 10 m stands
    mast_at_ten = base_13(
        10.0, obstruction_width_m=2.5, obstruction_height_m=60.0, zabudowa_srodmiejska=True
    )
    assert evaluate(r, mast_at_ten).status is RuleStatus.PASS


# --------------------------------------------------------------------------- #
# §60 nasłonecznienie
# --------------------------------------------------------------------------- #
def test_wt60_dwelling_3h(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-60-NASLONECZNIENIE-001")
    ok = {"room_type": "pokoj_mieszkalny", "insolation_hours_equinox": 3.0}
    bad = {"room_type": "pokoj_mieszkalny", "insolation_hours_equinox": 2.0}
    assert evaluate(r, ok).status is RuleStatus.PASS
    assert evaluate(r, bad).status is RuleStatus.FAIL


def test_wt60_single_room_srodmiejska_exempt(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-60-NASLONECZNIENIE-001")
    check = evaluate(
        r,
        {
            "room_type": "pokoj_mieszkalny",
            "insolation_hours_equinox": 0.0,
            "zabudowa_srodmiejska": True,
            "is_single_room_dwelling": True,
        },
    )
    assert check.status is RuleStatus.PASS  # ust. 3 in fine: no minimum
    assert any(e.get("exempt") for e in check.trace["checks"])


def test_wt60_children_rooms_3h(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-60-NASLONECZNIENIE-001")
    assert (
        evaluate(r, {"room_type": "sala_dzieci", "insolation_hours_equinox": 3.0}).status
        is RuleStatus.PASS
    )
    assert (
        evaluate(r, {"room_type": "sala_dzieci", "insolation_hours_equinox": 2.5}).status
        is RuleStatus.FAIL
    )


# --------------------------------------------------------------------------- #
# §19 parking distances + §21 dimensions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("spaces", "window_d", "boundary_d", "expected"),
    [
        (10, 7.0, 3.0, RuleStatus.PASS),
        (10, 6.5, 3.0, RuleStatus.FAIL),
        (25, 8.0, 6.0, RuleStatus.FAIL),  # golden: 25 stalls 8 m from window (needs 10)
        (25, 10.0, 6.0, RuleStatus.PASS),
        (25, 10.0, 5.0, RuleStatus.FAIL),  # boundary needs 6 m for 11-60
        (61, 20.0, 16.0, RuleStatus.PASS),
        (61, 19.5, 16.0, RuleStatus.FAIL),
    ],
)
def test_wt19_brackets(
    registry: RulesetRegistry,
    spaces: int,
    window_d: float,
    boundary_d: float,
    expected: RuleStatus,
) -> None:
    check = evaluate(
        rule(registry, "PL-WT-19-PARKING-DISTANCES-001"),
        {
            "parking_spaces": spaces,
            "distance_to_windows_m": window_d,
            "distance_to_boundary_m": boundary_d,
        },
    )
    assert check.status is expected


def test_wt21_stall_dimensions(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-21-PARKING-DIMENSIONS-001")
    ok = {"stall_width_m": 2.5, "stall_length_m": 5.0}
    assert evaluate(r, ok).status is RuleStatus.PASS  # default stall_type=standard
    assert evaluate(r, {**ok, "stall_width_m": 2.4}).status is RuleStatus.FAIL
    disabled = {"stall_type": "disabled", "stall_width_m": 3.6, "stall_length_m": 5.0}
    assert evaluate(r, disabled).status is RuleStatus.PASS
    assert (
        evaluate(r, {**disabled, "stall_width_m": 3.0}).status is RuleStatus.FAIL
    )


# --------------------------------------------------------------------------- #
# §39 PBC
# --------------------------------------------------------------------------- #
def test_wt39_statutory_25_percent(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-39-PBC-001")
    base = {"building_type": "wielorodzinny", "pbc_ratio": 0.25}
    assert evaluate(r, base).status is RuleStatus.PASS
    assert evaluate(r, {**base, "pbc_ratio": 0.20}).status is RuleStatus.FAIL


def test_wt39_mpzp_overrides_statutory(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-39-PBC-001")
    check = evaluate(
        r,
        {
            "building_type": "wielorodzinny",
            "pbc_ratio": 0.18,
            "mpzp_pbc_known": True,
            "mpzp_min_pbc_ratio": 0.15,
        },
    )
    assert check.status is RuleStatus.PASS  # MPZP 15% replaces the 25% floor


def test_wt39_public_square_20_percent(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-39-PBC-001")
    base = {
        "building_type": "inne",
        "is_public_square": True,
        "square_area_m2": 1200.0,
        "square_pbc_ratio": 0.15,
    }
    assert evaluate(r, base).status is RuleStatus.FAIL  # >1000 m2 needs 20%
    assert evaluate(r, {**base, "square_pbc_ratio": 0.20}).status is RuleStatus.PASS
    small = {**base, "square_area_m2": 900.0, "square_pbc_ratio": 0.0}
    assert evaluate(r, small).status is RuleStatus.PASS  # <=1000 m2: no ust. 2 minimum


def test_wt39_other_building_not_applicable(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-39-PBC-001")
    check = evaluate(r, {"building_type": "jednorodzinny", "pbc_ratio": 0.0})
    assert check.status is RuleStatus.NOT_APPLICABLE


# --------------------------------------------------------------------------- #
# §40 plac zabaw
# --------------------------------------------------------------------------- #
def playground(n: int, area: float, **extra: object) -> dict[str, object]:
    return {
        "is_multifamily": True,
        "total_mieszkania": n,
        "playground_area_m2": area,
        "playground_pbc_share": 0.35,
        "playground_insolation_hours_equinox": 2.5,
        "playground_min_distance_m": 12.0,
        **extra,
    }


@pytest.mark.parametrize(
    ("mieszkania", "area", "expected"),
    [
        (20, 0.0, RuleStatus.PASS),  # trigger: >20 mieszkań — 20 needs nothing
        (30, 30.0, RuleStatus.PASS),  # 1 m2/mieszkanie
        (30, 29.0, RuleStatus.FAIL),
        (80, 50.0, RuleStatus.PASS),  # 51-100 -> 50 m2
        (80, 49.0, RuleStatus.FAIL),
        (120, 60.0, RuleStatus.PASS),  # 0.5 m2/mieszkanie
        (120, 59.0, RuleStatus.FAIL),
        (400, 200.0, RuleStatus.PASS),  # >300 -> 200 m2
        (400, 199.0, RuleStatus.FAIL),
    ],
)
def test_wt40_area_brackets(
    registry: RulesetRegistry, mieszkania: int, area: float, expected: RuleStatus
) -> None:
    check = evaluate(rule(registry, "PL-WT-40-PLAC-ZABAW-001"), playground(mieszkania, area))
    assert check.status is expected


def test_wt40_part_size_pbc_share_sun_and_distance(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-WT-40-PLAC-ZABAW-001")
    split = playground(120, 60.0, playground_split=True, smallest_part_area_m2=49.0)
    assert evaluate(r, split).status is RuleStatus.FAIL  # parts >=50 m2 (ust. 9)
    low_pbc = playground(120, 60.0, playground_pbc_share=0.25)
    assert evaluate(r, low_pbc).status is RuleStatus.FAIL  # >=30% on PBC (ust. 1)
    dark = playground(120, 60.0, playground_insolation_hours_equinox=1.5)
    assert evaluate(r, dark).status is RuleStatus.FAIL  # >=2 h równonoc (ust. 3)
    close = playground(120, 60.0, playground_min_distance_m=9.0)
    assert evaluate(r, close).status is RuleStatus.FAIL  # >=10 m (ust. 4)


def test_wt40_srodmiejska_floor_never_creates_an_obligation(registry: RulesetRegistry) -> None:
    """B1 regression: <=20 mieszkań → NO playground obligation (ust. 1/8); the
    śródmiejska 20 m² floor (ust. 14/15) only modifies an existing obligation —
    it must not turn the 0 m² no-obligation case into a hard FAIL."""
    r = rule(registry, "PL-WT-40-PLAC-ZABAW-001")
    few = playground(15, 0.0, zabudowa_srodmiejska=True)
    assert evaluate(r, few).status is RuleStatus.PASS
    # Where the obligation exists the floor still applies:
    # 30 mieszkań śródm. → 30 m² * 0.5 = 15, clamped to >= 20 m².
    assert (
        evaluate(r, playground(30, 20.0, zabudowa_srodmiejska=True)).status
        is RuleStatus.PASS
    )
    assert (
        evaluate(r, playground(30, 19.0, zabudowa_srodmiejska=True)).status
        is RuleStatus.FAIL
    )


def test_wt40_not_applicable_for_non_multifamily(registry: RulesetRegistry) -> None:
    check = evaluate(
        rule(registry, "PL-WT-40-PLAC-ZABAW-001"),
        {"is_multifamily": False},
    )
    assert check.status is RuleStatus.NOT_APPLICABLE


# --------------------------------------------------------------------------- #
# Śródmiejska flag flips §13/§60/§40 thresholds (plan §8.3 parameterized check)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("rule_id", "inputs"),
    [
        ("PL-WT-13-PRZESLANIANIE-001", base_13(12.0)),  # needs 20 m / śródm. 10 m
        (
            "PL-WT-60-NASLONECZNIENIE-001",
            {"room_type": "pokoj_mieszkalny", "insolation_hours_equinox": 2.0},
        ),  # needs 3 h / śródm. 1.5 h
        (
            "PL-WT-40-PLAC-ZABAW-001",
            playground(120, 35.0),  # needs 60 m2 / śródm. 30 m2 (>=20)
        ),
    ],
)
def test_srodmiejska_flips_thresholds(
    registry: RulesetRegistry, rule_id: str, inputs: dict[str, object]
) -> None:
    r = rule(registry, rule_id)
    assert evaluate(r, {**inputs, "zabudowa_srodmiejska": False}).status is RuleStatus.FAIL
    assert evaluate(r, {**inputs, "zabudowa_srodmiejska": True}).status is RuleStatus.PASS


# --------------------------------------------------------------------------- #
# Ppoż §271-273
# --------------------------------------------------------------------------- #
def fire(separation: float, **extra: object) -> dict[str, object]:
    return {
        "separation_distance_m": separation,
        "max_q_mj_m2": 0.0,  # ZL<->ZL pair
        "distance_to_unbuilt_boundary_m": 10.0,
        "neighbor_assumed_q_mj_m2": 0.0,
        **extra,
    }


def test_ppoz271_zl_zl_8m(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PPOZ-271-273-FIRE-SEPARATION-001")
    assert evaluate(r, fire(6.0)).status is RuleStatus.FAIL
    assert evaluate(r, fire(8.0)).status is RuleStatus.PASS


@pytest.mark.parametrize(
    ("q", "required"),
    [(800.0, 8.0), (2000.0, 15.0), (5000.0, 20.0)],
)
def test_ppoz271_pm_q_brackets(
    registry: RulesetRegistry, q: float, required: float
) -> None:
    r = rule(registry, "PL-PPOZ-271-273-FIRE-SEPARATION-001")
    assert evaluate(r, fire(required, max_q_mj_m2=q)).status is RuleStatus.PASS
    assert evaluate(r, fire(required - 0.5, max_q_mj_m2=q)).status is RuleStatus.FAIL


def test_ppoz271_modifiers(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PPOZ-271-273-FIRE-SEPARATION-001")
    # Fire-spreading wall on one building: 8 m * 1.5 = 12 m.
    spreading = fire(11.0, wall_or_roof_fire_spreading_one=True)
    assert evaluate(r, spreading).status is RuleStatus.FAIL
    assert evaluate(r, {**spreading, "separation_distance_m": 12.0}).status is RuleStatus.PASS
    # Sprinklers in both buildings: 8 m * 0.5 = 4 m.
    sprinklered = fire(4.0, sprinklers_both=True)
    assert evaluate(r, sprinklered).status is RuleStatus.PASS


def test_ppoz272_half_distance_to_unbuilt_boundary(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PPOZ-271-273-FIRE-SEPARATION-001")
    # ZL assumption: half of 8 m = 4 m to the unbuilt neighbor boundary.
    assert evaluate(r, fire(8.0, distance_to_unbuilt_boundary_m=3.9)).status is RuleStatus.FAIL
    assert evaluate(r, fire(8.0, distance_to_unbuilt_boundary_m=4.0)).status is RuleStatus.PASS


def test_ppoz272_boundary_distance_inherits_271_modifiers(
    registry: RulesetRegistry,
) -> None:
    """B3 regression: par. 272 ust. 1 references "odległości określonej w par.
    271 ust. 1-7" — the half-distance to the unbuilt boundary composes with the
    ust. 2-7 modifiers (multiplicatively)."""
    r = rule(registry, "PL-PPOZ-271-273-FIRE-SEPARATION-001")
    # One fire-spreading wall: boundary requires (8 * 1.5) / 2 = 6 m.
    spreading_close = fire(
        12.0, wall_or_roof_fire_spreading_one=True, distance_to_unbuilt_boundary_m=4.0
    )
    assert evaluate(r, spreading_close).status is RuleStatus.FAIL
    spreading_ok = fire(
        12.0, wall_or_roof_fire_spreading_one=True, distance_to_unbuilt_boundary_m=6.0
    )
    assert evaluate(r, spreading_ok).status is RuleStatus.PASS
    # Sprinklers in both buildings: (8 * 0.5) / 2 = 2 m suffices.
    sprinklered = fire(4.0, sprinklers_both=True, distance_to_unbuilt_boundary_m=2.0)
    assert evaluate(r, sprinklered).status is RuleStatus.PASS


def test_ppoz271_explosion_risk_20m(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PPOZ-271-273-FIRE-SEPARATION-001")
    assert evaluate(r, fire(15.0, explosion_risk=True)).status is RuleStatus.FAIL
    assert evaluate(r, fire(20.0, explosion_risk=True)).status is RuleStatus.PASS


# --------------------------------------------------------------------------- #
# Droga pożarowa
# --------------------------------------------------------------------------- #
def fire_road(**extra: object) -> dict[str, object]:
    return {
        "fire_road_required": True,
        "road_width_m": 4.0,
        "road_edge_distance_m": 7.0,
        "runs_along_longer_side": True,
        "outer_curve_radius_m": 11.0,
        "dojscie_length_m": 40.0,
        "dojscie_width_m": 1.5,
        **extra,
    }


def test_droga_pozarowa_compliant(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PPOZ-DROGA-POZAROWA-001")
    assert evaluate(r, fire_road()).status is RuleStatus.PASS


@pytest.mark.parametrize(
    "mutation",
    [
        {"road_width_m": 3.5},  # width >=4 m
        {"road_edge_distance_m": 4.0},  # edge 5-15 m
        {"road_edge_distance_m": 16.0},
        {"runs_along_longer_side": False},
        {"outer_curve_radius_m": 10.0},  # radius >=11 m
        {"dojscie_length_m": 55.0},  # dojście <=50 m
        {"dojscie_width_m": 1.2},  # dojście >=1.5 m
        {"dead_end": True, "plac_manewrowy_width_m": 18.0, "plac_manewrowy_length_m": 20.0},
        {"przejazd_present": True, "przejazd_height_m": 4.0, "przejazd_width_m": 3.6},
    ],
)
def test_droga_pozarowa_violations(
    registry: RulesetRegistry, mutation: dict[str, object]
) -> None:
    r = rule(registry, "PL-PPOZ-DROGA-POZAROWA-001")
    assert evaluate(r, fire_road(**mutation)).status is RuleStatus.FAIL


def test_droga_pozarowa_not_required_not_applicable(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PPOZ-DROGA-POZAROWA-001")
    check = evaluate(r, {"fire_road_required": False})
    assert check.status is RuleStatus.NOT_APPLICABLE


# --------------------------------------------------------------------------- #
# PUM / PN-ISO 9836 sloped-ceiling shares
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("height", "share"),
    [(2.5, 1.0), (2.2, 1.0), (2.1, 0.5), (1.4, 0.5), (1.39, 0.0), (0.8, 0.0)],
)
def test_pum_sloped_ceiling_shares(
    registry: RulesetRegistry, height: float, share: float
) -> None:
    r = rule(registry, "PL-PUM-PN-ISO-9836-001")
    check = evaluate(r, {"clear_height_m": height})
    assert check.status is RuleStatus.PASS
    assert check.trace["checks"][0]["resolved_value"] == share


def test_pum_references_undated_norm_with_2022_edition_note(
    registry: RulesetRegistry,
) -> None:
    r = rule(registry, "PL-PUM-PN-ISO-9836-001")
    assert "PN-ISO 9836" in str(r.source_reference)
    assert "2022-07" in str(r.source_reference)
    assert r.severity == "soft"


# --------------------------------------------------------------------------- #
# Parking from MPZP — the no-national-default hard guard
# --------------------------------------------------------------------------- #
def test_parking_norm_missing_is_unknown_never_default(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PLAN-PARKING-MPZP-001")
    assert r.raw["source"] == "planning_act"
    assert r.raw["default"] == "unknown"
    # No numeric national default anywhere in the rule document.
    assert "thresholds" not in r.raw
    check = evaluate(r, {}, mode=EvaluationMode.OPTIMISTIC)
    assert check.status is RuleStatus.UNKNOWN
    conservative = evaluate(r, {})
    assert conservative.status is RuleStatus.WARNING  # potential blocker
    assert conservative.trace["blocker_note"]


def test_parking_balance_with_mpzp_rate(registry: RulesetRegistry) -> None:
    r = rule(registry, "PL-PLAN-PARKING-MPZP-001")
    base = {
        "parking_rate_per_mieszkanie": 1.2,
        "parking_requirement_known": True,
        "parking_spaces_required": 144,
        "parking_spaces_provided": 150,
    }
    assert evaluate(r, base).status is RuleStatus.PASS
    assert (
        evaluate(r, {**base, "parking_spaces_provided": 120}).status is RuleStatus.FAIL
    )


# --------------------------------------------------------------------------- #
# Hot-reload property: edited YAML value reflected on fresh load (F-0133/F-0440)
# --------------------------------------------------------------------------- #
def test_hot_reload_edited_threshold_reflected(tmp_path: Path) -> None:
    workdir = tmp_path / "PL" / "building-technical"
    workdir.mkdir(parents=True)
    source = RULESETS / "building-technical" / "wt-12-setbacks.yaml"
    target = workdir / "wt-12-setbacks.yaml"
    shutil.copy(source, target)

    before = load_rulesets(tmp_path / "PL")
    r = before.get("PL-WT-12-SETBACKS-001")
    assert r is not None
    golden = {
        "wall_has_windows_or_doors": True,
        "is_multifamily_over_4_storeys": False,
        "distance_to_boundary_m": 4.5,
    }
    assert evaluate(r, golden).status is RuleStatus.PASS  # 4.5 >= 4.0

    # Edit the threshold on disk (simulating a live rule change)...
    doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    doc["thresholds"]["setback_windows_m"] = 6.0
    target.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # ...a FRESH load (what the hot-reload path does) reflects it immediately.
    after = load_rulesets(tmp_path / "PL")
    r2 = after.get("PL-WT-12-SETBACKS-001")
    assert r2 is not None
    assert evaluate(r2, golden).status is RuleStatus.FAIL  # 4.5 < 6.0 now
    assert after.ruleset_version != before.ruleset_version
