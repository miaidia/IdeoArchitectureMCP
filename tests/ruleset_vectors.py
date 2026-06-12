"""Golden input/output vectors for EVERY rule in ``rulesets/PL`` (F-0559).

The Phase 16 regression runner (``tests/test_ruleset_regression.py``) asserts:

* every rule id in the loaded registry has ≥1 vector here — adding a NEW rule
  without a test vector FAILS the suite (the §18.3 "rulesets versioned, tested"
  gate is enforced, not aspirational);
* every vector evaluates to its expected status through the real engine.

Vectors for rules with machine-evaluable ``checks`` carry a decided pass AND a
fail (or not_applicable) case; declaratively-empty rules (typology priors, the
F-0128 planning examples kept for the loader, the retired setback exemplar)
expect the engine's honest no-silent-pass outcome: ``unknown`` for soft rules,
``warning`` for hard rules in the default conservative mode.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Vector(TypedDict, total=False):
    inputs: dict[str, Any]
    expected: str  # pass | fail | warning | unknown | not_applicable
    mode: str  # evaluation mode; default "conservative"
    note: str


def _fire(separation: float, **extra: Any) -> dict[str, Any]:
    return {
        "separation_distance_m": separation,
        "max_q_mj_m2": 0.0,  # ZL<->ZL pair
        "distance_to_unbuilt_boundary_m": 10.0,
        "neighbor_assumed_q_mj_m2": 0.0,
        **extra,
    }


def _fire_road(**extra: Any) -> dict[str, Any]:
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


def _playground(n: int, area: float, **extra: Any) -> dict[str, Any]:
    return {
        "is_multifamily": True,
        "total_mieszkania": n,
        "playground_area_m2": area,
        "playground_pbc_share": 0.35,
        "playground_insolation_hours_equinox": 2.5,
        "playground_min_distance_m": 12.0,
        **extra,
    }


#: rule_id -> golden vectors. EVERY registry rule must appear here (runner-enforced).
VECTORS: dict[str, list[Vector]] = {
    # ------------------------------------------------------------------ #
    # WT / ppoż corpus (Phase 8/10) — decided pass + fail pairs
    # ------------------------------------------------------------------ #
    "PL-WT-12-SETBACKS-001": [
        {
            "inputs": {
                "wall_has_windows_or_doors": True,
                "is_multifamily_over_4_storeys": False,
                "distance_to_boundary_m": 4.0,
            },
            "expected": "pass",
        },
        {
            "inputs": {
                "wall_has_windows_or_doors": True,
                "is_multifamily_over_4_storeys": True,
                "distance_to_boundary_m": 4.0,
            },
            "expected": "fail",
            "note": "wielorodzinny >4 kondygnacje needs 5 m",
        },
    ],
    "PL-WT-13-PRZESLANIANIE-001": [
        {
            "inputs": {
                "obstruction_distance_m": 20.0,
                "obstruction_height_m": 21.0,
                "obstruction_width_m": 12.0,
                "wysokosc_przeslaniania_m": 20.0,
            },
            "expected": "pass",
        },
        {
            "inputs": {
                "obstruction_distance_m": 19.0,
                "obstruction_height_m": 21.0,
                "obstruction_width_m": 12.0,
                "wysokosc_przeslaniania_m": 20.0,
            },
            "expected": "fail",
        },
        {
            "inputs": {
                "obstruction_distance_m": 10.5,
                "obstruction_height_m": 21.0,
                "obstruction_width_m": 12.0,
                "wysokosc_przeslaniania_m": 20.0,
                "zabudowa_srodmiejska": True,
            },
            "expected": "pass",
            "note": "ust. 4 śródmiejska halving",
        },
    ],
    "PL-WT-19-PARKING-DISTANCES-001": [
        {
            "inputs": {
                "parking_spaces": 25,
                "distance_to_windows_m": 10.0,
                "distance_to_boundary_m": 6.0,
            },
            "expected": "pass",
        },
        {
            "inputs": {
                "parking_spaces": 25,
                "distance_to_windows_m": 8.0,
                "distance_to_boundary_m": 6.0,
            },
            "expected": "fail",
        },
    ],
    "PL-WT-21-PARKING-DIMENSIONS-001": [
        {
            "inputs": {"stall_width_m": 2.5, "stall_length_m": 5.0},
            "expected": "pass",
        },
        {
            "inputs": {"stall_type": "disabled", "stall_width_m": 3.0, "stall_length_m": 5.0},
            "expected": "fail",
        },
    ],
    "PL-WT-39-PBC-001": [
        {
            "inputs": {"building_type": "wielorodzinny", "pbc_ratio": 0.25},
            "expected": "pass",
        },
        {
            "inputs": {"building_type": "wielorodzinny", "pbc_ratio": 0.20},
            "expected": "fail",
        },
        {
            "inputs": {
                "building_type": "wielorodzinny",
                "pbc_ratio": 0.18,
                "mpzp_pbc_known": True,
                "mpzp_min_pbc_ratio": 0.15,
            },
            "expected": "pass",
            "note": "MPZP indicator replaces the statutory 25%",
        },
    ],
    "PL-WT-40-PLAC-ZABAW-001": [
        {"inputs": _playground(30, 30.0), "expected": "pass"},
        {"inputs": _playground(30, 29.0), "expected": "fail"},
        {"inputs": {"is_multifamily": False}, "expected": "not_applicable"},
    ],
    "PL-WT-60-NASLONECZNIENIE-001": [
        {
            "inputs": {"room_type": "pokoj_mieszkalny", "insolation_hours_equinox": 3.0},
            "expected": "pass",
        },
        {
            "inputs": {"room_type": "pokoj_mieszkalny", "insolation_hours_equinox": 2.0},
            "expected": "fail",
        },
        {
            "inputs": {
                "room_type": "pokoj_mieszkalny",
                "insolation_hours_equinox": 0.0,
                "zabudowa_srodmiejska": True,
                "is_single_room_dwelling": True,
            },
            "expected": "pass",
            "note": "ust. 3 in fine exemption",
        },
    ],
    "PL-PPOZ-271-273-FIRE-SEPARATION-001": [
        {"inputs": _fire(8.0), "expected": "pass"},
        {"inputs": _fire(6.0), "expected": "fail"},
        {
            "inputs": _fire(4.0, sprinklers_both=True),
            "expected": "pass",
            "note": "ust. 2: sprinklers both -> 50%",
        },
    ],
    "PL-PPOZ-DROGA-POZAROWA-001": [
        {"inputs": _fire_road(), "expected": "pass"},
        {"inputs": _fire_road(road_width_m=3.5), "expected": "fail"},
        {"inputs": {"fire_road_required": False}, "expected": "not_applicable"},
    ],
    "PL-PUM-PN-ISO-9836-001": [
        {
            "inputs": {"clear_height_m": 2.5},
            "expected": "pass",
            "note": "resolve-op: the sloped-ceiling share bracket resolves",
        },
        {
            "inputs": {},
            "expected": "unknown",
            "mode": "optimistic",
            "note": "soft rule, no height -> bracket unresolvable",
        },
    ],
    # ------------------------------------------------------------------ #
    # Planning examples (Phase 2 loader fixtures) + parking balance
    # ------------------------------------------------------------------ #
    "PL-PLAN-PARKING-MPZP-001": [
        {
            "inputs": {
                "parking_rate_per_mieszkanie": 1.2,
                "parking_requirement_known": True,
                "parking_spaces_required": 100.0,
                "parking_spaces_provided": 120.0,
            },
            "expected": "pass",
        },
        {
            "inputs": {
                "parking_rate_per_mieszkanie": 1.2,
                "parking_requirement_known": True,
                "parking_spaces_required": 100.0,
                "parking_spaces_provided": 80.0,
            },
            "expected": "fail",
        },
    ],
    "PL-PLAN-MN-COVERAGE-001": [
        {
            "inputs": {"zone_symbol": "MN", "parcel_area_m2": 1000.0},
            "expected": "warning",
            "note": "hard rule without machine checks -> conservative warning "
            "(never a silent pass)",
        },
    ],
    "PL-EIA-SCREENING-001": [
        {
            "inputs": {
                "investment_type": "mieszkaniowa",
                "investment_area_ha": 2.5,
                "in_protected_area": False,
            },
            "expected": "unknown",
            "note": "soft screening prior without machine checks stays unknown",
        },
    ],
    # Retired §12 exemplar kept for loader/version history (see
    # test_setback_exemplar_retired_in_favor_of_wt12).
    "PL-WT-SETBACK-GRANICA-001": [
        {
            "inputs": {"building_wall_type": "z_oknami"},
            "expected": "warning",
            "note": "hard rule without machine checks -> conservative warning",
        },
    ],
    # ------------------------------------------------------------------ #
    # Typology priors (Phase 11) — suggestions, never validators: the engine
    # honestly reports unknown (no machine-evaluable checks).
    # ------------------------------------------------------------------ #
    "PL-TYP-GALERIOWIEC-001": [
        {"inputs": {"shape_class": "elongated", "width_m": 60.0}, "expected": "unknown"},
    ],
    "PL-TYP-HALA-GARAZOWA-DZIEDZINIEC-001": [
        {"inputs": {"width_m": 60.0, "parking_indicator_present": True}, "expected": "unknown"},
    ],
    "PL-TYP-KLATKOWIEC-SEKCYJNY-001": [
        {"inputs": {"shape_class": "elongated", "width_m": 40.0}, "expected": "unknown"},
    ],
    "PL-TYP-KWARTAL-OBRZEZNY-001": [
        {
            "inputs": {
                "shape_class": "compact",
                "width_m": 80.0,
                "srodmiejska": True,
                "uslugi_expected": True,
            },
            "expected": "unknown",
        },
    ],
    "PL-TYP-PUNKTOWIEC-001": [
        {"inputs": {"shape_class": "compact", "width_m": 30.0}, "expected": "unknown"},
    ],
    "PL-TYP-USLUGI-W-PARTERZE-001": [
        {"inputs": {"srodmiejska": True, "uslugi_expected": True}, "expected": "unknown"},
    ],
    "PL-TYP-WIELORODZINNY-TRAKT-001": [
        {"inputs": {"shape_class": "elongated", "width_m": 50.0}, "expected": "unknown"},
    ],
}
