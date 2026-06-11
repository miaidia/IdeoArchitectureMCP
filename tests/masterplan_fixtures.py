"""Shared Phase 11 masterplan/architect-workflow fixtures (part A + part B).

The centrepiece is :func:`riverside_parcel` — the irregular, concave, ~5 ha
"elektrownia-like" riverside polygon the Phase 11 acceptance demo builds on (plan
§11.3): water along the south boundary, railway along the north, public road on the
west, two heritage footprints inside. Everything lives in the EPSG:2180-plausible
metric frame anchored at ``(X0, Y0)`` (reused from ``tests.wt_fixtures`` so the DSL
ingest guard accepts the coordinates).

Also provides staged-masterplan payload builders for the etapowanie tests
(`staged_masterplan_payload`) reused by part B's loop tests.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import LineString, Polygon, mapping
from tests.wt_fixtures import X0, Y0, gj_rect

# --------------------------------------------------------------------------- #
# Parcels (robustness set for the skeleton/medial-axis tests)
# --------------------------------------------------------------------------- #
#: Base outline (local metres) of the riverside parcel; scaled to ~5 ha below.
_RIVERSIDE_OUTLINE: list[tuple[float, float]] = [
    (0, 0), (120, -15), (260, 5), (310, 60), (280, 140),
    (220, 160), (180, 120), (120, 150), (60, 135), (20, 80),
]
_RIVERSIDE_SCALE = 1.13  # area 39 325 m² × 1.13² ≈ 50 210 m² ≈ 5.0 ha


def riverside_parcel() -> Polygon:
    """Irregular concave ~5 ha riverside parcel (plan §11.3 golden fixture)."""
    return Polygon(
        [(X0 + x * _RIVERSIDE_SCALE, Y0 + y * _RIVERSIDE_SCALE) for x, y in _RIVERSIDE_OUTLINE]
    )


def concave_l_parcel() -> Polygon:
    """Concave L-shaped parcel (skeleton robustness case)."""
    return Polygon(
        [
            (X0, Y0), (X0 + 100, Y0), (X0 + 100, Y0 + 40),
            (X0 + 40, Y0 + 40), (X0 + 40, Y0 + 120), (X0, Y0 + 120),
        ]
    )


def collinear_parcel() -> Polygon:
    """Rectangle with redundant collinear vertices (crashes a naive skeleton)."""
    return Polygon(
        [
            (X0, Y0), (X0 + 50, Y0), (X0 + 100, Y0),  # collinear south edge
            (X0 + 100, Y0 + 50), (X0 + 100, Y0 + 100),  # collinear east edge
            (X0, Y0 + 100),
        ]
    )


def sliver_parcel() -> Polygon:
    """Near-degenerate sliver (200 m long, ~1.5 m wide)."""
    return Polygon(
        [(X0, Y0), (X0 + 200, Y0 + 0.5), (X0 + 200, Y0 + 2.0), (X0, Y0 + 1.0)]
    )


def skeleton_robustness_parcels() -> list[Polygon]:
    """The property-ish fixture set the axes extraction must never crash on."""
    return [riverside_parcel(), concave_l_parcel(), collinear_parcel(), sliver_parcel()]


# --------------------------------------------------------------------------- #
# Context edges / heritage / indicators for the riverside fixture
# --------------------------------------------------------------------------- #
def _scaled(x: float, y: float) -> tuple[float, float]:
    return (X0 + x * _RIVERSIDE_SCALE, Y0 + y * _RIVERSIDE_SCALE)


def riverside_context_edges() -> list[dict[str, Any]]:
    """Water along the south boundary, railway along the north, road on the west.

    Each line runs ~5 m outside the parcel boundary, well within the brief's
    default 10 m context-matching tolerance.
    """
    water = LineString([_scaled(-5, -6), _scaled(120, -21), _scaled(260, -1), _scaled(315, 56)])
    railway = LineString([_scaled(315, 68), _scaled(286, 147), _scaled(222, 168)])
    road = LineString([_scaled(-6, 0), _scaled(14, 81), _scaled(55, 141)])
    return [
        {"geometry": dict(mapping(water)), "kind": "woda"},
        {"geometry": dict(mapping(railway)), "kind": "kolej"},
        {"geometry": dict(mapping(road)), "kind": "droga_publiczna"},
    ]


def riverside_heritage_footprints() -> list[dict[str, Any]]:
    """Two heritage footprints inside the parcel (retained zabytki, plan §11.3)."""
    hala = Polygon([_scaled(90, 40), _scaled(130, 40), _scaled(130, 70), _scaled(90, 70)])
    komin = Polygon([_scaled(150, 60), _scaled(158, 60), _scaled(158, 68), _scaled(150, 68)])
    return [
        {"type": "Feature", "properties": {"name": "Hala elektrowni"},
         "geometry": dict(mapping(hala))},
        {"type": "Feature", "properties": {"name": "Komin"},
         "geometry": dict(mapping(komin))},
    ]


def riverside_indicators() -> dict[str, Any]:
    """Synthetic-but-realistic MPZP indicators for the riverside fixture."""
    return {
        "max_intensity": 1.8,
        "max_height_m": 25.0,
        "max_kondygnacje": 7,
        "max_coverage_ratio": 0.45,
        "min_pbc_ratio": 0.25,
        "parking_per_mieszkanie": 1.2,
        "parking_per_100m2_uslug": 2.0,
        "zabudowa_srodmiejska": False,
    }


# --------------------------------------------------------------------------- #
# Staged masterplan payloads (etapowanie tests; reused by part B)
# --------------------------------------------------------------------------- #
def staged_building(name: str, stage: int, x: float, y: float, *,
                    w: float = 60.0, h: float = 15.0, floors: int = 5) -> dict[str, Any]:
    return {
        "name": name,
        "stage": stage,
        "segments": [{"polygon": gj_rect(x, y, w, h), "floors": floors, "use": "mieszkalny"}],
    }


def staged_masterplan_payload(
    *,
    parking_stage: int | None = 1,
    playground_stage: int | None = None,
    with_road: bool = True,
    parking_spaces: int = 250,
) -> dict[str, Any]:
    """Three-stage masterplan on a 300×200 m parcel (see ``staging_parcel``).

    Defaults form a COMPLIANT staging (road from the start, parking in stage 1,
    unstaged playground = available from the first stage). The knobs create the
    violation cases: ``parking_stage=2`` (hall later than the stage-1 building it
    serves), ``playground_stage=3`` (playground arriving after the cumulative
    mieszkania trigger), ``with_road=False`` (no road access).
    """
    playground: dict[str, Any] = gj_rect(200, 10, 20, 20)
    if playground_stage is not None:
        playground = {
            "type": "Feature",
            "properties": {"stage": playground_stage},
            "geometry": playground,
        }
    payload: dict[str, Any] = {
        "schema_version": 2,
        "buildings": [
            staged_building("B1", 1, 10, 10),
            staged_building("B2", 2, 10, 60),
            staged_building("B3", 3, 10, 110),
        ],
        "roads": [],
        "parking": [
            {
                "kind": "hala_podziemna",
                "polygon": gj_rect(100, 10, 60, 60),
                "spaces": parking_spaces,
                "serves_buildings": ["B1", "B2", "B3"],
                **({"stage": parking_stage} if parking_stage is not None else {}),
            }
        ],
        "playgrounds": [playground],
    }
    if with_road:
        payload["roads"] = [
            {
                "centerline": {
                    "type": "LineString",
                    "coordinates": [[X0 + 5, Y0 + 5], [X0 + 5, Y0 + 190]],
                },
                "width_m": 6.0,
                "function": "kdw",
            }
        ]
    return payload


def staging_parcel() -> Polygon:
    """The 300×200 m parcel the staged masterplan payloads sit on."""
    return Polygon(
        [(X0, Y0), (X0 + 300, Y0), (X0 + 300, Y0 + 200), (X0, Y0 + 200)]
    )


# --------------------------------------------------------------------------- #
# Phase 11 part B — E2E koncepcja fixtures (plan §11.3 acceptance demo)
# --------------------------------------------------------------------------- #
def riverside_e2e_indicators() -> dict[str, Any]:
    """Synthetic MPZP indicators for the E2E demo (plan §11.3).

    Differs from :func:`riverside_indicators` where the acceptance scenario pins
    values: max_kondygnacje 6 and a max_intensity calibrated so the BASE capacity
    scenario PUM target (~38 660 m²) is reachable by the hand-written compliant
    masterplan below within ±10%.
    """
    return {
        **riverside_indicators(),
        "max_kondygnacje": 6,
        "max_intensity": 1.1,
    }


def _line(coords: list[tuple[float, float]]) -> dict[str, Any]:
    return {
        "type": "LineString",
        "coordinates": [[X0 + x, Y0 + y] for x, y in coords],
    }


def _road(coords: list[tuple[float, float]], width: float, function: str) -> dict[str, Any]:
    return {"centerline": _line(coords), "width_m": width, "function": function}


def _bldg(
    name: str, x: float, y: float, w: float, h: float, floors: int, stage: int,
    *, use: str = "mieszkalny", ground_floor_use: str | None = None,
    status: str = "projektowany",
) -> dict[str, Any]:
    segment: dict[str, Any] = {"polygon": gj_rect(x, y, w, h), "floors": floors, "use": use}
    if ground_floor_use is not None:
        segment["ground_floor_use"] = ground_floor_use
    return {"name": name, "stage": stage, "status": status, "segments": [segment]}


def riverside_masterplan_payload(*, flawed: bool) -> dict[str, Any]:
    """Hand-written riverside masterplan for the E2E demo (plan §11.3, §11.4).

    BOTH variants (the model's "iteration 1" and "iteration 2") are HAND-WRITTEN
    fixture data — no optimizer generates them (anti-pattern guard §11.4); the
    coordinates were verified against the Phase 10 validators while authoring.

    Layout (local metres on the ~5 ha riverside parcel; 2 heritage zabytki kept,
    a 4-element internal road loop, 8 dojście stubs, 2 stages, 2 underground
    parking halls, 1 playground inside the eastern green, ~13 000 m² greenery):

    * Row A (y 8–24): A1 90×16, A2 110×16 (usługi w parterze) — stage 1;
    * Row B (y 64–80): B1 52×16, B2 110×16 — stage 1; heritage Hala + Komin;
    * Row C (y 108–124): C1 90×16, C2 95×16 — stage 2.

    ``flawed=True`` is the deliberately violating iteration 1: B1 is shifted to
    6 m from A1 (→ §13 przesłanianie FAIL + §271 ppoż FAIL for the pair) and all
    NEW buildings drop to 4 kondygnacje (→ PUM far below the base-scenario
    target — the critique must state the capacity gap, §11.4). ``flawed=False``
    is iteration 2 "after the critique": pair separated again (40 m), 6 floors.
    """
    floors = 4 if flawed else 6
    b1_y = 30.0 if flawed else 64.0
    buildings = [
        _bldg("A1", 30, 8, 90, 16, floors, 1),
        _bldg("A2", 145, 8, 110, 16, floors, 1, ground_floor_use="uslugowy"),
        _bldg("B1", 30, b1_y, 52, 16, floors, 1),
        _bldg("B2", 195, 64, 110, 16, floors, 1),
        _bldg("C1", 60, 108, 90, 16, floors, 2),
        _bldg("C2", 175, 108, 95, 16, floors, 2),
        # Heritage retained (plan §11.3): footprints == riverside_heritage_footprints.
        _bldg("Hala elektrowni", 101.7, 45.2, 45.2, 33.9, 1, 1,
              use="uslugowy", status="zabytek_do_remontu"),
        _bldg("Komin", 169.5, 67.8, 9.04, 9.04, 1, 1,
              use="techniczny", status="zabytek_do_remontu"),
    ]
    roads = [
        # Internal loop (kdw ≥4 m → qualifies as droga pożarowa candidates) whose
        # corridors run 5–15 m from each row's longer facade.
        _road([(20, 39), (315, 39)], 6.0, "kdw"),
        _road([(20, 95), (315, 95)], 6.0, "kdw"),
        _road([(20, 39), (20, 95)], 6.0, "kdw"),
        _road([(315, 39), (315, 95)], 6.0, "kdw"),
        # Dojście stubs: every building touches the road system within the staging
        # road-access tolerance (5 m) without breaking the fire-road 5–15 m band.
        _road([(75, 25), (75, 36)], 2.0, "dojscie"),
        _road([(200, 25), (200, 36)], 2.0, "dojscie"),
        _road([(59, 81), (59, 92)], 2.0, "dojscie"),
        _road([(250, 81), (250, 92)], 2.0, "dojscie"),
        _road([(105, 98), (105, 107)], 2.0, "dojscie"),
        _road([(220, 98), (220, 107)], 2.0, "dojscie"),
        _road([(174, 77.5), (174, 92)], 2.0, "dojscie"),
    ]
    parking = [
        {  # hala garażowa pod dziedzińcem (G5) — stage 1
            "kind": "hala_podziemna",
            "polygon": gj_rect(155, 44, 150, 18),
            "spaces": 600,
            "serves_buildings": ["A1", "A2", "B1", "B2"],
            "stage": 1,
        },
        {  # north hall under G1 — stage 2
            "kind": "hala_podziemna",
            "polygon": gj_rect(60, 128, 80, 20),
            "spaces": 350,
            "serves_buildings": ["C1", "C2"],
            "stage": 2,
        },
    ]
    greenery = [
        gj_rect(70, 128, 70, 22),     # G1 north-west
        gj_rect(230, 126, 80, 28),    # G2 east (hosts the playground)
        gj_rect(25, 81.5, 280, 10),   # G3 between row B and the north road
        gj_rect(25, 42, 70, 18),      # G4 west courtyard
        gj_rect(152, 42, 156, 20),    # G5 east courtyard (over the parking hall)
        gj_rect(25, -2, 110, 8),      # G6 south strip (riverside)
        gj_rect(150, 0, 100, 6),      # G7 south-east strip
        gj_rect(322, 70, 12, 35),     # G8 east edge
        gj_rect(150, 130, 45, 8),     # G9 north-centre
    ]
    return {
        "schema_version": 2,
        "buildings": buildings,
        "roads": roads,
        "parking": parking,
        "greenery_polygons": greenery,
        "playgrounds": [gj_rect(276, 136, 22, 10)],  # ≥10 m from windows/roads/boundary
        "zabudowa_srodmiejska": False,
    }
