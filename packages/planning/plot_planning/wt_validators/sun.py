"""WT §60 — nasłonecznienie (insolation) engine + validator (plan §10.1.3).

Solar positions come from **pvlib** (verified 0.15.1):
``pvlib.solarposition.get_solarposition(time, latitude, longitude, ...)``
(per the pvlib docs / installed signature) returns a DataFrame with
``apparent_elevation`` and ``azimuth`` (degrees clockwise from north). Times are
15-min steps (config) on the March equinox within the RULE's hour window
(pokoje mieszkalne 7:00–17:00; plac zabaw 10:00–16:00 — hours read from the
wt-60 / wt-40 YAML thresholds, never literals) in local civil time.

2.5D shadow casting (standard prism-shadow formula, NFR-PERF-013 — no 3D mesh):
for a building of height ``H`` at solar elevation ``el`` and azimuth ``az``::

    L  = H / tan(el)                      # horizontal shadow length
    dir = az + 180°                       # shadow extends AWAY from the sun
    (dx, dy) = (L·sin(dir), L·cos(dir))   # azimuth from north/+Y, clockwise

``shadow = footprint ∪ translate(footprint, dx, dy) ∪ per-edge sweep
parallelograms`` — exact for a vertical prism (the edge sweeps close the gap a
plain union would leave for non-convex footprints).

A window point is SUNLIT at time *t* iff: solar elevation > 0, the sun azimuth
lies in the wall's sun-facing half-plane (outward normal · horizontal sun
vector > 0), and the point is not covered by ANY building's shadow polygon
(window points are offset just outside their wall and evaluated at GROUND level
— conservative: ground shadows are the longest; the sill heuristic feeds only
§13 wysokość przesłaniania). Insolation = the LONGEST CONTINUOUS sunlit run;
``k`` consecutive sunlit samples span ``(k−1)·step`` — interval-based, i.e.
conservative (never overstates).

Performance guard (plan §10.1.3): obstructor parts farther from a point than
their maximum possible shadow length ``H / tan(min elevation in the window)``
are skipped entirely for that point.

Dwelling aggregation (§60 ust. 2 — multi-room dwellings need ONE compliant
room): the DSL has no dwelling layout, so per window the validator is exact
where decidable and honest where not:

* a failing window in a segment whose OTHER windows also ALL fail → **FAIL**
  (no dwelling layout could satisfy ust. 2 — provably non-compliant);
* a failing window in a segment that has compliant windows → **UNKNOWN**
  (layout-dependent), reported per evaluation mode (conservative → WARNING
  with a blocker note), with the ust. 2 reasoning recorded in the trace.
"""

from __future__ import annotations

import math
from functools import lru_cache

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry, RuleStatus
from plot_rules import evaluate as evaluate_rule
from shapely.affinity import translate
from shapely.geometry import Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from plot_planning.wt_validators.config import (
    RULE_WT60,
    ValidatorConfig,
    check_margin,
    decided_entry,
    find_override,
    missing_rule_check,
    rule_threshold,
    with_evidence,
)
from plot_planning.wt_validators.context import (
    RESIDENTIAL_USES,
    ObstructorPart,
    ValidationContext,
    Wall,
    offset_point,
    sample_wall_points,
)

# Numerical guards (geometry precision, not legal values).
_MIN_ELEVATION_DEG = 0.01
_FACING_EPS = 1e-9


@lru_cache(maxsize=32)
def _solar_samples(
    lat: float, lon: float, date_iso: str, start_h: float, end_h: float,
    step_min: int, tz: str,
) -> tuple[tuple[float, float], ...]:
    """(elevation°, azimuth°) at each step of the window — pvlib, cached."""
    import pandas as pd
    from pvlib.solarposition import get_solarposition  # pvlib docs API (verified 0.15.1)

    day = pd.Timestamp(date_iso, tz=tz)
    times = pd.date_range(
        day + pd.Timedelta(hours=start_h),
        day + pd.Timedelta(hours=end_h),
        freq=f"{step_min}min",
    )
    solpos = get_solarposition(times, lat, lon)
    return tuple(
        (float(el), float(az))
        for el, az in zip(solpos["apparent_elevation"], solpos["azimuth"], strict=True)
    )


