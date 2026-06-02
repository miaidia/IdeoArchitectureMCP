"""CRS detection and transformation (F-0014/F-0015, base_assumptions §9.4 / §26.3).

The analytical working CRS for Poland is **EPSG:2180** (PUWG1992); every metric
operation in :mod:`plot_geo` runs there. Input geometry keeps its original CRS as
metadata only — see :func:`to_analytical` for the one-way projection into 2180.

shapely 2.x has no per-geometry CRS, so detection works on GeoJSON ``crs`` members
(legacy GeoJSON) or an explicit hint; transformation goes through
``pyproj.Transformer.from_crs(..., always_xy=True)`` fed to ``shapely.ops.transform``.

API notes (verified against the installed packages):
  * ``shapely.ops.transform(func, geom)`` — signature ``(func, geom)``; ``func`` maps
    (x, y[, z]) arrays -> transformed coords. Verified in
    ``.venv/lib/python3.12/site-packages/shapely/ops.py``.
  * ``pyproj.Transformer.from_crs(crs_from, crs_to, always_xy=False, ...)`` and
    ``Transformer.transform(xx, yy, ...)`` — verified in
    ``.venv/lib/python3.12/site-packages/pyproj/transformer.py``. We pass
    ``always_xy=True`` so coordinates are (lon/easting, lat/northing) regardless of
    the CRS authority axis order (the anti-pattern guard against swapped axes).
"""

from __future__ import annotations

from typing import Any

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as _shapely_transform

# Analytical working CRS for Poland (base_assumptions §26.3). Mirrors
# plot_shared.Settings.analytical_crs but kept as a constant so plot_geo has no
# hard runtime dependency on settings construction for a pure-geometry call.
ANALYTICAL_CRS = "EPSG:2180"

# WGS84 lon/lat — the de-facto CRS of a GeoJSON object that carries no ``crs``
# member (RFC 7946 mandates EPSG:4326).
GEOJSON_DEFAULT_CRS = "EPSG:4326"


def _normalise_crs(crs: str | int | CRS) -> str:
    """Return a canonical ``"EPSG:nnnn"`` (or WKT-derived) authority string."""
    obj = crs if isinstance(crs, CRS) else CRS.from_user_input(crs)
    auth = obj.to_authority()
    if auth is not None:
        return f"{auth[0]}:{auth[1]}"
    return obj.to_wkt()


def is_geographic(crs: str | int | CRS) -> bool:
    """True if *crs* uses angular (degree) units — buffering/measuring there is wrong."""
    obj = crs if isinstance(crs, CRS) else CRS.from_user_input(crs)
    return bool(obj.is_geographic)


def detect_crs(geom_or_geojson: Any) -> str | None:
    """Best-effort CRS detection from a GeoJSON dict (F-0014).

    Returns a canonical authority string (e.g. ``"EPSG:2180"``) or ``None`` when no
    CRS can be determined. shapely geometries carry no CRS, so a bare geometry always
    returns ``None`` — the caller must supply ``src_crs`` to :func:`to_analytical`.

    Detection order for a GeoJSON mapping:
      1. an explicit legacy ``crs`` member (named CRS / EPSG urn / OGC urn);
      2. a top-level ``"EPSG"`` / ``"epsg"`` integer hint;
      3. otherwise ``None`` (RFC 7946 implies 4326, but we do not *guess* — the
         caller decides whether to assume :data:`GEOJSON_DEFAULT_CRS`).
    """
    if isinstance(geom_or_geojson, BaseGeometry):
        return None
    if not isinstance(geom_or_geojson, dict):
        return None

    crs_member = geom_or_geojson.get("crs")
    if isinstance(crs_member, dict):
        props = crs_member.get("properties", {})
        name = props.get("name") or props.get("href")
        if isinstance(name, str):
            try:
                return _normalise_crs(_parse_crs_name(name))
            except (CRSError, ValueError):
                return None
    elif isinstance(crs_member, str):
        try:
            return _normalise_crs(crs_member)
        except (CRSError, ValueError):
            return None

    for key in ("EPSG", "epsg"):
        epsg = geom_or_geojson.get(key)
        if isinstance(epsg, int):
            try:
                return _normalise_crs(f"EPSG:{epsg}")
            except (CRSError, ValueError):
                return None
    return None


def _parse_crs_name(name: str) -> str:
    """Map a legacy GeoJSON CRS ``name``/``href`` to a pyproj-acceptable string.

    Handles ``urn:ogc:def:crs:EPSG::2180``, ``urn:ogc:def:crs:OGC:1.3:CRS84``,
    ``EPSG:2180`` and bare numeric codes.
    """
    upper = name.upper()
    if "CRS84" in upper:
        return GEOJSON_DEFAULT_CRS  # OGC:CRS84 == WGS84 lon/lat
    if "EPSG" in upper:
        # Take the trailing numeric token (handles "EPSG::2180" and "EPSG:2180").
        digits = "".join(ch if ch.isdigit() else " " for ch in upper.split("EPSG")[-1]).split()
        if digits:
            return f"EPSG:{digits[-1]}"
    return name


def _transformer(src: str | int | CRS, dst: str | int | CRS) -> Transformer:
    return Transformer.from_crs(
        CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True
    )


def transform(geom: BaseGeometry, src: str | int | CRS, dst: str | int | CRS) -> BaseGeometry:
    """Reproject *geom* from *src* to *dst* (no-op when the CRSs are equivalent).

    Uses ``always_xy=True`` so the transform is consistently (x, y) ordered. Returns a
    new shapely geometry; the input is left unchanged.
    """
    src_obj = CRS.from_user_input(src)
    dst_obj = CRS.from_user_input(dst)
    if src_obj.equals(dst_obj):
        return geom
    tr = Transformer.from_crs(src_obj, dst_obj, always_xy=True)
    # shapely.ops.transform passes coordinate arrays; Transformer.transform accepts
    # them and returns the projected arrays (verified above).
    return _shapely_transform(tr.transform, geom)


def to_analytical(geom: BaseGeometry, src_crs: str | int | CRS) -> BaseGeometry:
    """Project *geom* into the analytical CRS :data:`ANALYTICAL_CRS` (F-0015).

    This is the single entry point every metric operation should use to guarantee
    work happens in metres, not degrees (base_assumptions §9.4 / §26.3).
    """
    return transform(geom, src_crs, ANALYTICAL_CRS)
