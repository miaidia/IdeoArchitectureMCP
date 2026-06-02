"""Shape metrics for parcels (F-0027–F-0036, base_assumptions §8.6).

All metrics assume the geometry is in the analytical CRS (EPSG:2180), so areas are m²,
lengths are metres, and azimuths are degrees from north (geographic convention). Use
:func:`plot_geo.crs.to_analytical` first if your input is geographic.

Width profile / narrowest passage (F-0029/F-0032) use a medial-axis approximation built
from ``shapely.voronoi_polygons`` of the densified boundary (Implementation Plan §0.4:
"Medial axis / skeleton via shapely.voronoi_polygons"). Voronoi edges whose midpoints
fall inside the polygon approximate the medial axis; the local width at a medial point
is twice its distance to the boundary, so the minimum is the narrowest passage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
import shapely
from shapely import LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

_PI = math.pi


# --------------------------------------------------------------------------------------
# Basic size / shape descriptors (F-0027, §8.6)
# --------------------------------------------------------------------------------------
def area_m2(geom: BaseGeometry) -> float:
    """Polygon area in square metres (EPSG:2180)."""
    return float(geom.area)


def perimeter_m(geom: BaseGeometry) -> float:
    """Perimeter / boundary length in metres."""
    return float(geom.length)


def compactness(geom: BaseGeometry) -> float:
    """Polsby-Popper compactness ``4πA / P²`` in (0, 1]; 1.0 == perfect circle.

    Returns 0.0 for degenerate (zero-perimeter) geometry.
    """
    p = float(geom.length)
    if p <= 0.0:
        return 0.0
    return (4.0 * _PI * float(geom.area)) / (p * p)


def convexity(geom: BaseGeometry) -> float:
    """Convexity = area / convex-hull area, in (0, 1]; 1.0 == convex.

    Returns 0.0 for degenerate geometry.
    """
    hull = geom.convex_hull
    ha = float(hull.area)
    if ha <= 0.0:
        return 0.0
    return float(geom.area) / ha


def irregularity(geom: BaseGeometry) -> float:
    """Irregularity in [0, 1): ``1 - compactness`` proxy combined with non-convexity.

    A simple, bounded score: ``1 - 0.5*(compactness + convexity)``. Convex circles → ~0,
    ragged shapes → toward 1. Intended as a coarse ordering metric, not a legal value.
    """
    return max(0.0, 1.0 - 0.5 * (compactness(geom) + convexity(geom)))


# --------------------------------------------------------------------------------------
# Axes / orientation (F-0034, F-0035)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class MainAxis:
    """Main (longest) axis of a parcel."""

    line: LineString
    length_m: float
    azimuth_deg: float  # 0..360 from north, clockwise


def _azimuth_from_north(x0: float, y0: float, x1: float, y1: float) -> float:
    """Compass azimuth (deg, 0=N, 90=E) of the vector (x0,y0)->(x1,y1)."""
    az = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0
    return az


def main_axis(geom: BaseGeometry) -> MainAxis:
    """Main axis via the longer edge of the minimum rotated rectangle (F-0034).

    The minimum rotated rectangle's long edge is a robust, cheap proxy for the parcel's
    principal direction (more stable than raw PCA on irregular boundaries). The returned
    line runs through the centroid along that direction, clipped to the polygon.
    """
    mrr = geom.minimum_rotated_rectangle
    if mrr.geom_type != "Polygon":
        # Degenerate; fall back to bbox diagonal.
        minx, miny, maxx, maxy = geom.bounds
        line = LineString([(minx, miny), (maxx, maxy)])
        return MainAxis(line=line, length_m=line.length, azimuth_deg=_azimuth_from_north(minx, miny, maxx, maxy))

    coords = list(mrr.exterior.coords)
    # Identify the longer side direction.
    best_len = -1.0
    direction = (1.0, 0.0)
    for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:], strict=False):
        length = math.hypot(x1 - x0, y1 - y0)
        if length > best_len:
            best_len = length
            direction = ((x1 - x0) / length, (y1 - y0) / length)

    cx, cy = geom.centroid.coords[0]
    minx, miny, maxx, maxy = geom.bounds
    span = math.hypot(maxx - minx, maxy - miny) + 1.0
    p_a = (cx - direction[0] * span, cy - direction[1] * span)
    p_b = (cx + direction[0] * span, cy + direction[1] * span)
    full = LineString([p_a, p_b])
    clipped = full.intersection(geom)
    if clipped.is_empty:
        clipped = LineString([(cx, cy), (cx + direction[0], cy + direction[1])])
    # If the clip is multi-part, take the longest piece.
    axis_line = _longest_line(clipped)
    ac = list(axis_line.coords)
    az = _azimuth_from_north(ac[0][0], ac[0][1], ac[-1][0], ac[-1][1])
    return MainAxis(line=axis_line, length_m=float(axis_line.length), azimuth_deg=az)


def _longest_line(geom: BaseGeometry) -> LineString:
    if geom.geom_type == "LineString":
        return geom
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        best: LineString | None = None
        best_len = -1.0
        for part in getattr(geom, "geoms", []):
            if part.geom_type == "LineString" and part.length > best_len:
                best_len = part.length
                best = part
        if best is not None:
            return best
    # Fallback: a tiny degenerate line.
    c = geom.centroid.coords[0]
    return LineString([c, (c[0] + 1.0, c[1])])


# --------------------------------------------------------------------------------------
# Frontage / corner (F-0030, F-0031, F-0035)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Frontage:
    """Detected frontage of a parcel onto a road."""

    line: BaseGeometry  # the parcel boundary portion adjacent to the road
    length_m: float
    azimuth_deg: float


def _as_road_geom(road_geom_or_edges: BaseGeometry | list[BaseGeometry]) -> BaseGeometry:
    if isinstance(road_geom_or_edges, list):
        return shapely.unary_union(road_geom_or_edges) if road_geom_or_edges else LineString()
    return road_geom_or_edges


def detect_frontage(
    parcel: BaseGeometry,
    road_geom_or_edges: BaseGeometry | list[BaseGeometry],
    *,
    tolerance_m: float = 1.0,
) -> Frontage | None:
    """Detect the parcel frontage onto a road (F-0030).

    The frontage is the portion of the parcel boundary within *tolerance_m* of the road
    geometry. Returns ``None`` when the parcel does not adjoin the road within tolerance.
    """
    road = _as_road_geom(road_geom_or_edges)
    if road.is_empty:
        return None
    boundary = parcel.boundary
    road_zone = road.buffer(tolerance_m)
    front = boundary.intersection(road_zone)
    if front.is_empty or float(front.length) <= 0.0:
        return None
    az = frontage_azimuth(front)
    return Frontage(line=front, length_m=float(front.length), azimuth_deg=az)


def frontage_azimuth(frontage_line: BaseGeometry) -> float:
    """Azimuth (deg from north) of a frontage line (F-0035).

    Uses the straight line between the frontage's extreme endpoints (its dominant
    direction). For multi-part frontage the longest part's endpoints are used.
    """
    line = _longest_line(frontage_line) if frontage_line.geom_type != "LineString" else frontage_line
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0
    return _azimuth_from_north(coords[0][0], coords[0][1], coords[-1][0], coords[-1][1])


def is_corner(
    parcel: BaseGeometry,
    roads: BaseGeometry | list[BaseGeometry],
    *,
    tolerance_m: float = 1.0,
    min_distinct_directions: int = 2,
    angular_separation_deg: float = 30.0,
) -> bool:
    """Corner-parcel detection (F-0031).

    A parcel is a corner lot when it fronts roads on two (or more) sides running in
    distinct directions. We compute the frontage, explode it into adjoining segments,
    and count how many fall into separated azimuth buckets (mod 180°, since a road's two
    directions are the same line orientation).
    """
    road = _as_road_geom(roads)
    if road.is_empty:
        return False
    front = parcel.boundary.intersection(road.buffer(tolerance_m))
    if front.is_empty:
        return False

    segments: list[BaseGeometry] = []
    if front.geom_type == "LineString":
        segments = [front]
    elif front.geom_type in ("MultiLineString", "GeometryCollection"):
        segments = [g for g in front.geoms if g.geom_type == "LineString" and g.length > 0]

    orientations: list[float] = []
    for seg in segments:
        coords = list(seg.coords)
        if len(coords) < 2:
            continue
        orientation = _azimuth_from_north(coords[0][0], coords[0][1], coords[-1][0], coords[-1][1]) % 180.0
        orientations.append(orientation)

    distinct: list[float] = []
    for o in orientations:
        if all(_angular_gap(o, d) >= angular_separation_deg for d in distinct):
            distinct.append(o)
    return len(distinct) >= min_distinct_directions


def _angular_gap(a: float, b: float) -> float:
    """Smallest gap between two orientations on a 0..180 circle."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