def solar_window_samples(
    config: ValidatorConfig, start_h: float, end_h: float
) -> tuple[tuple[float, float], ...]:
    """Public wrapper over the cached pvlib solar samples (Phase 12 reuse).

    The site-context neighbor-shading module reuses the §60 machinery
    (ShadowField + these samples) for the with/without impact comparison —
    plan PHASE 12 delta 1 ("do not re-implement").
    """
    return _solar_samples(
        config.site_lat,
        config.site_lon,
        config.equinox_date,
        start_h,
        end_h,
        config.sun_step_min,
        config.timezone,
    )


def shadow_polygon(
    footprint: BaseGeometry, height_m: float, elevation_deg: float, azimuth_deg: float
) -> BaseGeometry:
    """2.5D prism shadow (formula in the module docstring)."""
    length = height_m / math.tan(math.radians(elevation_deg))
    direction = math.radians(azimuth_deg + 180.0)
    dx, dy = length * math.sin(direction), length * math.cos(direction)
    moved = translate(footprint, xoff=dx, yoff=dy)
    sweeps: list[Polygon] = []
    for poly in getattr(footprint, "geoms", [footprint]):
        if not hasattr(poly, "exterior"):  # pragma: no cover - non-polygonal part
            continue
        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)
            for (x1, y1), (x2, y2) in zip(coords, coords[1:], strict=False):
                quad = Polygon([(x1, y1), (x2, y2), (x2 + dx, y2 + dy), (x1 + dx, y1 + dy)])
                if quad.is_valid and quad.area > 0:
                    sweeps.append(quad)
    return unary_union([footprint, moved, *sweeps])


def longest_run_hours(sunlit: list[bool], step_min: int) -> float:
    """Longest CONTINUOUS sunlit duration; k samples span (k−1)·step (conservative)."""
    best = run = 0
    for flag in sunlit:
        run = run + 1 if flag else 0
        best = max(best, run)
    return max(0, best - 1) * step_min / 60.0


class ShadowField:
    """Lazy per-(part, time) shadow-polygon cache shared across sampled points.

    Carries the plan §10.1.3 performance guard: a part whose distance to the
    queried point exceeds its maximum possible shadow length over the window
    (``H / tan(min elevation)``) is skipped without building its shadow.
    """

    def __init__(
        self,
        parts: tuple[ObstructorPart, ...],
        samples: tuple[tuple[float, float], ...],
    ) -> None:
        self.parts = parts
        self.samples = samples
        self._shadows: dict[tuple[int, int], BaseGeometry] = {}
        min_el = min((el for el, _ in samples if el > _MIN_ELEVATION_DEG), default=None)
        self.max_lengths = [
            0.0 if min_el is None else p.height_m / math.tan(math.radians(min_el))
            for p in parts
        ]

    def _shadow(self, part_idx: int, t_idx: int) -> BaseGeometry:
        key = (part_idx, t_idx)
        if key not in self._shadows:
            elevation, azimuth = self.samples[t_idx]
            part = self.parts[part_idx]
            self._shadows[key] = shadow_polygon(
                part.geometry, part.height_m, elevation, azimuth
            )
        return self._shadows[key]

    def insolation_hours(
        self, point: Point, step_min: int, *, normal: tuple[float, float] | None = None
    ) -> float:
        """Longest continuous sunlit run for one point (window if ``normal`` given)."""
        candidates = [
            i
            for i, (part, max_len) in enumerate(
                zip(self.parts, self.max_lengths, strict=True)
            )
            if float(part.geometry.distance(point)) <= max_len
        ]
        sunlit: list[bool] = []
        for t_idx, (elevation, azimuth) in enumerate(self.samples):
            if elevation <= _MIN_ELEVATION_DEG:
                sunlit.append(False)
                continue
            if normal is not None:
                az = math.radians(azimuth)
                sun_dir = (math.sin(az), math.cos(az))  # horizontal vector TOWARD the sun
                if normal[0] * sun_dir[0] + normal[1] * sun_dir[1] <= _FACING_EPS:
                    sunlit.append(False)  # sun behind the wall plane
                    continue
            shaded = any(self._shadow(i, t_idx).covers(point) for i in candidates)
            sunlit.append(not shaded)
        return longest_run_hours(sunlit, step_min)


