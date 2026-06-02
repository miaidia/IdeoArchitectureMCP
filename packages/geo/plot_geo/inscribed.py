"""Largest inscribed rectangle / orthogonal polygon (F-0159/F-0160).

shapely has **no** largest-inscribed-RECTANGLE primitive (Implementation Plan §0.4 /
§5.4 anti-pattern: it only ships ``maximum_inscribed_circle``, a *circle*). This module
implements a custom algorithm.

Approach (raster-mask + largest-rectangle-in-histogram):
  1. Rasterise the polygon onto a regular grid covering its bounding box: a cell is
     "inside" when its centre lies in the polygon (with a small inward safety margin so
     the resulting rectangle stays strictly contained, not just touching).
  2. Find the maximal all-inside axis-aligned rectangle of grid cells via the classic
     O(rows·cols) largest-rectangle-in-histogram dynamic program.
  3. Map the cell rectangle back to world coordinates and shrink it by a tiny epsilon so
     ``polygon.buffer(1e-9).contains(rect)`` holds even with floating-point noise.

For ``allow_rotation=True`` we additionally sweep candidate angles — the orientation of
the ``minimum_rotated_rectangle`` plus a small fan around it — rotating the polygon,
solving the axis-aligned problem, and rotating the best rectangle back. We keep the
largest rectangle that is verifiably contained.

This is an approximation whose accuracy scales with the grid resolution; it is exact in
the limit and always returns a *contained* rectangle (never one that pokes outside).
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely import Polygon
from shapely.affinity import rotate
from shapely.geometry.base import BaseGeometry

# Default grid resolution (cells along the longer bbox side). Higher == tighter fit,
# slower. 200 gives a good fit on the golden parcels in well under a second.
_DEFAULT_RESOLUTION = 200
# Containment safety: shrink the final rectangle by this fraction of a cell so it never
# coincides exactly with the boundary.
_SHRINK_CELLS = 0.5


def maximum_inscribed_circle(polygon: BaseGeometry, tolerance: float | None = None) -> Polygon:
    """Largest circle fully inside *polygon*, as a polygon (wrapper over GEOS).

    Uses ``shapely.maximum_inscribed_circle`` (verified: returns a ``LineString`` from
    the centre to the nearest boundary point — see
    ``.venv/lib/python3.12/site-packages/shapely/constructive.py``). We turn that radius
    line into a buffered circle for comparison/center use.
    """
    line = shapely.maximum_inscribed_circle(polygon, tolerance=tolerance)
    coords = list(line.coords)
    center = coords[0]
    radius = float(line.length)
    return shapely.Point(center).buffer(radius)


def inscribed_circle_center(polygon: BaseGeometry) -> tuple[float, float]:
    """The pole of inaccessibility — centre of the maximum inscribed circle."""
    line = shapely.maximum_inscribed_circle(polygon)
    x, y = line.coords[0]
    return float(x), float(y)


def _largest_rectangle_in_mask(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Largest all-True axis-aligned rectangle in a 2-D boolean *mask*.

    Returns ``(row0, col0, row1, col1)`` inclusive cell bounds, or ``None`` if empty.
    Classic largest-rectangle-in-histogram per row, O(rows·cols).
    """
    if mask.size == 0 or not mask.any():
        return None
    rows, cols = mask.shape
    heights = np.zeros(cols, dtype=np.int64)
    best_area = 0
    best: tuple[int, int, int, int] | None = None

    for r in range(rows):
        row = mask[r]
        heights = np.where(row, heights + 1, 0)
        # Monotonic stack over this row's histogram.
        stack: list[int] = []
        c = 0
        while c <= cols:
            cur = int(heights[c]) if c < cols else 0
            if not stack or cur >= int(heights[stack[-1]]):
                stack.append(c)
                c += 1
            else:
                top = stack.pop()
                height = int(heights[top])
                left = stack[-1] + 1 if stack else 0
                width = c - left
                area = height * width
                if area > best_area:
                    best_area = area
                    # Rectangle spans rows [r-height+1 .. r], cols [left .. c-1].
                    best = (r - height + 1, left, r, c - 1)
    return best


def _axis_aligned_in_grid(
    polygon: Polygon, resolution: int
) -> tuple[Polygon | None, float]:
    """Solve the axis-aligned largest-rectangle problem on a grid; return (rect, area)."""
    minx, miny, maxx, maxy = polygon.bounds
    width = maxx - minx
    height = maxy - miny
    if width <= 0 or height <= 0:
        return None, 0.0

    # Cell size: divide the longer side into ``resolution`` cells.
    cell = max(width, height) / resolution
    ncols = max(1, int(math.ceil(width / cell)))
    nrows = max(1, int(math.ceil(height / cell)))

    # Cell-centre coordinates.
    xs = minx + (np.arange(ncols) + 0.5) * cell
    ys = miny + (np.arange(nrows) + 0.5) * cell
    gx, gy = np.meshgrid(xs, ys)  # shape (nrows, ncols)

    # Vectorised point-in-polygon for all cell centres at once.
    pts = shapely.points(gx.ravel(), gy.ravel())
    inside = shapely.contains(polygon, pts).reshape(nrows, ncols)

    rect_cells = _largest_rectangle_in_mask(inside)
    if rect_cells is None:
        return None, 0.0
    r0, c0, r1, c1 = rect_cells

    # World coords of the rectangle: from the inner edge of the first/last inside cell.
    # Use cell centres of the boundary cells and shrink inward by _SHRINK_CELLS so the
    # rectangle stays strictly inside the polygon.
    rminx = minx + (c0 + _SHRINK_CELLS) * cell
    rmaxx = minx + (c1 + 1 - _SHRINK_CELLS) * cell
    rminy = miny + (r0 + _SHRINK_CELLS) * cell
    rmaxy = miny + (r1 + 1 - _SHRINK_CELLS) * cell
    if rmaxx <= rminx or rmaxy <= rminy:
        return None, 0.0

    rect = shapely.box(rminx, rminy, rmaxx, rmaxy)
    if not polygon.buffer(1e-9).contains(rect):
        # Numerical edge case: shrink a touch more until contained.
        rect = shapely.box(
            rminx + 0.5 * cell, rminy + 0.5 * cell, rmaxx - 0.5 * cell, rmaxy - 0.5 * cell
        )
        if rect.is_empty or not polygon.buffer(1e-9).contains(rect):
            return None, 0.0
    return rect, float(rect.area)