# --------------------------------------------------------------------------------------
# Width profile / narrowest passage via medial axis (F-0029, F-0032)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class WidthProfile:
    """Width samples along a parcel's medial axis."""

    widths_m: tuple[float, ...]
    sample_points: tuple[tuple[float, float], ...]
    min_width_m: float
    median_width_m: float
    max_width_m: float


def _medial_points(polygon: Polygon, *, segment_len: float) -> list[tuple[float, float]]:
    """Approximate medial-axis vertices via Voronoi of the densified boundary.

    Implementation Plan §0.4: medial axis via ``shapely.voronoi_polygons``. We densify
    the boundary with ``shapely.segmentize`` so the Voronoi diagram of the boundary
    points has interior edges approximating the skeleton; we keep edge midpoints that
    lie inside the polygon.
    """
    boundary = polygon.boundary
    dense = shapely.segmentize(boundary, segment_len)
    # Voronoi of the densified boundary's vertices (edges only).
    edges = shapely.voronoi_polygons(dense, only_edges=True)
    pts: list[tuple[float, float]] = []
    lines: list[LineString]
    if isinstance(edges, MultiLineString):
        lines = list(edges.geoms)
    elif isinstance(edges, LineString):
        lines = [edges]
    else:
        lines = [g for g in getattr(edges, "geoms", []) if g.geom_type == "LineString"]

    shrunk = polygon.buffer(-segment_len * 0.25)  # avoid boundary-hugging Voronoi nodes
    test_region = shrunk if (not shrunk.is_empty) else polygon
    boundary_for_dist = polygon.boundary

    # The medial axis of a polygon legitimately runs into every CONVEX corner, where the
    # local width tapers to ~0. Those corner branches are an artifact for "narrowest
    # passage" (F-0032 przewężenie), which means a real constriction between two OPPOSITE
    # edges, not a corner cusp where the two defining boundary points are adjacent edges
    # meeting at a single vertex. A medial point belongs to a corner cusp when its nearest
    # polygon vertex is essentially as close as the boundary itself — i.e. the corner is
    # its defining contact point. We drop those (scale-free test).
    corner_pts = [shapely.Point(c) for c in _exterior_vertices(polygon)]

    for ln in lines:
        for x, y in ln.coords:
            p = shapely.Point(x, y)
            if not test_region.contains(p):
                continue
            d_boundary = p.distance(boundary_for_dist)
            if d_boundary <= 0.0:
                continue
            d_corner = min((p.distance(c) for c in corner_pts), default=float("inf"))
            # Corner cusp: the closest corner is within ~1.1x the inscribed radius here,
            # meaning the width measured is the corner taper, not a channel.
            if d_corner <= d_boundary * 1.1 + segment_len:
                continue
            pts.append((float(x), float(y)))
    return pts


