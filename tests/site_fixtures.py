"""Shared synthetic fixtures for the Phase 12 site-context tests (TEST FIXTURES).

The DEM builder writes a small in-memory GeoTIFF (rasterio docs: MemoryFile +
``rasterio.transform.from_origin``) — deterministic, zero network, clearly NOT
real NMT data. Coordinates live in the EPSG:2180-plausible metric frame used by
``tests/wt_fixtures.py`` (X0/Y0) or around the mock ULDK parcel from
``tests/mocks.py``.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def synthetic_dem_bytes(
    *,
    west: float,
    north: float,
    width: int = 200,
    height: int = 200,
    res_m: float = 1.0,
    base_elevation: float = 100.0,
    east_slope_pct: float = 0.0,
    flat_until_col: int = 0,
    depression_at: tuple[int, int] | None = None,
    depression_depth_m: float = 0.5,
    nodata_rects: list[tuple[int, int, int, int]] | None = None,
    nodata_invert: bool = False,
) -> bytes:
    """A synthetic GeoTIFF DEM: optionally flat west part + eastward slope + a sink.

    ``east_slope_pct`` applies from column ``flat_until_col`` eastwards;
    ``depression_at`` = (row, col) carves a local sink of ``depression_depth_m``.
    ``nodata_rects`` = list of (row0, row1, col0, col1) half-open windows filled
    with the -9999 nodata value (a "hole" in the coverage); ``nodata_invert=True``
    fills nodata EVERYWHERE EXCEPT those windows (a mostly-nodata raster).
    """
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    transform = from_origin(west, north, res_m, res_m)
    cols = np.arange(width, dtype="float64")[None, :].repeat(height, axis=0)
    ramp = np.clip(cols - flat_until_col, 0.0, None)
    z = base_elevation + ramp * res_m * (east_slope_pct / 100.0)
    if depression_at is not None:
        r, c = depression_at
        z[r, c] -= depression_depth_m
    if nodata_rects is not None:
        mask = np.zeros(z.shape, dtype=bool)
        for r0, r1, c0, c1 in nodata_rects:
            mask[r0:r1, c0:c1] = True
        z[~mask if nodata_invert else mask] = -9999.0
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float64",
        "crs": "EPSG:2180",
        "transform": transform,
        "nodata": -9999.0,
    }
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(z, 1)
        return bytes(mem.read())


def gj_polygon(coords: list[tuple[float, float]]) -> dict[str, Any]:
    """A GeoJSON Polygon from an (unclosed) coordinate ring."""
    ring = [list(c) for c in coords] + [list(coords[0])]
    return {"type": "Polygon", "coordinates": [ring]}


def gj_box(minx: float, miny: float, maxx: float, maxy: float) -> dict[str, Any]:
    return gj_polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])


def building_feature(
    geometry: dict[str, Any], **properties: Any
) -> dict[str, Any]:
    """A BDOT10k-shaped GeoJSON Feature (geometry + attribute properties)."""
    return {"type": "Feature", "geometry": geometry, "properties": dict(properties)}
