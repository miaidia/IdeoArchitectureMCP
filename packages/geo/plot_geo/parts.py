"""Multipart geometry handling (F-0021/F-0022/F-0023).

  * :func:`is_multipart` / :func:`explode` — detect and split multipart geometry into
    its single-part components (F-0021).
  * :func:`merge_parcels` — union a set of parcel geometries into one investment-area
    geometry via ``shapely.unary_union`` (F-0022).
  * :func:`split_to_components` — split an analysis geometry back into its constituent
    polygons for per-component analysis (F-0023).

All inputs are assumed to already be in the analytical CRS (EPSG:2180); these are pure
topological operations and do not reproject.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import shapely
from shapely.geometry import GeometryCollection
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry


def is_multipart(geom: BaseGeometry) -> bool:
    """True if *geom* is a multi-* / collection with more than one part (F-0021)."""
    if isinstance(geom, BaseMultipartGeometry):
        return len(geom.geoms) > 1
    return False


def explode(geom: BaseGeometry) -> list[BaseGeometry]:
    """Flatten *geom* into a list of single-part geometries (F-0021).

    Recurses through nested ``GeometryCollection``s; empty inputs yield ``[]``; a
    single-part input yields ``[geom]``.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, BaseMultipartGeometry):
        out: list[BaseGeometry] = []
        for part in geom.geoms:
            out.extend(explode(part))
        return out
    return [geom]


def merge_parcels(geoms: Iterable[BaseGeometry]) -> BaseGeometry:
    """Union parcel geometries into a single investment-area geometry (F-0022).

    Uses ``shapely.unary_union`` (verified in
    ``.venv/lib/python3.12/site-packages/shapely/set_operations.py``), which dissolves
    shared/adjacent boundaries. Returns an empty ``GeometryCollection`` for no inputs.
    """
    parts = [g for g in geoms if g is not None and not g.is_empty]
    if not parts:
        return GeometryCollection()
    return shapely.unary_union(parts)


def split_to_components(geom: BaseGeometry) -> list[BaseGeometry]:
    """Split a (possibly merged) analysis geometry into components (F-0023).

    Equivalent to :func:`explode` but named for the analysis-decomposition use case
    (base_assumptions F-0023: "podział analizy na działki składowe").
    """
    return explode(geom)


def component_count(geom: BaseGeometry) -> int:
    """Number of single-part components in *geom* (0 for empty)."""
    return len(explode(geom))


def merge_with_components(
    geoms: Sequence[BaseGeometry],
) -> tuple[BaseGeometry, list[BaseGeometry]]:
    """Convenience: return ``(merged, components)`` in one call.

    ``components`` is the exploded view of the merged geometry, useful when a union of
    overlapping parcels collapses to fewer parts than the input list.
    """
    merged = merge_parcels(geoms)
    return merged, explode(merged)
