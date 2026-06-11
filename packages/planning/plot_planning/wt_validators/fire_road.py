"""Droga pożarowa reachability (rozp. MSWiA Dz.U. 2009/124/1030; plan §10.1.6).

Trigger (computed by this caller, per the YAML's ``notes`` contract): a NEW
building with any ZL segment (mieszkalny → ZL IV etc., simplified mapping)
whose height (floors × ``floor_height_m`` config) exceeds the rule's
``trigger_height_m`` (12 m = budynek średniowysoki) REQUIRES a fire road. PM
triggers (§12 ust. 1 pkt 3–4, Q/area based) are not modeled → such buildings
evaluate as not-required with the simplification noted. Not-required buildings
still get an explicit NOT_APPLICABLE check (never a silent skip).

Candidate roads: ``RoadElement(function="pozarowa")`` or ``"kdw"`` with width ≥
the rule's ``min_road_width_m``. Geometric inputs per required building:

* ``runs_along_longer_side`` — SIMPLIFICATION (documented; the plan's "longer
  side" rule): the best candidate road's corridor polygon must lie within the
  legal 5–15 m band (``edge_distance_min/max_m`` from YAML) for ≥
  ``fire_road_coverage_share`` (config, 0.5) of sample points along one of the
  building's facade edges TIED for longest (a rectangle has two equal longer
  sides — a road along either satisfies par. 12 ust. 2); the best-covered
  (road, facade) pair wins deterministically;
* ``road_edge_distance_m`` — min sampled facade-point → road-corridor distance;
* ``shorter_side_over_60m`` — minimum-rotated-rectangle shorter side vs the
  rule's ``both_sides_shorter_side_m`` (60 m, added to the YAML with its
  §12 ust. 2 citation); when triggered, ``road_on_both_sides`` repeats the
  band test on the most opposite (anti-parallel-normal) facade edge;
* ``outer_curve_radius_m`` — polyline approximation (documented): at each
  centerline vertex with deflection δ between segments, the largest arc
  tangent to both within the shorter segment has r ≈ min(l₁,l₂)/(2·tan(δ/2));
  the minimum over vertices is reported (straight road → large finite sentinel);
* dead-end (``dead_end`` / ``plac_manewrowy_*``): a centerline endpoint not
  within tolerance of another road corridor or the parcel boundary is a dead
  end; the plac manewrowy inputs are the side lengths of the minimum rotated
  rectangle of (all candidate corridors ∩ a search disc around the endpoint,
  radius = the rule's 20 m plac size) — an area-style test per the plan
  (optimistic vs a true inscribed-square test; documented approximation);
* ``dojscie_length_m`` — building entrance ASSUMED at the midpoint of the
  facade edge nearest the chosen road (documented assumption); straight-line
  distance to the road corridor;
* ``dojscie_width_m`` — verified by a clearance test: the straight
  entrance→road segment buffered to the rule's ``min_dojscie_width_m`` must not
  cross another building; clear → the verified width (= the threshold) is
  passed, blocked → the input stays absent (a curved dojście may exist →
  honest *unknown*).

No candidate road at all → per the YAML notes: ``road_width_m = 0`` (+
``runs_along_longer_side = False``) → FAIL.
"""

from __future__ import annotations

import math

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry, RuleStatus
from plot_rules import evaluate as evaluate_rule
from shapely.geometry import LineString, Point, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from plot_planning.wt_validators.config import (
    RULE_PPOZ_DROGA,
    find_override,
    missing_rule_check,
    rule_threshold,
    with_evidence,
)
from plot_planning.wt_validators.context import (
    BuildingContext,
    RoadInfo,
    ValidationContext,
    Wall,
)

#: Sentinel radius for a straight centerline (no curve to approximate) —
#: a large finite value (JSON-safe), clearly traced, not a legal threshold.
_STRAIGHT_RADIUS_M = 1.0e9
#: Min deflection treated as a curve (numerical noise filter, degrees).
_MIN_DEFLECTION_DEG = 1.0