def playground_insolation_hours(
    playground: BaseGeometry,
    parts: tuple[ObstructorPart, ...],
    *,
    start_h: float,
    end_h: float,
    area_share: float,
    config: ValidatorConfig,
) -> float | None:
    """§40 ust. 3 input: hours achieved by ≥ ``area_share`` of the playground area.

    Grid points (``playground_grid_m`` config) + the centroid are sampled; the
    per-point longest-run hours are sorted descending and the value at the
    area-share quantile is returned (≥ share of sampled points reach it). The
    share itself comes from the wt-40 YAML (``insolation_area_share``).
    """
    if playground.is_empty or playground.area <= 0:
        return None
    samples = _solar_samples(
        config.site_lat, config.site_lon, config.equinox_date,
        start_h, end_h, config.sun_step_min, config.timezone,
    )
    minx, miny, maxx, maxy = playground.bounds
    step = config.playground_grid_m
    points: list[Point] = []
    y = miny + step / 2.0
    while y < maxy:
        x = minx + step / 2.0
        while x < maxx:
            p = Point(x, y)
            if playground.covers(p):
                points.append(p)
            x += step
        y += step
    centroid = playground.centroid
    if playground.covers(centroid):
        points.append(centroid)
    if not points:
        points = [centroid]
    field = ShadowField(parts, samples)
    hours = sorted(
        (field.insolation_hours(p, config.sun_step_min) for p in points), reverse=True
    )
    idx = max(0, math.ceil(area_share * len(hours)) - 1)
    return hours[idx]


