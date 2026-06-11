"""WT §13 — przesłanianie / obstruction of windows (plan §10.1.2).

Geometric approach: window axes are sampled along every windowed wall of
pomieszczenia na stały pobyt ludzi (spacing ``window_spacing_m`` config, midpoint
minimum). Per window a horizontal cone — vertex at the window axis, bisected by
the wall's outward normal — is built with the rule's own thresholds (angle
``angle_deg`` = 60°, search radius ``tall_obstruction_cap_m`` = 35 m: the legal
maximum any §13 distance can reach, both read from
``PL-WT-13-PRZESLANIANIE-001``, never literals). Every OTHER building part
(proposal segments + neighbor buildings) intersecting the cone is evaluated
through the rule with the geometric inputs:

* ``obstruction_distance_m``     — window point → (obstructor ∩ cone) distance;
* ``obstruction_height_m``       — obstructor part height (floors ×
  ``floor_height_m`` config / explicit neighbor height);
* ``obstruction_width_m``        — extent of the obstructor footprint projected
  onto the wall direction (= "mierząc równolegle do płaszczyzny okna", ust. 3) —
  the FULL footprint is projected (documented simplification);
* ``wysokosc_przeslaniania_m``   — obstructor top − window sill (ust. 2); the
  lowest-window sill is the ``window_sill_m`` config heuristic (1.0 m ground-
  floor parapet, ``basis: industry_heuristic``);
* ``zabudowa_srodmiejska``       — ust. 4 ½-reduction handled by the rule's
  modifier (the flag is just an input here).

Pairs where BOTH the obstructed building and the obstructor already exist are
skipped (nothing is being situated). Wings of the SAME building ARE evaluated
as obstructors (§13 ust. 1 pkt 1 explicitly covers "przesłaniająca część tego
samego budynku"): every same-building part from an OTHER segment enters the
cone test — only the wall's own segment footprint is excluded, and window
samples lying on wall portions covered by a sibling wing (internal portions of
a kept-whole wall) are skipped. Walls coplanar/adjacent to a sibling wing are
handled by the cone geometry itself (the cone opens outward only).

Output: one FAIL per (building, obstructor) pair (worst window evidence) or a
single PASS per building (binding pair / explicit "no obstruction in radius").
Heuristic-based inputs (obstructor height from ``floor_height_m``, sill from
``window_sill_m``) are marked with their ``basis`` entries in trace + evidence.
"""

from __future__ import annotations

import math

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry, RuleStatus
from plot_rules import evaluate as evaluate_rule
from shapely.geometry import Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry

from plot_planning.wt_validators.config import (
    DECIDED_CONFIDENCE,
    RULE_WT13,
    check_margin,
    decided_entry,
    find_override,
    missing_rule_check,
    rule_threshold,
    with_evidence,
)
from plot_planning.wt_validators.context import (
    STALY_POBYT_USES,
    ValidationContext,
    Wall,
    offset_point,
    sample_wall_points,
)

# Arc discretisation of the cone polygon (geometry sampling, not a legal value).
_CONE_ARC_STEPS = 16


def _cone_polygon(
    origin: Point, normal: tuple[float, float], half_angle_deg: float, radius_m: float
) -> Polygon:
    """Sector polygon: vertex at the window axis, bisected by the wall normal."""
    base = math.atan2(normal[1], normal[0])
    half = math.radians(half_angle_deg)
    points = [(origin.x, origin.y)]
    for i in range(_CONE_ARC_STEPS + 1):
        ang = base - half + (2 * half) * (i / _CONE_ARC_STEPS)
        points.append((origin.x + radius_m * math.cos(ang), origin.y + radius_m * math.sin(ang)))
    return Polygon(points)


def _projected_width(geom: BaseGeometry, direction: tuple[float, float]) -> float:
    """Footprint extent projected onto the wall direction (width ∥ window plane)."""
    coords: list[tuple[float, float]] = []
    for poly in getattr(geom, "geoms", [geom]):
        if hasattr(poly, "exterior"):
            coords.extend((x, y) for x, y in poly.exterior.coords)
        else:  # pragma: no cover - degenerate non-polygon obstructor
            coords.extend((x, y) for x, y in poly.coords)
    if not coords:
        return 0.0
    proj = [x * direction[0] + y * direction[1] for x, y in coords]
    return max(proj) - min(proj)