def _curve_radius(centerline: BaseGeometry) -> float:
    """Min approximated curve radius over the centerline vertices (docstring)."""
    if centerline.geom_type != "LineString":
        return _STRAIGHT_RADIUS_M
    coords = list(centerline.coords)
    best = _STRAIGHT_RADIUS_M
    for i in range(1, len(coords) - 1):
        (x0, y0), (x1, y1), (x2, y2) = coords[i - 1], coords[i], coords[i + 1]
        v1 = (x1 - x0, y1 - y0)
        v2 = (x2 - x1, y2 - y1)
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 <= 0 or l2 <= 0:
            continue
        dot = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
        deflection = math.acos(max(-1.0, min(1.0, dot)))
        if math.degrees(deflection) < _MIN_DEFLECTION_DEG:
            continue
        radius = min(l1, l2) / (2.0 * math.tan(deflection / 2.0))
        best = min(best, radius)
    return best


def _band_coverage(
    edge: LineString, road_poly: BaseGeometry, *, sample_m: float,
    band_min: float, band_max: float,
) -> tuple[float, float]:
    """(share of samples within [band_min, band_max], min sampled distance)."""
    length = float(edge.length)
    n = max(2, int(length // sample_m) + 1)
    in_band = 0
    min_dist = float("inf")
    for i in range(n):
        point = edge.interpolate((i / (n - 1)) * length)
        dist = float(point.distance(road_poly))
        min_dist = min(min_dist, dist)
        if band_min <= dist <= band_max:
            in_band += 1
    return in_band / n, min_dist


#: Relative length tolerance for walls "tied" for longest (a rectangle has TWO
#: equal longer sides — par. 12 ust. 2 "wzdluz dluzszego boku" is satisfied by a
#: road along EITHER of them). Numerical tolerance, not a legal value.
_LONGEST_TIE_REL = 1e-6


def _longest_walls(building: BuildingContext) -> list[Wall]:
    """All walls tied for the longest length (deterministic edge-index order)."""
    if not building.walls:
        return []
    longest = max(float(w.line.length) for w in building.walls)
    return [
        w
        for w in sorted(building.walls, key=lambda w: (w.segment_index, w.edge_index))
        if float(w.line.length) >= longest * (1.0 - _LONGEST_TIE_REL)
    ]


def _opposite_wall(building: BuildingContext, reference: Wall) -> Wall | None:
    """Longest wall with a roughly anti-parallel outward normal (two-sides test)."""
    candidates = [
        w
        for w in building.walls
        if (w.normal[0] * reference.normal[0] + w.normal[1] * reference.normal[1]) < -0.5
    ]
    candidates.sort(key=lambda w: -float(w.line.length))
    return candidates[0] if candidates else None


def _dead_end_inputs(
    road: RoadInfo,
    all_road_polys: BaseGeometry,
    parcel_boundary: BaseGeometry,
    *,
    tol_m: float,
    plac_size_m: float,
) -> dict[str, object]:
    """``dead_end`` + plac-manewrowy MRR side lengths at the worst dead endpoint."""
    if road.centerline.geom_type != "LineString":
        return {"dead_end": False}
    coords = list(road.centerline.coords)
    endpoints = [Point(*coords[0]), Point(*coords[-1])]
    other_roads = all_road_polys.difference(road.polygon.buffer(0))
    worst: tuple[float, float] | None = None
    for endpoint in endpoints:
        connected = (
            float(endpoint.distance(parcel_boundary)) <= tol_m
            or (not other_roads.is_empty and float(endpoint.distance(other_roads)) <= tol_m)
        )
        if connected:
            continue
        zone = all_road_polys.intersection(endpoint.buffer(plac_size_m))
        if zone.is_empty:
            dims = (0.0, 0.0)
        else:
            mrr = zone.minimum_rotated_rectangle
            if mrr.geom_type != "Polygon":
                dims = (0.0, 0.0)
            else:
                pts = list(mrr.exterior.coords)
                side_a = math.dist(pts[0], pts[1])
                side_b = math.dist(pts[1], pts[2])
                dims = (min(side_a, side_b), max(side_a, side_b))
        if worst is None or dims < worst:
            worst = dims
    if worst is None:
        return {"dead_end": False}
    return {
        "dead_end": True,
        "plac_manewrowy_width_m": worst[0],
        "plac_manewrowy_length_m": worst[1],
    }


def check_fire_road(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    *,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
) -> list[RuleCheck]:
    """Evaluate droga pożarowa reachability for every NEW building."""
    rule = registry.get(RULE_PPOZ_DROGA)
    if rule is None:
        return [missing_rule_check(RULE_PPOZ_DROGA, mode)]
    cfg = ctx.config

    trigger_h = rule_threshold(rule, "trigger_height_m")
    min_width = rule_threshold(rule, "min_road_width_m")
    band_min = rule_threshold(rule, "edge_distance_min_m")
    band_max = rule_threshold(rule, "edge_distance_max_m")
    both_sides_trigger = rule_threshold(rule, "both_sides_shorter_side_m")
    plac_size = rule_threshold(rule, "plac_manewrowy_min_m")
    dojscie_width = rule_threshold(rule, "min_dojscie_width_m")

    candidates = [
        r
        for r in ctx.roads
        if r.function == "pozarowa" or (r.function == "kdw" and r.width_m >= min_width)
    ]
    all_candidate_polys = (
        unary_union([r.polygon for r in candidates]) if candidates else None
    )
    dojscie_widths = [r.width_m for r in ctx.roads if r.function == "dojscie"]

    out: list[RuleCheck] = []
    for building in ctx.buildings:
        if not building.is_new:
            continue  # compliance burden assessed for the NEW investment
        override = find_override(
            overrides, analysis_id, RULE_PPOZ_DROGA, subject=building.name
        )
        required = building.is_zl and building.height_m > trigger_h
        if not required:
            check = evaluate_rule(
                rule, {"fire_road_required": False}, mode=mode, override=override
            )
            out.append(
                with_evidence(
                    check,
                    message=(
                        f"{building.name}: droga pozarowa niewymagana — wysokosc "
                        f"{building.height_m:.1f} m <= prog {trigger_h:g} m (ruleset) "
                        "lub brak stref ZL (PM-triggery par. 12 ust. 1 pkt 3-4 "
                        "nieobjete — uproszczenie)"
                    ),
                    evidence={
                        "geometry": None,
                        "building": building.name,
                        "height_m": round(building.height_m, 2),
                        "height_basis": "floors x floor_height_m (industry_heuristic)",
                    },
                )
            )
            continue

        longest_walls = _longest_walls(building)
        if not candidates or not longest_walls or all_candidate_polys is None:
            # Per the YAML notes: no fire road where required → width 0 → FAIL.
            check = evaluate_rule(
                rule,
                {
                    "fire_road_required": True,
                    "road_width_m": 0.0,
                    "runs_along_longer_side": False,
                },
                mode=mode,
                override=override,
            )
            out.append(
                with_evidence(
                    check,
                    message=(
                        f"{building.name}: wymaga drogi pozarowej (wysokosc "
                        f"{building.height_m:.1f} m > {trigger_h:g} m), ale plan nie "
                        f"zawiera drogi 'pozarowa' ani kdw o szerokosci >= "
                        f"{min_width:g} m"
                    ),
                    evidence={
                        "geometry": dict(mapping(building.footprint)),
                        "building": building.name,
                        "height_m": round(building.height_m, 2),
                    },
                )
            )
            continue

        # Best (road, longest-facade) pair: max band coverage over EVERY wall tied
        # for longest (a rectangular building has two equal "longer sides" — a road
        # along either satisfies par. 12 ust. 2); ties → smaller min distance, then
        # wall/declaration order (deterministic).
        scored: list[tuple[float, float, int, int, RoadInfo, Wall]] = []
        for w_idx, wall in enumerate(longest_walls):
            for i, road in enumerate(candidates):
                coverage, min_dist = _band_coverage(
                    wall.line, road.polygon, sample_m=cfg.facade_sample_m,
                    band_min=band_min, band_max=band_max,
                )
                scored.append((-coverage, min_dist, w_idx, i, road, wall))
        scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
        neg_coverage, min_dist, _, _, best, longest = scored[0]
        coverage = -neg_coverage

        inputs: dict[str, object] = {
            "fire_road_required": True,
            "road_width_m": best.width_m,
            "road_edge_distance_m": min_dist,
            "runs_along_longer_side": coverage >= cfg.fire_road_coverage_share,
            "outer_curve_radius_m": _curve_radius(best.centerline),
        }

        # Two-sides requirement (shorter MRR side vs the YAML's 60 m threshold).
        mrr = building.footprint.minimum_rotated_rectangle
        if mrr.geom_type == "Polygon":
            pts = list(mrr.exterior.coords)
            shorter = min(math.dist(pts[0], pts[1]), math.dist(pts[1], pts[2]))
        else:  # pragma: no cover - degenerate footprint
            shorter = 0.0
        over = shorter > both_sides_trigger
        inputs["shorter_side_over_60m"] = over
        if over:
            opposite = _opposite_wall(building, longest)
            both = False
            if opposite is not None:
                for road in candidates:
                    cov2, _ = _band_coverage(
                        opposite.line, road.polygon, sample_m=cfg.facade_sample_m,
                        band_min=band_min, band_max=band_max,
                    )
                    if cov2 >= cfg.fire_road_coverage_share:
                        both = True
                        break
            inputs["road_on_both_sides"] = both

        inputs.update(
            _dead_end_inputs(
                best, all_candidate_polys, ctx.parcel.boundary,
                tol_m=cfg.road_connection_tol_m, plac_size_m=plac_size,
            )
        )

        # Dojście: entrance at the midpoint of the facade nearest the road.
        entrance_wall = min(
            building.walls, key=lambda w: float(w.line.distance(best.polygon))
        )
        entrance = entrance_wall.line.interpolate(0.5, normalized=True)
        inputs["dojscie_length_m"] = float(entrance.distance(best.polygon))
        if dojscie_widths:
            inputs["dojscie_width_m"] = min(dojscie_widths)
        else:
            # Clearance test at exactly the required width along the straight
            # entrance→road connection (threshold from YAML, never a literal).
            target = nearest_points(entrance, best.polygon)[1]
            corridor = LineString([entrance, target]).buffer(dojscie_width / 2.0)
            blocked = any(
                corridor.intersection(b.footprint).area > 0
                for b in ctx.buildings
                if b.name != building.name
            )
            if not blocked:
                inputs["dojscie_width_m"] = dojscie_width
            # blocked → input stays absent → engine: unknown (a curved dojście
            # may exist; cannot be proven from this DSL — honest gap).

        check = evaluate_rule(rule, inputs, mode=mode, override=override)
        status_txt = "spelnia" if check.status is RuleStatus.PASS else "nie spelnia"
        out.append(
            with_evidence(
                check,
                message=(
                    f"{building.name} (wysokosc {building.height_m:.1f} m): droga "
                    f"'{best.function}' {status_txt} wymogow drogi pozarowej — pokrycie "
                    f"dluzszego boku {coverage:.0%} w pasmie {band_min:g}-{band_max:g} m, "
                    f"najblizsza krawedz {min_dist:.2f} m"
                ),
                evidence={
                    "geometry": dict(mapping(best.polygon)),
                    "building": building.name,
                    "longest_edge": dict(mapping(longest.line)),
                    "coverage_share": round(coverage, 3),
                    "coverage_share_required": cfg.fire_road_coverage_share,
                    "coverage_basis": "simplification (>=50% dluzszego boku w pasmie)",
                    "min_edge_distance_m": round(min_dist, 3),
                },
            )
        )
    return out
