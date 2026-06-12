"""Phase 16 — masterplan golden corpus (v2 plan PHASE 16 addition 1).

Five fixtures spanning the plot classes the system must handle, each with:

* a parcel polygon (EPSG:2180-plausible metric frame, reusing the
  ``tests.wt_fixtures`` anchor so the DSL ingest guard accepts coordinates);
* synthetic-but-realistic MPZP indicators (TEST FIXTURES — not real plans);
* context edges (roads/water/rail) where the plot class calls for them;
* EXPECTED capacity **ranges** (min/max base-scenario PUM / mieszkania /
  footprint-coverage) — ranges, not exact values, so the corpus pins behaviour
  without freezing heuristics;
* a HAND-WRITTEN compliant masterplan (anti-pattern guard: no optimizer
  generates these — coordinates were verified against the Phase 10 validators
  while authoring) that passes the hard validators with zero fails, or carries
  documented ``expected_fails``.

Corpus members:

1. ``riverside_irregular``      — the ~5 ha irregular riverside exemplar
   (REUSED from :mod:`tests.masterplan_fixtures` — corpus adapter only);
2. ``narrow_infill_srodmiejska`` — ~0.15 ha, 18 m wide śródmiejska gap site
   (zabudowa pierzejowa idiom); proves the §13 halving on a real layout;
3. ``suburban_mn_1ha``          — 1 ha MN subdivision, 5 domów;
4. ``corner_mixed_use``         — ~0.5 ha corner plot, kwartał fragment,
   usługi w parterze;
5. ``multi_parcel_assembly``    — two parcels merged into a ~2 ha
   InvestmentArea (exercises ``plot_geo.merge_parcels`` + a building that
   straddles the erstwhile internal boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import plot_geo
from shapely.geometry import LineString, Polygon, mapping
from tests.masterplan_fixtures import (
    riverside_context_edges,
    riverside_e2e_indicators,
    riverside_heritage_footprints,
    riverside_masterplan_payload,
    riverside_parcel,
)
from tests.wt_fixtures import X0, Y0, gj_rect


@dataclass(frozen=True)
class CapacityRange:
    """Expected base-scenario capacity range (min/max, inclusive)."""

    pum_m2: tuple[float, float]
    mieszkania: tuple[int, int]
    max_coverage_ratio: float  # base footprint / parcel must not exceed this


@dataclass(frozen=True)
class CorpusFixture:
    """One golden corpus member (see module docstring)."""

    name: str
    description: str
    parcel: Polygon
    indicators: dict[str, Any]
    masterplan: dict[str, Any]
    expected: CapacityRange
    context_edges: list[dict[str, Any]] = field(default_factory=list)
    heritage_footprints: list[dict[str, Any]] = field(default_factory=list)
    #: rule_id -> documented reason, for fixtures that legitimately cannot reach
    #: zero fails (none today; the mechanism is part of the corpus contract).
    expected_fails: dict[str, str] = field(default_factory=dict)
    #: Statuses other than FAIL the runner additionally asserts absent for
    #: specific rules (e.g. the śródmiejska positive control).
    notes: str = ""


def _line(coords: list[tuple[float, float]]) -> dict[str, Any]:
    return {
        "type": "LineString",
        "coordinates": [[X0 + x, Y0 + y] for x, y in coords],
    }


def _road(coords: list[tuple[float, float]], width: float, function: str) -> dict[str, Any]:
    return {"centerline": _line(coords), "width_m": width, "function": function}


def _poly(coords: list[tuple[float, float]]) -> Polygon:
    return Polygon([(X0 + x, Y0 + y) for x, y in coords])


def _bldg(
    name: str,
    rect: tuple[float, float, float, float],
    floors: int,
    *,
    use: str = "mieszkalny",
    ground_floor_use: str | None = None,
    status: str = "projektowany",
    stage: int | None = None,
    windowed_walls: list[int] | None = None,
    declare_windowless: bool = False,
) -> dict[str, Any]:
    x, y, w, h = rect
    segment: dict[str, Any] = {"polygon": gj_rect(x, y, w, h), "floors": floors, "use": use}
    if ground_floor_use is not None:
        segment["ground_floor_use"] = ground_floor_use
    if declare_windowless:
        segment["windowed_walls"] = []
    elif windowed_walls is not None:
        segment["windowed_walls"] = windowed_walls
    building: dict[str, Any] = {"name": name, "status": status, "segments": [segment]}
    if stage is not None:
        building["stage"] = stage
    return building


# --------------------------------------------------------------------------- #
# 1. riverside_irregular — REUSE of the Phase 11 exemplar (corpus adapter)
# --------------------------------------------------------------------------- #
def fixture_riverside_irregular() -> CorpusFixture:
    """The ~5 ha irregular riverside plot (the user's "elektrownia-like" bar).

    Base-scenario maths (engine heuristics over the full-parcel envelope):
    footprint = min(0.55·A, 0.45·A) = 22 594 m²; PC = min(6·footprint, 1.1·A)
    = 55 231 m²; PUM = 0.7·PC ≈ 38 662 m² → ≈743 mieszkania.
    """
    return CorpusFixture(
        name="riverside_irregular",
        description="~5 ha irregular concave riverside plot; water/rail/road "
        "context; two retained heritage footprints; 2-stage masterplan.",
        parcel=riverside_parcel(),
        indicators=riverside_e2e_indicators(),
        masterplan=riverside_masterplan_payload(flawed=False),
        context_edges=riverside_context_edges(),
        heritage_footprints=riverside_heritage_footprints(),
        expected=CapacityRange(
            pum_m2=(33_000.0, 44_500.0),
            mieszkania=(630, 860),
            max_coverage_ratio=0.45,
        ),
    )


# --------------------------------------------------------------------------- #
# 2. narrow_infill_srodmiejska — 18 m wide gap site, śródmiejska halving demo
# --------------------------------------------------------------------------- #
#: Parcel: 18 × 85 m (~0.153 ha); street along the south edge.
_NARROW_PARCEL = [(0.0, 0.0), (18.0, 0.0), (18.0, 85.0), (0.0, 85.0)]


def narrow_infill_parcel() -> Polygon:
    return _poly(_NARROW_PARCEL)


def narrow_infill_indicators() -> dict[str, Any]:
    """Synthetic śródmiejska MPZP indicators (TEST FIXTURE)."""
    return {
        "max_intensity": 2.2,
        "max_kondygnacje": 4,
        "max_coverage_ratio": 0.60,
        "min_pbc_ratio": 0.25,
        "parking_per_mieszkanie": 0.5,
        "zabudowa_srodmiejska": True,
    }


def narrow_infill_masterplan(*, srodmiejska: bool = True) -> dict[str, Any]:
    """Hand-written infill: a 10×16 m front building (3 kondygnacje — below the
    12 m droga-pożarowa trigger) + the EXISTING 6-kondygnacje oficyna 12 m to
    the north across the courtyard.

    §13 przesłanianie demo (the fixture's reason to exist): the oficyna's
    wysokość przesłaniania is 6·3.3 − 1.0 = 18.8 m, so the 12 m courtyard is
    compliant ONLY under the ust. 4 śródmiejska halving (9.4 m required).
    ``srodmiejska=False`` is the corpus runner's positive control: the same
    geometry must then FAIL §13 (proves the halving bites on a real layout).
    The oficyna declares windowless walls (its dwellings face the other street
    — fixture simplification) so it creates no §60 case of its own.
    """
    return {
        "schema_version": 2,
        "buildings": [
            _bldg("Frontowy", (4, 4, 10, 16), 3),
            _bldg(
                "Oficyna",
                (4, 32, 10, 16),
                6,
                status="istniejacy",
                declare_windowless=True,
            ),
        ],
        "roads": [],
        "parking": [],
        # Backyard greenery: 14 × 32 m = 448 m² ≥ 0.25 · 1530 m² (§39 via MPZP).
        "greenery_polygons": [gj_rect(2, 50, 14, 32)],
        "playgrounds": [],
        "zabudowa_srodmiejska": srodmiejska,
    }


def fixture_narrow_infill() -> CorpusFixture:
    """Base maths: footprint = min(0.55, 0.60)·1530 = 841.5 m²; PC = min(4·841.5,
    2.2·1530) = 3 366 m²; PUM = 2 356 m² → ≈45 mieszkania."""
    return CorpusFixture(
        name="narrow_infill_srodmiejska",
        description="~0.15 ha, 18 m wide śródmiejska gap site (pierzeja idiom); "
        "§13 halving proven on a real layout.",
        parcel=narrow_infill_parcel(),
        indicators=narrow_infill_indicators(),
        masterplan=narrow_infill_masterplan(),
        context_edges=[
            {"geometry": _line([(-2, -3), (22, -3)]), "kind": "droga_publiczna"}
        ],
        expected=CapacityRange(
            pum_m2=(2_000.0, 2_750.0),
            mieszkania=(36, 55),
            max_coverage_ratio=0.60,
        ),
    )


# --------------------------------------------------------------------------- #
# 3. suburban_mn_1ha — 1 ha MN subdivision (5 domów)
# --------------------------------------------------------------------------- #
_MN_PARCEL = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]


def suburban_mn_parcel() -> Polygon:
    return _poly(_MN_PARCEL)


def suburban_mn_indicators() -> dict[str, Any]:
    """Synthetic MN indicators (TEST FIXTURE)."""
    return {
        "max_kondygnacje": 2,
        "max_height_m": 9.0,
        "max_coverage_ratio": 0.30,
        "max_intensity": 0.60,
        "min_pbc_ratio": 0.50,
        "parking_per_mieszkanie": 2.0,
        "zabudowa_srodmiejska": False,
    }


def suburban_mn_masterplan() -> dict[str, Any]:
    """Five detached 10×8 m houses (2 kondygnacje, 6.6 m — below every
    droga-pożarowa / >4-kondygnacje trigger), ≥14 m apart (≥8 m ppoż ZL–ZL,
    §13 clearance 5.6 m), ≥6 m from every boundary (§12: 4 m windowed), one
    kdw spine touching both parcel edges (no dead end), greenery ≥50% PBC."""
    houses = [
        _bldg("Dom 1", (8, 8, 10, 8), 2),
        _bldg("Dom 2", (8, 40, 10, 8), 2),
        _bldg("Dom 3", (8, 72, 10, 8), 2),
        _bldg("Dom 4", (40, 8, 10, 8), 2),
        _bldg("Dom 5", (40, 40, 10, 8), 2),
    ]
    return {
        "schema_version": 2,
        "buildings": houses,
        "roads": [
            # kdw spine: endpoints exactly on the south/north boundary so the
            # dead-end detector sees boundary connections.
            _road([(30, 0), (30, 100)], 5.0, "kdw"),
        ],
        "parking": [],
        # Eastern strip 46 × 100 = 4600 m² + two north pockets (clear of the
        # kdw corridor x∈[27.5, 32.5]) 260 + 210 m² → 5070 ≥ 0.5 · 10 000 (§39
        # via the MPZP min_pbc_ratio indicator); no overlap with buildings/road.
        "greenery_polygons": [
            gj_rect(54, 0, 46, 100),
            gj_rect(0, 90, 26, 10),
            gj_rect(34, 90, 20, 10),
        ],
        "playgrounds": [],
        "zabudowa_srodmiejska": False,
    }


def fixture_suburban_mn() -> CorpusFixture:
    """Base maths: footprint = min(0.55·10000, 0.30·10000) = 3 000 m²; PC =
    min(2·3000, 0.6·10000) = 6 000 m²; PUM = 4 200 m² (engine's multifamily
    PUM heuristic — for MN it is the upper-bound chłonność, not a unit count)."""
    return CorpusFixture(
        name="suburban_mn_1ha",
        description="1 ha suburban MN subdivision: 5 detached houses, kdw "
        "spine, half the plot green.",
        parcel=suburban_mn_parcel(),
        indicators=suburban_mn_indicators(),
        masterplan=suburban_mn_masterplan(),
        context_edges=[
            {"geometry": _line([(-3, -4), (104, -4)]), "kind": "droga_publiczna"}
        ],
        expected=CapacityRange(
            pum_m2=(3_600.0, 4_800.0),
            mieszkania=(60, 95),
            max_coverage_ratio=0.30,
        ),
    )


# --------------------------------------------------------------------------- #
# 4. corner_mixed_use — ~0.5 ha corner plot, kwartał fragment
# --------------------------------------------------------------------------- #
_CORNER_PARCEL = [(0.0, 0.0), (70.0, 0.0), (70.0, 72.0), (0.0, 72.0)]


def corner_mixed_use_parcel() -> Polygon:
    return _poly(_CORNER_PARCEL)


def corner_mixed_use_indicators() -> dict[str, Any]:
    """Synthetic mixed-use indicators (TEST FIXTURE)."""
    return {
        "max_kondygnacje": 3,
        "max_coverage_ratio": 0.45,
        "max_intensity": 1.2,
        "min_pbc_ratio": 0.25,
        "parking_per_mieszkanie": 1.0,
        "parking_per_100m2_uslug": 2.0,
        "zabudowa_srodmiejska": False,
    }


def corner_mixed_use_masterplan() -> dict[str, Any]:
    """ONE L-shaped corner building (north + west street frontages) as multiple
    segments of the SAME building (wings of one building — §13 evaluates
    same-building parts as obstructors, §271 does not apply within a building),
    usługi w parterze, 3 kondygnacje (9.9 m — below the droga-pożarowa
    trigger). Segment split near the inner corner keeps windowed walls clear of
    the re-entrant junction (real kwartał idiom: the corner bay is windowless —
    staircase/gable), so §13 passes with measured geometry.

    Courtyard: playground (§40 — the ~37 mieszkania estimate exceeds the
    trigger) fully inside the greenery (PBC share 100%), ≥10 m from every
    windowed wall and from the parcel boundary; underground hall parking
    (§19-exempt)."""
    return {
        "schema_version": 2,
        "buildings": [
            {
                "name": "Naroznik",
                "status": "projektowany",
                "segments": [
                    # North wing, windowless corner bay (x 6..30).
                    {
                        "polygon": gj_rect(6, 52.5, 24, 14),
                        "floors": 3,
                        "use": "mieszkalny",
                        "ground_floor_use": "uslugowy",
                        "windowed_walls": [],
                    },
                    # North wing, windowed part (south/courtyard + north/street).
                    {
                        "polygon": gj_rect(30, 52.5, 34, 14),
                        "floors": 3,
                        "use": "mieszkalny",
                        "ground_floor_use": "uslugowy",
                    },
                    # West wing, windowless top bay adjoining the corner (y 42..52.5).
                    {
                        "polygon": gj_rect(6, 42, 14, 10.5),
                        "floors": 3,
                        "use": "mieszkalny",
                        "ground_floor_use": "uslugowy",
                        "windowed_walls": [],
                    },
                    # West wing, windowed part (east/courtyard + west/street).
                    {
                        "polygon": gj_rect(6, 10, 14, 32),
                        "floors": 3,
                        "use": "mieszkalny",
                        "ground_floor_use": "uslugowy",
                    },
                ],
            }
        ],
        "roads": [],
        "parking": [
            {
                "kind": "hala_podziemna",
                "polygon": gj_rect(30, 6, 30, 20),
                "spaces": 60,
                "serves_buildings": ["Naroznik"],
            }
        ],
        # Courtyard greenery 36 × 34 m = 1224 m² ≥ 0.25 · 5040 = 1260? No —
        # plus the south strip 64 × 6 = 384 m² → 1608 m² total.
        "greenery_polygons": [gj_rect(32, 8, 36, 34), gj_rect(2, 2, 64, 6)],
        # Playground 12 × 8 m at the courtyard's south-east, ≥10.5 m from the
        # windowed walls (x ≥ 44 vs west-wing wall x = 20; y ≤ 30 vs north-wing
        # wall y = 52.5) and ≥10.5 m from the boundary (70/72 edges).
        "playgrounds": [gj_rect(44, 22, 12, 8)],
        "zabudowa_srodmiejska": False,
    }


def fixture_corner_mixed_use() -> CorpusFixture:
    """Base maths: footprint = min(0.55, 0.45)·5040 = 2 268 m²; PC = min(3·2268,
    1.2·5040) = 6 048 m²; PUM = 4 234 m² → ≈81 mieszkania (estimate)."""
    return CorpusFixture(
        name="corner_mixed_use",
        description="~0.5 ha corner plot; L-shaped kwartał fragment with usługi "
        "w parterze, courtyard playground, underground parking.",
        parcel=corner_mixed_use_parcel(),
        indicators=corner_mixed_use_indicators(),
        masterplan=corner_mixed_use_masterplan(),
        context_edges=[
            {"geometry": _line([(-3, 76), (74, 76)]), "kind": "droga_publiczna"},
            {"geometry": _line([(-4, -2), (-4, 76)]), "kind": "droga_publiczna"},
        ],
        expected=CapacityRange(
            pum_m2=(3_600.0, 4_900.0),
            mieszkania=(65, 98),
            max_coverage_ratio=0.45,
        ),
    )


# --------------------------------------------------------------------------- #
# 5. multi_parcel_assembly — 2 parcels merged into a ~2 ha InvestmentArea
# --------------------------------------------------------------------------- #
def assembly_parcels() -> list[Polygon]:
    """The two source parcels (100×100 m each, sharing the x=100 edge)."""
    return [
        _poly([(0, 0), (100, 0), (100, 100), (0, 100)]),
        _poly([(100, 0), (200, 0), (200, 100), (100, 100)]),
    ]


def assembly_merged_parcel() -> Polygon:
    """The merged InvestmentArea (exercises ``plot_geo.merge_parcels``)."""
    merged = plot_geo.merge_parcels(assembly_parcels())
    assert merged.geom_type == "Polygon"
    return merged  # type: ignore[return-value]


def assembly_indicators() -> dict[str, Any]:
    """Synthetic MW indicators for the assembly (TEST FIXTURE)."""
    return {
        "max_kondygnacje": 5,
        "max_coverage_ratio": 0.35,
        "max_intensity": 1.4,
        "min_pbc_ratio": 0.25,
        "parking_per_mieszkanie": 1.0,
        "zabudowa_srodmiejska": False,
    }


def assembly_masterplan() -> dict[str, Any]:
    """Two rows of 5-kondygnacje buildings (16.5 m → droga pożarowa REQUIRED:
    two full-width kdw roads in the legal 5–15 m band along the rows' longer
    facades, endpoints on the boundary — no dead ends, no corners); building
    R1 deliberately STRADDLES the erstwhile internal boundary at x=100 (the
    merge demonstration: on the merged InvestmentArea no §12 case arises
    there). Playground between the row-2 buildings, parking in two underground
    halls, PBC from the perimeter strips."""
    return {
        "schema_version": 2,
        "buildings": [
            # Row 1 (south): north facades at y=28 face the y=37 road.
            _bldg("R1", (60, 12, 80, 16), 5, stage=1),  # straddles x=100
            _bldg("R2", (160, 12, 30, 16), 5, stage=1),
            # Row 2 (north): south facades at y=64 face the y=54 road.
            _bldg("R3", (20, 64, 65, 16), 5, stage=2),
            _bldg("R4", (120, 64, 65, 16), 5, stage=2),
        ],
        "roads": [
            # Full-width kdw: centerline endpoints exactly on the boundary →
            # connectivity by boundary touch; straight → no curve-radius case.
            _road([(0, 37), (200, 37)], 6.0, "kdw"),
            _road([(0, 54), (200, 54)], 6.0, "kdw"),
        ],
        "parking": [
            {
                "kind": "hala_podziemna",
                "polygon": gj_rect(60, 12, 80, 16),
                "spaces": 220,
                "serves_buildings": ["R1", "R2"],
                "stage": 1,
            },
            {
                "kind": "hala_podziemna",
                "polygon": gj_rect(20, 64, 65, 16),
                "spaces": 220,
                "serves_buildings": ["R3", "R4"],
                "stage": 2,
            },
        ],
        # Perimeter strips (south 200×8, north 200×12), the mid band between
        # the two road corridors, and the row-2 gap green that HOSTS the
        # playground (§40 ust. 1: ≥30% of the plac on PBC — here 100%) →
        # 1600 + 2400 + 1800 + 550 = 6 350 ≥ 0.25 · 20 000 = 5 000 m².
        "greenery_polygons": [
            gj_rect(0, 0, 200, 8),
            gj_rect(0, 88, 200, 12),
            gj_rect(0, 42, 200, 9),
            gj_rect(90, 62, 25, 22),
        ],
        # Playground in the row-2 gap (x 85..120): ≥10.5 m from the windowed
        # walls (x=85 / x=120) is impossible in a 35 m gap with a 12 m piece —
        # so it sits ≥10.5 m NORTH of the y=54 road corridor (ends y=57) and
        # ≥10.5 m from R3/R4 side walls: x∈[95.5,109.5], y∈[67.5,79.5].
        "playgrounds": [gj_rect(95.5, 67.5, 14, 12)],
        "zabudowa_srodmiejska": False,
    }


def fixture_multi_parcel_assembly() -> CorpusFixture:
    """Base maths: footprint = min(0.55, 0.35)·20 000 = 7 000 m²; PC = min(5·7000,
    1.4·20 000) = 28 000 m²; PUM = 19 600 m² → ≈376 mieszkania."""
    return CorpusFixture(
        name="multi_parcel_assembly",
        description="Two 1 ha parcels merged into a ~2 ha InvestmentArea; "
        "building straddling the old boundary; 2-stage rows with fire roads.",
        parcel=assembly_merged_parcel(),
        indicators=assembly_indicators(),
        masterplan=assembly_masterplan(),
        context_edges=[
            {"geometry": _line([(-4, -4), (204, -4)]), "kind": "droga_publiczna"}
        ],
        expected=CapacityRange(
            pum_m2=(17_000.0, 22_500.0),
            mieszkania=(300, 440),
            max_coverage_ratio=0.35,
        ),
    )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def corpus_fixtures() -> list[CorpusFixture]:
    """All five corpus members, in plan order."""
    return [
        fixture_riverside_irregular(),
        fixture_narrow_infill(),
        fixture_suburban_mn(),
        fixture_corner_mixed_use(),
        fixture_multi_parcel_assembly(),
    ]


__all__ = [
    "CapacityRange",
    "CorpusFixture",
    "assembly_indicators",
    "assembly_masterplan",
    "assembly_merged_parcel",
    "assembly_parcels",
    "corner_mixed_use_indicators",
    "corner_mixed_use_masterplan",
    "corner_mixed_use_parcel",
    "corpus_fixtures",
    "fixture_corner_mixed_use",
    "fixture_multi_parcel_assembly",
    "fixture_narrow_infill",
    "fixture_riverside_irregular",
    "fixture_suburban_mn",
    "narrow_infill_indicators",
    "narrow_infill_masterplan",
    "narrow_infill_parcel",
    "suburban_mn_indicators",
    "suburban_mn_masterplan",
    "suburban_mn_parcel",
]

# silence the unused-import linter for re-exported riverside pieces
_ = (mapping, LineString)