def _exterior_vertices(polygon: Polygon) -> list[tuple[float, float]]:
    """Exterior + interior ring vertices of *polygon* (the polygon's corners)."""
    verts: list[tuple[float, float]] = [(float(x), float(y)) for x, y in polygon.exterior.coords]
    for ring in polygon.interiors:
        verts.extend((float(x), float(y)) for x, y in ring.coords)
    return verts


def width_profile(geom: BaseGeometry, *, samples: int = 60) -> WidthProfile:
    """Width profile along the medial axis (F-0029).

    The local width at a medial point is twice its distance to the polygon boundary
    (inscribed-circle diameter at that point). Returns sorted-along-axis samples plus
    min/median/max summaries.
    """
    if geom.geom_type == "MultiPolygon":
        # Profile the largest component.
        geom = max(geom.geoms, key=lambda g: g.area)
    if not isinstance(geom, Polygon) or geom.is_empty:
        return WidthProfile((), (), 0.0, 0.0, 0.0)

    minx, miny, maxx, maxy = geom.bounds
    diag = math.hypot(maxx - minx, maxy - miny)
    segment_len = max(diag / max(samples, 1), 1e-3)
    medial = _medial_points(geom, segment_len=segment_len)
    if not medial:
        # Fallback: maximum inscribed circle radius as the single width sample.
        line = shapely.maximum_inscribed_circle(geom)
        w = 2.0 * float(line.length)
        c = line.coords[0]
        return WidthProfile((w,), ((float(c[0]), float(c[1])),), w, w, w)

    boundary = geom.boundary
    widths: list[float] = []
    out_pts: list[tuple[float, float]] = []
    for x, y in medial:
        d = shapely.Point(x, y).distance(boundary)
        widths.append(2.0 * float(d))
        out_pts.append((x, y))

    arr = np.asarray(widths)
    return WidthProfile(
        widths_m=tuple(widths),
        sample_points=tuple(out_pts),
        min_width_m=float(arr.min()),
        median_width_m=float(np.median(arr)),
        max_width_m=float(arr.max()),
    )