def check_naslonecznienie(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    *,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
) -> list[RuleCheck]:
    """Evaluate WT §60 for every windowed wall of residential segments."""
    rule = registry.get(RULE_WT60)
    if rule is None:
        return [missing_rule_check(RULE_WT60, mode)]
    cfg = ctx.config
    mode = EvaluationMode(mode)
    # m6: shadow heights derive from the floor_height_m heuristic — marked in
    # every §60 output (trace + evidence).
    basis = cfg.basis_block("floor_height_m")

    # The dwelling hour window (7:00–17:00) comes from the wt-60 YAML thresholds.
    samples = _solar_samples(
        cfg.site_lat, cfg.site_lon, cfg.equinox_date,
        rule_threshold(rule, "dwelling_window_start_h"),
        rule_threshold(rule, "dwelling_window_end_h"),
        cfg.sun_step_min, cfg.timezone,
    )
    field = ShadowField(ctx.all_parts(), samples)

    out: list[RuleCheck] = []
    for building in ctx.buildings:
        if not building.is_new:
            # §60 compliance burden is assessed for the buildings being SITUATED;
            # an existing building's pre-existing insolation deficit is not
            # attributable to this investment (shadows still come from ALL parts —
            # existing + neighbors shade the new windows). Neighbor-impact baseline
            # attribution (new shadow on existing dwellings) is the Phase 12
            # with/without comparison in
            # plot_planning.site_context.neighbor_shading_impact (soft report).
            continue
        override = find_override(
            overrides, analysis_id, RULE_WT60, subject=building.name
        )
        walls = [w for w in building.walls if w.windowed and w.use in RESIDENTIAL_USES]
        if not walls:
            continue  # no pokoje mieszkalne modeled (usługi room types unknown)

        # window evaluations grouped per SEGMENT (the ust. 2 aggregation unit).
        by_segment: dict[int, list[tuple[Wall, Point, float, RuleCheck]]] = {}
        for wall in walls:
            for point in sample_wall_points(wall, cfg.window_spacing_m):
                origin = offset_point(point, wall.normal, cfg.window_offset_m)
                hours = field.insolation_hours(
                    origin, cfg.sun_step_min, normal=wall.normal
                )
                check = evaluate_rule(
                    rule,
                    {
                        "room_type": "pokoj_mieszkalny",
                        "insolation_hours_equinox": hours,
                        "zabudowa_srodmiejska": ctx.srodmiejska,
                        # is_single_room_dwelling intentionally absent → the rule's
                        # documented conservative default (false) applies + is traced.
                    },
                    mode=mode,
                    override=override,
                )
                by_segment.setdefault(wall.segment_index, []).append(
                    (wall, origin, hours, check)
                )

        building_records: list[tuple[Wall, Point, float, RuleCheck]] = []
        emitted_fail = False
        for seg_idx in sorted(by_segment):
            records = by_segment[seg_idx]
            building_records.extend(records)
            failing = [r for r in records if r[3].status is RuleStatus.FAIL]
            if not failing:
                continue
            segment_has_pass = any(r[3].status is RuleStatus.PASS for r in records)
            worst = min(failing, key=lambda r: r[2])
            wall, origin, hours, check = worst
            entry = decided_entry(check)
            required = entry["target_value"] if entry else "?"
            if segment_has_pass:
                # ust. 2: another room of the dwelling MAY satisfy §60 — the
                # dwelling layout is unknown, so the outcome is UNDECIDABLE, not
                # a fail. Reported per evaluation mode (engine semantics).
                status = {
                    EvaluationMode.STRICT: RuleStatus.FAIL,
                    EvaluationMode.CONSERVATIVE: RuleStatus.WARNING,
                    EvaluationMode.OPTIMISTIC: RuleStatus.UNKNOWN,
                }[mode]
                adjusted = check.model_copy(
                    update={
                        "status": status,
                        "trace": {
                            **check.trace,
                            "ust2_aggregation": {
                                "segment_index": seg_idx,
                                "reason": (
                                    "okno ponizej wymogu, ale segment ma okna "
                                    "spelniajace par. 60 — uklad mieszkan nieznany "
                                    "(par. 60 ust. 2): wynik nieokreslony"
                                ),
                                "mode_applied": mode.value,
                            },
                        },
                    }
                )
                out.append(
                    with_evidence(
                        adjusted,
                        message=(
                            f"{building.name}: okno (segment {seg_idx}, sciana "
                            f"#{wall.edge_index}) ma {hours:.2f} h naslonecznienia "
                            f"(< wymagane {required} h), lecz segment ma okna "
                            "spelniajace wymog — par. 60 ust. 2 zalezy od ukladu "
                            "mieszkan (nieznany) [potencjalny blocker]"
                        ),
                        evidence=_window_evidence(
                            building.name, wall, origin, hours, basis
                        ),
                        extra_trace={"basis": basis},
                    )
                )
            else:
                # EVERY window of the segment fails → no dwelling layout can
                # satisfy ust. 2 → provably non-compliant.
                emitted_fail = True
                out.append(
                    with_evidence(
                        check,
                        message=(
                            f"{building.name}: wszystkie okna segmentu {seg_idx} "
                            f"ponizej wymogu naslonecznienia — najgorsze okno (sciana "
                            f"#{wall.edge_index}): {hours:.2f} h < wymagane "
                            f"{required} h na rownonoc (WT par. 60)"
                        ),
                        evidence=_window_evidence(
                            building.name, wall, origin, hours, basis
                        ),
                        extra_trace={"basis": basis},
                    )
                )

        all_pass = building_records and all(
            r[3].status is RuleStatus.PASS for r in building_records
        )
        if all_pass and not emitted_fail:
            wall, origin, hours, check = min(
                building_records, key=lambda r: check_margin(r[3])
            )
            out.append(
                with_evidence(
                    check,
                    message=(
                        f"{building.name}: kazde okno >= wymaganego naslonecznienia; "
                        f"najgorsze okno (segment {wall.segment_index}, sciana "
                        f"#{wall.edge_index}): {hours:.2f} h"
                    ),
                    evidence=_window_evidence(
                        building.name, wall, origin, hours, basis
                    ),
                    extra_trace={"basis": basis},
                )
            )
    return out


def _window_evidence(
    building: str, wall: Wall, origin: Point, hours: float, basis: dict[str, object]
) -> dict[str, object]:
    return {
        "geometry": dict(mapping(wall.line)),
        "building": building,
        "segment_index": wall.segment_index,
        "edge_index": wall.edge_index,
        "window": [round(origin.x, 3), round(origin.y, 3)],
        "insolation_hours_equinox": round(hours, 3),
        "assumed_windowed": wall.assumed_windowed,
        "basis": basis,
    }
