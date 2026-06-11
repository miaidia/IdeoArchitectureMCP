"""WT §12 — building setbacks from the parcel boundary (plan §10.1.1).

Geometric approach: per NEW building (existing buildings already stand — §12
governs *situating* a building), every external **wall plane** from the §12
decomposition (each załamanie/uskok = a separate wall, Dz.U. 2024/726) gets its
distance to the parcel boundary computed and evaluated through the
``PL-WT-12-SETBACKS-001`` rule. The 4 / 3 / 5 m brackets (windowed / windowless /
multifamily >4 kondygnacje) live ONLY in the YAML — this module supplies the
geometric inputs:

* ``distance_to_boundary_m``  — wall → the boundary stretch the wall FACES
  (its outward-normal corridor ∩ boundary, refined for oblique granice by the
  faced-edge half-plane minimum; §12 binds walls "zwrócone w stronę granicy" —
  a perpendicular wall is not charged with its corner's distance;
  see :func:`_facing_boundary_distance`; the interpretation is recorded in the
  check trace as ``distance_interpretation``);
* ``wall_has_windows_or_doors`` — from ``windowed_walls`` (``None`` → ALL
  windowed, the conservative default; evidence carries ``assumed_windowed``);
* ``is_multifamily_over_4_storeys`` — building has residential segments AND
  max kondygnacje > 4 (mieszkalno-usługowy counted as wielorodzinny —
  simplified classification, see context.RESIDENTIAL_USES);
* ``mpzp_allows_reduced_setback`` — NOT modeled here → left to the rule's
  documented conservative ``input_defaults`` (false = full distances).

Documented assumption: the WHOLE parcel boundary is treated as a granica z
sąsiednią działką budowlaną (§12 ust. 10 — boundary with a road parcel is
exempt — needs neighbor-parcel data, Phase 12); conservative until then.

Output: one FAIL RuleCheck per violating wall (evidence = the wall line), or a
single PASS summary per building citing its binding (smallest-margin) wall.
"""

from __future__ import annotations

import math

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry, RuleStatus
from plot_rules import evaluate as evaluate_rule
from shapely.geometry import LineString, Polygon, mapping
from shapely.geometry.base import BaseGeometry

from plot_planning.wt_validators.config import (
    RULE_WT12,
    check_margin,
    decided_entry,
    find_override,
    missing_rule_check,
    with_evidence,
)
from plot_planning.wt_validators.context import ValidationContext, Wall


def _wall_label(wall: Wall) -> str:
    kind = "z oknami" if wall.windowed else "bez okien"
    if wall.windowed and wall.assumed_windowed:
        kind += " (zalozenie konserwatywne)"
    return f"segment {wall.segment_index}, sciana #{wall.edge_index} ({kind})"


def _boundary_edges(boundary: BaseGeometry) -> list[LineString]:
    """The boundary decomposed into its straight edges (per granica stretch)."""
    edges: list[LineString] = []
    for line in getattr(boundary, "geoms", [boundary]):
        coords = list(line.coords)
        for a, b in zip(coords, coords[1:], strict=False):
            if a != b:
                edges.append(LineString([a, b]))
    return edges


def _facing_boundary_distance(
    wall: Wall,
    boundary: BaseGeometry,
    boundary_edges: list[LineString],
    reach_m: float,
) -> tuple[float, dict[str, float]] | None:
    """Distance from the wall to the boundary stretch the wall FACES (§12 ust. 1).

    §12 regulates a building "zwrócony ścianą ... w stronę tej granicy" — the
    distance is between a wall and the boundary IN FRONT of it. Geometrically:
    the wall segment swept outward along its normal (a perpendicular corridor of
    length ``reach_m`` — the parcel diagonal suffices) is intersected with the
    parcel boundary; a wall whose corridor meets no boundary (it faces only the
    parcel interior / courtyard) yields ``None`` — that wall is not "zwrócona w
    stronę granicy" and creates no §12 case.

    The corridor-clipped distance alone is ANTI-conservative for an oblique
    granica: the nearest point of a faced boundary edge can lie just outside
    the corridor, diagonally off a wall end. Conservative measure: for every
    boundary EDGE that enters the corridor (i.e. an edge the wall faces), the
    distance to that edge clipped to the wall's outward half-plane is also
    taken; the reported value is the minimum. An edge that never enters the
    corridor is still not "in front" of the wall — a perpendicular wall keeps
    NOT being charged with its corner's distance (a plain wall→whole-boundary
    min would). Returns ``(distance, interpretation-note values)``.
    """
    (x1, y1), (x2, y2) = wall.line.coords[0], wall.line.coords[-1]
    nx, ny = wall.normal
    corridor = Polygon(
        [
            (x1, y1),
            (x2, y2),
            (x2 + nx * reach_m, y2 + ny * reach_m),
            (x1 + nx * reach_m, y1 + ny * reach_m),
        ]
    )
    facing = boundary.intersection(corridor)
    if facing.is_empty:
        return None
    corridor_distance = float(wall.line.distance(facing))
    # Outward half-plane, bounded: the corridor extended laterally by reach_m
    # on both sides (points farther sideways are farther than the parcel
    # diagonal and can never undercut the corridor distance).
    ux, uy = wall.direction
    half_plane = Polygon(
        [
            (x1 - ux * reach_m, y1 - uy * reach_m),
            (x2 + ux * reach_m, y2 + uy * reach_m),
            (x2 + (ux + nx) * reach_m, y2 + (uy + ny) * reach_m),
            (x1 + (nx - ux) * reach_m, y1 + (ny - uy) * reach_m),
        ]
    )
    distance = corridor_distance
    for edge in boundary_edges:
        if not edge.intersects(corridor):
            continue  # the wall does not face this granica stretch
        clipped = edge.intersection(half_plane)
        if clipped.is_empty:
            continue
        distance = min(distance, float(wall.line.distance(clipped)))
    return distance, {
        "corridor_distance_m": round(corridor_distance, 3),
        "faced_edge_halfplane_min_m": round(distance, 3),
    }


