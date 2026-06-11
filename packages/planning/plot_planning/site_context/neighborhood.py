"""Neighborhood context: neighbor buildings, boundary windows, shading impact
(Phase 12 / v1 Phase 10 §10.1.6).

* :func:`neighbors_from_features` — BDOT10k building features (GeoJSON Feature
  dicts with properties) → typed :class:`plot_planning.wt_validators.context
  .NeighborBuilding` records the EXISTING Phase 10 validators consume via their
  ``neighbors`` parameter (§13/§60 — no new validator code). Heights come from
  the feature attributes (``wysokosc``) or storeys × floor height (flagged
  ``height_basis: storeys_heuristic``); buildings with no height information are
  SKIPPED with a note (never an invented height, §21).
* :func:`windows_at_boundary_precheck` — neighbors hugging the parcel boundary
  (possible windows facing the boundary: a §12-relevant verification flag).
* :func:`neighbor_shading_impact` — shading caused TO neighbors by the NEW
  buildings (F-0343/0344), REUSING the Phase 10v2 sun engine
  (:class:`~plot_planning.wt_validators.sun.ShadowField` + pvlib samples — plan
  PHASE 12 delta 1: do not re-implement). With/without comparison: insolation at
  neighbor facade points with only pre-existing obstructors vs with the proposal
  added; the report is SOFT (impact information + a §60-threshold flag), since
  the legal burden assessment belongs to the projektant/organ.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from plot_planning.wt_validators.config import ValidatorConfig
from plot_planning.wt_validators.context import NeighborBuilding, ObstructorPart
from plot_planning.wt_validators.sun import ShadowField, solar_window_samples

#: Representative BDOT10k/EGiB attribute names for building height / storeys.
_HEIGHT_KEYS = ("wysokosc", "height", "WYSOKOSC", "wysokoscBudynku")
_STOREY_KEYS = ("liczbaKondygnacji", "kondygnacje", "storeys", "LICZBA_KONDYGNACJI")

#: Boundary proximity under which a neighbor may have windows facing the parcel
#: (verification flag distance — geometry_sampling parameter, the LEGAL setback
#: values stay in the wt-12 ruleset consumed by the validators).
WINDOWS_AT_BOUNDARY_DIST_M = 4.0

#: Minimum insolation loss (hours) worth reporting in the impact report.
MIN_REPORTED_LOSS_H = 0.25


def neighbors_from_features(
    features: list[dict[str, Any]],
    *,
    config: ValidatorConfig | None = None,
) -> tuple[list[NeighborBuilding], list[str]]:
    """BDOT10k building features → typed neighbors + honesty notes (§10.1.6).

    Returns ``(neighbors, notes)``: a feature without a usable height attribute
    is skipped and noted (its shading influence stays UNKNOWN — never a guessed
    height, §21). Storey-derived heights are flagged in the note list.
    """
    from shapely.geometry import shape

    cfg = config or ValidatorConfig()
    neighbors: list[NeighborBuilding] = []
    notes: list[str] = []
    for i, feat in enumerate(features):
        geom_json = feat.get("geometry") if isinstance(feat, dict) else None
        if not isinstance(geom_json, dict):
            continue
        try:
            geom = shape(geom_json)
        except (ValueError, TypeError, KeyError):
            continue
        if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        props = feat.get("properties") or {}
        name = str(props.get("id") or props.get("name") or f"sasiad-{i + 1}")
        height = _height_from_props(props)
        if height is not None:
            neighbors.append(NeighborBuilding(name=name, geometry=geom, height_m=height))
            continue
        storeys = _storeys_from_props(props)
        if storeys is not None:
            neighbors.append(
                NeighborBuilding(
                    name=name, geometry=geom, height_m=storeys * cfg.floor_height_m
                )
            )
            notes.append(
                f"{name}: wysokość z liczby kondygnacji ({storeys} × "
                f"{cfg.floor_height_m} m) — height_basis: storeys_heuristic."
            )
            continue
        notes.append(
            f"{name}: brak atrybutu wysokości/kondygnacji — budynek pominięty w "
            "analizie cienia (wpływ NIEZNANY, nie zgadywany — §21)."
        )
    return neighbors, notes


def windows_at_boundary_precheck(
    neighbors: list[NeighborBuilding],
    parcel: BaseGeometry,
    *,
    distance_m: float = WINDOWS_AT_BOUNDARY_DIST_M,
) -> list[dict[str, Any]]:
    """Neighbors close enough to the boundary to plausibly have facing windows.

    BDOT10k carries no window data — this is a VERIFICATION flag (wizja lokalna /
    EGiB budynki), feeding the §12 conversation, never a determination.
    """
    out: list[dict[str, Any]] = []
    boundary = parcel.boundary
    for n in neighbors:
        d = float(n.shapely().distance(boundary))
        if d <= distance_m:
            out.append(
                {
                    "neighbor": n.name,
                    "distance_to_boundary_m": round(d, 2),
                    "status": "verify",
                    "note": (
                        f"Budynek sąsiada '{n.name}' {d:.1f} m od granicy — możliwe okna "
                        "zwrócone ku działce; zweryfikować w terenie (wpływa na odsunięcia "
                        "§12 i przesłanianie §13)."
                    ),
                }
            )
    return out


def neighbor_shading_impact(
    new_parts: list[ObstructorPart],
    neighbors: list[NeighborBuilding],
    *,
    config: ValidatorConfig | None = None,
    window_start_h: float,
    window_end_h: float,
    min_required_hours: float | None = None,
    existing_parts: list[ObstructorPart] | tuple[ObstructorPart, ...] = (),
) -> list[dict[str, Any]]:
    """Shading caused TO neighbors by the proposal (soft impact report, F-0343/0344).

    For each neighbor: facade sample points (edge midpoints offset outward) are
    evaluated with the REUSED §60 engine twice — baseline obstructors = the
    OTHER neighbors plus ``existing_parts`` (the proposal's PRE-EXISTING
    on-parcel buildings: they are context in BOTH runs, so pre-existing
    insolation loss is never attributed to the new buildings — review m3);
    impact obstructors = baseline + the proposal's new parts. The worst
    per-neighbor loss is reported. ``window_*_h`` and ``min_required_hours``
    come from the wt-60 ruleset thresholds at the call site (legal values never
    live here).
    """
    cfg = config or ValidatorConfig()
    if not new_parts or not neighbors:
        return []
    samples = solar_window_samples(cfg, window_start_h, window_end_h)
    neighbor_parts = {
        n.name: ObstructorPart(
            owner=n.name,
            geometry=n.shapely(),
            height_m=float(n.height_m),
            source="neighbor",
            is_new=False,
        )
        for n in neighbors
    }
    out: list[dict[str, Any]] = []
    for n in neighbors:
        others = tuple(p for name, p in neighbor_parts.items() if name != n.name)
        others = others + tuple(existing_parts)  # context in BOTH runs (m3)
        baseline_field = ShadowField(others, samples)
        impact_field = ShadowField(others + tuple(new_parts), samples)
        worst: dict[str, Any] | None = None
        for point, normal in _facade_points(n.shapely(), cfg.window_offset_m):
            before = baseline_field.insolation_hours(
                point, cfg.sun_step_min, normal=normal
            )
            after = impact_field.insolation_hours(point, cfg.sun_step_min, normal=normal)
            loss = before - after
            if loss < MIN_REPORTED_LOSS_H:
                continue
            entry = {
                "neighbor": n.name,
                "point": [round(point.x, 2), round(point.y, 2)],
                "hours_before": round(before, 2),
                "hours_after": round(after, 2),
                "hours_lost": round(loss, 2),
            }
            if min_required_hours is not None:
                entry["drops_below_wt60_minimum"] = bool(
                    before >= min_required_hours > after
                )
            if worst is None or entry["hours_lost"] > worst["hours_lost"]:
                worst = entry
        if worst is not None:
            worst["status"] = "warning"
            worst["message"] = (
                f"Projektowana zabudowa skraca nasłonecznienie elewacji sąsiada "
                f"'{n.name}' o {worst['hours_lost']:.2f} h (z {worst['hours_before']:.2f} "
                f"do {worst['hours_after']:.2f} h w dniu równonocy)"
                + (
                    " — PONIŻEJ minimum §60 dla pokoi mieszkalnych; wysokie ryzyko "
                    "zarzutu przesłaniania."
                    if worst.get("drops_below_wt60_minimum")
                    else " — wpływ do oceny projektanta (raport miękki)."
                )
            )
            out.append(worst)
    return out


def _facade_points(
    footprint: BaseGeometry, offset_m: float
) -> list[tuple[Point, tuple[float, float]]]:
    """Edge-midpoint sample points just OUTSIDE a neighbor footprint + outward normals.

    Mirrors the §12 wall decomposition idea (orient → CCW exterior → normal
    (dy, −dx)) at neighbor level — neighbors carry no window declaration, so
    every facade midpoint is a conservative window proxy.
    """
    from shapely.geometry.polygon import orient

    points: list[tuple[Point, tuple[float, float]]] = []
    polys = (
        [footprint]
        if footprint.geom_type == "Polygon"
        else [g for g in getattr(footprint, "geoms", []) if g.geom_type == "Polygon"]
    )
    for poly in polys:
        ring = orient(poly, sign=1.0).exterior
        coords = list(ring.coords)
        for (x1, y1), (x2, y2) in zip(coords, coords[1:], strict=False):
            dx, dy = x2 - x1, y2 - y1
            length = (dx * dx + dy * dy) ** 0.5
            if length <= 1e-6:
                continue
            nx, ny = dy / length, -dx / length  # outward normal (CCW exterior)
            mid_x, mid_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            points.append(
                (Point(mid_x + nx * offset_m, mid_y + ny * offset_m), (nx, ny))
            )
    return points


def _height_from_props(props: dict[str, Any]) -> float | None:
    for key in _HEIGHT_KEYS:
        val = props.get(key)
        if isinstance(val, int | float) and val > 0:
            return float(val)
        if isinstance(val, str):
            try:
                parsed = float(val.replace(",", "."))
            except ValueError:
                continue
            if parsed > 0:
                return parsed
    return None


def _storeys_from_props(props: dict[str, Any]) -> int | None:
    for key in _STOREY_KEYS:
        val = props.get(key)
        if isinstance(val, int | float) and val > 0:
            return int(val)
        if isinstance(val, str) and val.strip().isdigit():
            return int(val.strip())
    return None