def largest_inscribed_rectangle(
    polygon: BaseGeometry,
    *,
    allow_rotation: bool = False,
    resolution: int = _DEFAULT_RESOLUTION,
    angle_steps: int = 18,
) -> Polygon:
    """Largest rectangle fully contained in *polygon* (F-0159) — custom algorithm.

    Args:
        polygon: a (multi)polygon in the analytical CRS (EPSG:2180).
        allow_rotation: if True, sweep candidate orientations and return the best
            rotated rectangle; otherwise the rectangle is axis-aligned.
        resolution: grid cells along the longer bounding-box side (fit accuracy).
        angle_steps: number of angles sampled in [0, 90) when ``allow_rotation``.

    Returns:
        a ``shapely.Polygon`` rectangle. The result satisfies
        ``polygon.buffer(1e-9).contains(rect)`` (containment guarantee) and has
        positive area for any non-degenerate polygon.

    Raises:
        ValueError: if *polygon* is empty or not polygonal.
    """
    if polygon.is_empty:
        raise ValueError("largest_inscribed_rectangle: empty geometry")
    if polygon.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(
            f"largest_inscribed_rectangle: expected (Multi)Polygon, got {polygon.geom_type}"
        )

    # For a MultiPolygon, solve per part and keep the best.
    if polygon.geom_type == "MultiPolygon":
        best_multi: Polygon | None = None
        best_multi_area = 0.0
        for part in polygon.geoms:
            cand = largest_inscribed_rectangle(
                part,
                allow_rotation=allow_rotation,
                resolution=resolution,
                angle_steps=angle_steps,
            )
            if cand.area > best_multi_area:
                best_multi_area = cand.area
                best_multi = cand
        if best_multi is None:
            raise ValueError("largest_inscribed_rectangle: no inscribed rectangle found")
        return best_multi

    poly = polygon  # single Polygon below
    assert isinstance(poly, Polygon)

    if not allow_rotation:
        rect, _area = _axis_aligned_in_grid(poly, resolution)
        if rect is None:
            raise ValueError("largest_inscribed_rectangle: no inscribed rectangle found")
        return rect

    # Rotated: candidate angles = orientation of the minimum rotated rectangle plus an
    # even fan across [0, 90). Rotate polygon by -angle, solve axis-aligned, rotate the
    # best rectangle back by +angle about the polygon centroid.
    angles: list[float] = [float(a) for a in np.linspace(0.0, 90.0, angle_steps, endpoint=False)]
    angles.append(_mrr_angle(poly))
    origin = poly.centroid

    best_rect: Polygon | None = None
    best_area = 0.0
    for angle in angles:
        rotated = rotate(poly, -angle, origin=origin, use_radians=False)
        rect, area = _axis_aligned_in_grid(rotated, resolution)
        if rect is None:
            continue
        back = rotate(rect, angle, origin=origin, use_radians=False)
        # Re-verify containment after the inverse rotation (guards float drift).
        if not poly.buffer(1e-9).contains(back):
            continue
        if back.area > best_area:
            best_area = back.area
            best_rect = back

    if best_rect is None:
        # Fall back to the axis-aligned solution.
        rect, _ = _axis_aligned_in_grid(poly, resolution)
        if rect is None:
            raise ValueError("largest_inscribed_rectangle: no inscribed rectangle found")
        return rect
    return best_rect


def _mrr_angle(polygon: Polygon) -> float:
    """Orientation (degrees, [0, 90)) of the longer edge of the min rotated rectangle."""
    mrr = polygon.minimum_rotated_rectangle
    if mrr.geom_type != "Polygon":
        return 0.0
    coords = list(mrr.exterior.coords)
    # Longest edge of the 4-corner rectangle.
    best_len = -1.0
    best_angle = 0.0
    for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:], strict=False):
        length = math.hypot(x1 - x0, y1 - y0)
        if length > best_len:
            best_len = length
            best_angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 90.0
    return best_angle


def largest_orthogonal_polygon(
    polygon: BaseGeometry,
    *,
    resolution: int = _DEFAULT_RESOLUTION,
) -> Polygon:
    """Largest orthogonal (rectilinear) polygon inside *polygon* (F-0160).

    v1: returns the axis-aligned largest inscribed rectangle (a special, always-valid
    orthogonal polygon). A true maximal rectilinear polygon (L/T/staircase shapes) is a
    harder problem deferred to a later phase.

    TODO(F-0160): replace with a true maximal orthogonal polygon (e.g. greedy maximal
    rectangle decomposition over the inside-mask, unioned), keeping the containment
    guarantee.
    """
    return largest_inscribed_rectangle(polygon, allow_rotation=False, resolution=resolution)
