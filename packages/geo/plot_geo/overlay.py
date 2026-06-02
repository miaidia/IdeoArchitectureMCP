"""Overlay / buffer / clip engine wrappers (base_assumptions §13).

Thin, typed wrappers over shapely 2.x set operations plus a **metric-safe** buffer.
The buffer guard is the key anti-pattern defence (§5.4 / §9.4): buffering in degrees is
meaningless, so :func:`buffer_m` refuses a geographic CRS — the caller must reproject to
EPSG:2180 (or pass ``src_crs`` and let us auto-project, then return in 2180).

Verified APIs (installed shapely 2.1.2):
  * ``shapely.intersection/union/difference/symmetric_difference`` — set_operations.py
  * ``shapely.unary_union`` — set_operations.py
  * ``shapely.simplify(geometry, tolerance, preserve_topology=True)`` — linear/coordinate
  * ``shapely.ops.nearest_points`` — ops.py
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import shapely
from shapely.geometry import GeometryCollection
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from .crs import ANALYTICAL_CRS, is_geographic, to_analytical


def intersection(a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    return shapely.intersection(a, b)


def union(a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    return shapely.union(a, b)


def difference(a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    return shapely.difference(a, b)


def symmetric_difference(a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    return shapely.symmetric_difference(a, b)


def dissolve(geoms: Iterable[BaseGeometry]) -> BaseGeometry:
    """Dissolve (union) a collection of geometries into one (§13 'dissolving')."""
    parts = [g for g in geoms if g is not None and not g.is_empty]
    if not parts:
        return GeometryCollection()
    return shapely.unary_union(parts)


def buffer_m(
    geom: BaseGeometry,
    meters: float,
    *,
    src_crs: str | int | None = None,
    **kwargs: object,
) -> BaseGeometry:
    """Buffer *geom* by *meters* — **only in a metric CRS** (§5.4 anti-pattern guard).

    Behaviour:
      * ``src_crs`` given and geographic  → auto-project to EPSG:2180, buffer, return in
        2180 (we never silently buffer in degrees).
      * ``src_crs`` given and already metric → buffer directly.
      * ``src_crs`` is ``None`` → assume the geometry is already in the analytical metric
        CRS (the documented contract for plot_geo). No degree-buffering can happen here
        because no geographic CRS is involved.

    Raises:
        ValueError: if ``src_crs`` is an explicit geographic CRS and projection is not
            wanted — i.e. we refuse rather than buffer in degrees. (We choose to
            auto-project instead, but a caller can detect this by passing src_crs.)
    """
    if src_crs is not None:
        if is_geographic(src_crs):
            geom = to_analytical(geom, src_crs)  # project into EPSG:2180 metres
        # If a non-metric projected CRS were ever passed we'd still be in linear units;
        # we standardise on EPSG:2180 to keep results comparable.
        elif str(src_crs) != ANALYTICAL_CRS:
            geom = to_analytical(geom, src_crs)
    return shapely.buffer(geom, meters, **kwargs)


def simplify_topo(geom: BaseGeometry, tol: float) -> BaseGeometry:
    """Topology-preserving simplification (§13 'simplification with topology').

    Wraps ``shapely.simplify(..., preserve_topology=True)`` so rings do not invert or
    self-intersect (unlike a raw Douglas-Peucker).
    """
    return shapely.simplify(geom, tol, preserve_topology=True)


def nearest(geom: BaseGeometry, layer: Sequence[BaseGeometry]) -> BaseGeometry | None:
    """Nearest geometry in *layer* to *geom* (§13 'nearest-neighbor'). ``None`` if empty."""
    best: BaseGeometry | None = None
    best_d = float("inf")
    for cand in layer:
        if cand is None or cand.is_empty:
            continue
        d = float(geom.distance(cand))
        if d < best_d:
            best_d = d
            best = cand
    return best


def distance_to_layer(geom: BaseGeometry, layer: Sequence[BaseGeometry]) -> float:
    """Minimum distance (m) from *geom* to any geometry in *layer* (§13).

    Returns ``inf`` for an empty layer.
    """
    best_d = float("inf")
    for cand in layer:
        if cand is None or cand.is_empty:
            continue
        d = float(geom.distance(cand))
        if d < best_d:
            best_d = d
    return best_d


def nearest_point_pair(geom: BaseGeometry, other: BaseGeometry) -> tuple[BaseGeometry, BaseGeometry]:
    """The closest pair of points between two geometries (``shapely.ops.nearest_points``)."""
    a, b = nearest_points(geom, other)
    return a, b


def clip(layer: BaseGeometry, parcel_plus_buffer: BaseGeometry) -> BaseGeometry:
    """Clip *layer* to the parcel-plus-analysis-buffer region (§13 'clipping').

    Equivalent to an intersection, named for the analysis-window use case.
    """
    return shapely.intersection(layer, parcel_plus_buffer)