def check_przeslanianie(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    *,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
) -> list[RuleCheck]:
    """Evaluate WT §13 for every windowed stały-pobyt wall vs every obstructor."""
    rule = registry.get(RULE_WT13)
    if rule is None:
        return [missing_rule_check(RULE_WT13, mode)]
    cfg = ctx.config
    # m6: the §13 inputs derive from these heuristics — marked in every output.
    basis = cfg.basis_block("floor_height_m", "window_sill_m")

    # Legal geometry parameters FROM THE RULE: 60° cone + the 35 m cap, which is
    # also the search radius (no §13 distance can legally exceed it).
    half_angle = rule_threshold(rule, "angle_deg") / 2.0
    radius = rule_threshold(rule, "tall_obstruction_cap_m")

    parts = ctx.all_parts()
    out: list[RuleCheck] = []
    for building in ctx.buildings:
        override = find_override(overrides, analysis_id, RULE_WT13, subject=building.name)
        walls = [w for w in building.walls if w.windowed and w.use in STALY_POBYT_USES]
        if not walls:
            continue
        # (obstructor label) -> worst evaluated record for this building; an
        # own-building wing is labelled "<name> (segment <i>)" so the offending
        # wing is NAMED and never collides with cross-segment records.
        evaluated: dict[str, tuple[RuleCheck, Wall, Point, float, BaseGeometry]] = {}
        any_window = False
        for wall in walls:
            for point in sample_wall_points(wall, cfg.window_spacing_m):
                origin = offset_point(point, wall.normal, cfg.window_offset_m)
                if building.footprint.covers(origin):
                    # The sample sits on a wall portion covered by a sibling
                    # wing (kept-whole wall) — an internal portion, not a window.
                    continue
                any_window = True
                cone = _cone_polygon(origin, wall.normal, half_angle, radius)
                for part in parts:
                    same_building = (
                        part.source == "proposal" and part.owner == building.name
                    )
                    if same_building and part.segment_index == wall.segment_index:
                        continue  # a wall is never obstructed by its own segment
                    if not (building.is_new or part.is_new):
                        continue  # both already stand — nothing is being situated
                    clipped = part.geometry.intersection(cone)
                    if clipped.is_empty:
                        continue
                    distance = float(origin.distance(clipped))
                    check = evaluate_rule(
                        rule,
                        {
                            "obstruction_distance_m": distance,
                            "obstruction_height_m": part.height_m,
                            "obstruction_width_m": _projected_width(
                                part.geometry, wall.direction
                            ),
                            "wysokosc_przeslaniania_m": max(
                                part.height_m - cfg.window_sill_m, 0.0
                            ),
                            "zabudowa_srodmiejska": ctx.srodmiejska,
                        },
                        mode=mode,
                        override=override,
                    )
                    label = (
                        f"{part.owner} (segment {part.segment_index})"
                        if same_building
                        else part.owner
                    )
                    record = (check, wall, origin, distance, clipped)
                    current = evaluated.get(label)
                    if current is None or _worse(record, current):
                        evaluated[label] = record

        if not any_window:
            continue
        fails = {
            owner: rec
            for owner, rec in evaluated.items()
            if rec[0].status is RuleStatus.FAIL
        }
        if fails:
            for owner in sorted(fails):
                check, wall, origin, distance, clipped = fails[owner]
                entry = decided_entry(check)
                required = entry["target_value"] if entry else "?"
                out.append(
                    with_evidence(
                        check,
                        message=(
                            f"{building.name}: okno (segment {wall.segment_index}, "
                            f"sciana #{wall.edge_index}) przeslaniane przez '{owner}' w "
                            f"odleglosci {distance:.2f} m — wymagane >= {required} m "
                            "(WT par. 13)"
                        ),
                        evidence={
                            "geometry": dict(mapping(clipped)),
                            "building": building.name,
                            "obstructor": owner,
                            "window": [round(origin.x, 3), round(origin.y, 3)],
                            "distance_m": round(distance, 3),
                            "assumed_windowed": wall.assumed_windowed,
                            "basis": basis,
                        },
                        extra_trace={"basis": basis},
                    )
                )
        elif evaluated:
            owner, (check, wall, origin, distance, clipped) = min(
                evaluated.items(), key=lambda kv: (check_margin(kv[1][0]), kv[0])
            )
            out.append(
                with_evidence(
                    check,
                    message=(
                        f"{building.name}: brak przeslaniania — najblizszy obiekt "
                        f"'{owner}' w {distance:.2f} m spelnia WT par. 13"
                    ),
                    evidence={
                        "geometry": dict(mapping(clipped)),
                        "building": building.name,
                        "obstructor": owner,
                        "window": [round(origin.x, 3), round(origin.y, 3)],
                        "distance_m": round(distance, 3),
                        "assumed_windowed": wall.assumed_windowed,
                        "basis": basis,
                    },
                    extra_trace={"basis": basis},
                )
            )
        else:
            # No obstructor enters ANY window cone within the legal 35 m cap →
            # §13 is satisfied geometrically. Explicit (never silent) PASS.
            assumed = any(w.assumed_windowed for w in walls)
            out.append(
                RuleCheck(
                    rule_id=rule.id,
                    status=RuleStatus.PASS,
                    severity=rule.severity,
                    message=(
                        f"{rule.id}: {building.name}: zaden obiekt przeslaniajacy nie "
                        f"wchodzi w kat 60 stopni zadnego okna w promieniu {radius:g} m "
                        "(prog z ruleset) — par. 13 spelniony"
                    ),
                    trace={
                        "mode": EvaluationMode(mode).value,
                        "search_radius_m": radius,
                        "windows_sampled": sum(
                            len(sample_wall_points(w, cfg.window_spacing_m)) for w in walls
                        ),
                        "assumed_windowed": assumed,
                    },
                    source_reference=rule.source_reference,
                    confidence=DECIDED_CONFIDENCE,
                    geometry_evidence={
                        "geometry": None,
                        "building": building.name,
                        "assumed_windowed": assumed,
                    },
                )
            )
    return out


def _worse(
    a: tuple[RuleCheck, Wall, Point, float, BaseGeometry],
    b: tuple[RuleCheck, Wall, Point, float, BaseGeometry],
) -> bool:
    """Is record ``a`` worse than ``b``? Fails first, then smaller margin/distance."""
    a_fail = a[0].status is RuleStatus.FAIL
    b_fail = b[0].status is RuleStatus.FAIL
    if a_fail != b_fail:
        return a_fail
    return (check_margin(a[0]), a[3]) < (check_margin(b[0]), b[3])