def narrowest_passage(geom: BaseGeometry) -> tuple[float, tuple[float, float] | None]:
    """Narrowest passage width and its medial-axis location (F-0032).

    Returns ``(min_width_m, point)`` where ``point`` is the medial location of the
    constriction (or ``None`` if no medial sample exists).
    """
    prof = width_profile(geom)
    if not prof.widths_m:
        return 0.0, None
    idx = int(np.argmin(np.asarray(prof.widths_m)))
    return prof.widths_m[idx], prof.sample_points[idx]


# --------------------------------------------------------------------------------------
# Boundary-edge classification (F-0036)
# --------------------------------------------------------------------------------------
class NeighborType(str, Enum):
    """Type of feature on the far side of a parcel boundary edge."""

    ROAD = "road"
    PARCEL = "parcel"
    WATER = "water"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BoundaryEdge:
    """A classified portion of the parcel boundary."""

    line: BaseGeometry
    neighbor_type: NeighborType
    length_m: float


def classify_boundary_edges(
    parcel: BaseGeometry,
    neighbors: dict[NeighborType, BaseGeometry | list[BaseGeometry]],
    *,
    tolerance_m: float = 1.0,
) -> list[BoundaryEdge]:
    """Classify boundary edges by adjacent neighbour type (F-0036).

    *neighbors* maps a :class:`NeighborType` to the geometry (or list) representing that
    neighbour class (roads, adjacent parcels, water). Boundary portions within
    *tolerance_m* of a neighbour are labelled with the highest-priority matching type
    (ROAD > WATER > PARCEL); the remainder is UNKNOWN.
    """
    boundary = parcel.boundary
    remaining = boundary
    edges: list[BoundaryEdge] = []

    priority = [NeighborType.ROAD, NeighborType.WATER, NeighborType.PARCEL]
    for ntype in priority:
        if ntype not in neighbors or remaining.is_empty:
            continue
        ngeom = _as_road_geom(neighbors[ntype])
        if ngeom.is_empty:
            continue
        zone = ngeom.buffer(tolerance_m)
        matched = remaining.intersection(zone)
        if not matched.is_empty and float(matched.length) > 0.0:
            edges.append(BoundaryEdge(line=matched, neighbor_type=ntype, length_m=float(matched.length)))
            remaining = remaining.difference(zone)

    if not remaining.is_empty and float(remaining.length) > 0.0:
        edges.append(
            BoundaryEdge(line=remaining, neighbor_type=NeighborType.UNKNOWN, length_m=float(remaining.length))
        )
    return edges


# --------------------------------------------------------------------------------------
# Usable area before / after constraints (F-0033 enclaves/wedges proxy, §8.6)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class UsableArea:
    """Usable area before and after subtracting constraint geometry."""

    before_m2: float
    after_m2: float
    removed_m2: float
    removed_percent: float
    usable_geom: BaseGeometry


def usable_area(
    geom: BaseGeometry,
    constraints: BaseGeometry | list[BaseGeometry] | None = None,
) -> UsableArea:
    """Estimate usable area before/after constraints (§8.6, "before and after").

    Subtracts the union of *constraints* from *geom*; returns areas and the residual
    usable geometry. With no constraints, after == before.
    """
    before = float(geom.area)
    if constraints is None:
        return UsableArea(before, before, 0.0, 0.0, geom)
    cgeom = _as_road_geom(constraints)
    if cgeom.is_empty:
        return UsableArea(before, before, 0.0, 0.0, geom)
    usable = geom.difference(cgeom)
    after = float(usable.area)
    removed = max(0.0, before - after)
    pct = (removed / before * 100.0) if before > 0.0 else 0.0
    return UsableArea(before, after, removed, pct, usable)


# Re-export for callers that want the raw frontage azimuth without a Frontage object.
def frontage(
    parcel: BaseGeometry,
    road_geom_or_edges: BaseGeometry | list[BaseGeometry],
    *,
    tolerance_m: float = 1.0,
) -> float:
    """Frontage *length* in metres (0.0 if the parcel does not adjoin the road)."""
    f = detect_frontage(parcel, road_geom_or_edges, tolerance_m=tolerance_m)
    return f.length_m if f is not None else 0.0


def nearest_boundary_distance(geom: BaseGeometry, other: BaseGeometry) -> float:
    """Distance (m) between *geom* and *other* via ``shapely.ops.nearest_points``."""
    a, b = nearest_points(geom, other)
    return float(a.distance(b))