def check_boundary_setbacks(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    *,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
) -> list[RuleCheck]:
    """Evaluate WT §12 setbacks for every external wall plane of NEW buildings."""
    rule = registry.get(RULE_WT12)
    if rule is None:
        return [missing_rule_check(RULE_WT12, mode)]

    boundary = ctx.parcel.boundary
    boundary_edges = _boundary_edges(boundary)
    minx, miny, maxx, maxy = ctx.parcel.bounds
    reach_m = math.hypot(maxx - minx, maxy - miny) + 1.0  # parcel diagonal
    interpretation = (
        "min(odleglosc w korytarzu prostopadlym, odleglosc do krawedzi granicy "
        "wchodzacych w korytarz przycietych do polplaszczyzny zewnetrznej "
        "sciany) — konserwatywnie dla granic ukosnych"
    )
    out: list[RuleCheck] = []
    for building in ctx.buildings:
        if not building.is_new:
            # Existing/heritage buildings already stand — §12 governs situating
            # NEW buildings (documented scope decision; cf. validate_hard_masterplan).
            continue
        override = find_override(
            overrides, analysis_id, RULE_WT12, subject=building.name
        )
        over4 = building.is_multifamily and building.max_floors > 4
        evaluated: list[tuple[Wall, float, RuleCheck, dict[str, float]]] = []
        for wall in building.walls:
            facing = _facing_boundary_distance(
                wall, boundary, boundary_edges, reach_m
            )
            if facing is None:
                continue  # wall faces no granica — no §12 case (see helper docstring)
            distance, note = facing
            check = evaluate_rule(
                rule,
                {
                    "wall_has_windows_or_doors": wall.windowed,
                    "is_multifamily_over_4_storeys": over4,
                    "distance_to_boundary_m": distance,
                    # mpzp_allows_reduced_setback intentionally absent → the rule's
                    # documented conservative default (false) applies + is traced.
                },
                mode=mode,
                override=override,
            )
            evaluated.append((wall, distance, check, note))

        if not evaluated:
            continue
        fails = [r for r in evaluated if r[2].status is RuleStatus.FAIL]
        if fails:
            for wall, distance, check, note in fails:
                entry = decided_entry(check)
                required = entry["target_value"] if entry else "?"
                out.append(
                    with_evidence(
                        check,
                        message=(
                            f"{building.name}: {_wall_label(wall)} w odleglosci "
                            f"{distance:.2f} m od granicy dzialki — wymagane "
                            f">= {required} m (WT par. 12)"
                        ),
                        evidence={
                            "geometry": dict(mapping(wall.line)),
                            "building": building.name,
                            "segment_index": wall.segment_index,
                            "edge_index": wall.edge_index,
                            "distance_to_boundary_m": round(distance, 3),
                            "windowed": wall.windowed,
                            "assumed_windowed": wall.assumed_windowed,
                            "is_multifamily_over_4_storeys": over4,
                        },
                        extra_trace={
                            "distance_interpretation": {
                                "method": interpretation,
                                **note,
                            }
                        },
                    )
                )
        else:
            # All walls compliant → one PASS summary with the binding wall.
            wall, distance, check, note = min(
                evaluated, key=lambda t: check_margin(t[2])
            )
            out.append(
                with_evidence(
                    check,
                    message=(
                        f"{building.name}: wszystkie sciany >= wymaganej odleglosci od "
                        f"granicy; najblizsza: {_wall_label(wall)} w {distance:.2f} m"
                    ),
                    evidence={
                        "geometry": dict(mapping(wall.line)),
                        "building": building.name,
                        "segment_index": wall.segment_index,
                        "edge_index": wall.edge_index,
                        "distance_to_boundary_m": round(distance, 3),
                        "windowed": wall.windowed,
                        "assumed_windowed": wall.assumed_windowed,
                        "is_multifamily_over_4_storeys": over4,
                    },
                    extra_trace={
                        "distance_interpretation": {
                            "method": interpretation,
                            **note,
                        }
                    },
                )
            )
    return out
