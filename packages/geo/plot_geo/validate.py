"""Topology validation, repair, and uncertainty flagging (F-0016/F-0017/F-0018).

``repair`` is built on ``shapely.make_valid`` (verified signature
``make_valid(geometry, *, method='linework', keep_collapsed=True)`` in
``.venv/lib/python3.12/site-packages/shapely/constructive.py``) and is **idempotent**:
``repair(repair(g))`` is geometrically equal to ``repair(g)`` because a geometry that
is already valid is returned by ``make_valid`` essentially unchanged. We additionally
normalise the result so repeated calls are byte-stable for hashing.

``assess_quality`` produces a :class:`QualityReport` carrying any
:class:`UncertainGeometryFlag` values, so the caller can surface "uncertain geometry"
in confidence-first UX (base_assumptions §9.4) without hard-failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import shapely
from shapely.geometry.base import BaseGeometry

# Heuristic thresholds (metres / m², EPSG:2180). Conservative defaults that flag
# obviously-degenerate cadastral geometry without rejecting valid small parcels.
_SLIVER_MIN_AREA_M2 = 1.0
_SLIVER_THINNESS = 0.02  # Polsby-Popper-style 4πA/P² below this == sliver-like.
_TINY_AREA_M2 = 0.5
_HIGH_VERTEX_COUNT = 2000


class UncertainGeometryFlag(str, Enum):
    """Reasons a geometry is flagged uncertain (F-0018)."""

    EMPTY = "empty"
    INVALID_TOPOLOGY = "invalid_topology"
    SELF_INTERSECTION = "self_intersection"
    SLIVER = "sliver"
    TINY_AREA = "tiny_area"
    SUSPICIOUS_VERTEX_COUNT = "suspicious_vertex_count"
    NON_POLYGONAL = "non_polygonal"
    REPAIRED = "repaired"


@dataclass(frozen=True)
class QualityReport:
    """Immutable summary of geometry quality (F-0016/F-0018)."""

    is_valid: bool
    flags: tuple[UncertainGeometryFlag, ...] = field(default_factory=tuple)
    area_m2: float = 0.0
    perimeter_m: float = 0.0
    vertex_count: int = 0
    explanation: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_uncertain(self) -> bool:
        return bool(self.flags)


def is_valid(geom: BaseGeometry) -> bool:
    """True if *geom* is topologically valid (OGC simple-features sense, F-0016)."""
    return bool(shapely.is_valid(geom))


def explain_invalidity(geom: BaseGeometry) -> str | None:
    """Human-readable reason a geometry is invalid, or ``None`` if it is valid."""
    reason = shapely.is_valid_reason(geom)
    return None if reason == "Valid Geometry" else reason


def repair(geom: BaseGeometry) -> BaseGeometry:
    """Repair simple geometry errors with ``make_valid`` (F-0017), idempotently.

    A geometry that is already valid is returned normalised (so two repairs are
    byte-identical, which keeps :func:`plot_geo.snapshot.input_hash` stable). An
    invalid geometry is passed through ``make_valid`` (linework method) and then
    normalised.
    """
    if geom.is_empty:
        return shapely.normalize(geom)
    if shapely.is_valid(geom):
        return shapely.normalize(geom)
    fixed = shapely.make_valid(geom)
    return shapely.normalize(fixed)


def _vertex_count(geom: BaseGeometry) -> int:
    # shapely.get_num_coordinates counts all coordinates across all parts/rings.
    return int(shapely.get_num_coordinates(geom))


def assess_quality(geom: BaseGeometry) -> QualityReport:
    """Flag slivers, self-intersections, tiny areas, and suspicious vertex counts.

    Implements F-0016 (validation), F-0018 (uncertainty flagging). Pure inspection —
    it never mutates *geom*; call :func:`repair` separately to fix issues.
    """
    flags: list[UncertainGeometryFlag] = []
    notes: list[str] = []

    if geom.is_empty:
        return QualityReport(
            is_valid=False,
            flags=(UncertainGeometryFlag.EMPTY,),
            explanation=("Geometry is empty.",),
        )

    valid = bool(shapely.is_valid(geom))
    if not valid:
        flags.append(UncertainGeometryFlag.INVALID_TOPOLOGY)
        reason = shapely.is_valid_reason(geom)
        notes.append(f"Invalid topology: {reason}")
        if "self-intersection" in reason.lower() or "self intersection" in reason.lower():
            flags.append(UncertainGeometryFlag.SELF_INTERSECTION)

    area = float(geom.area)
    perimeter = float(geom.length)
    vcount = _vertex_count(geom)

    is_polygonal = geom.geom_type in ("Polygon", "MultiPolygon")
    if not is_polygonal:
        flags.append(UncertainGeometryFlag.NON_POLYGONAL)
        notes.append(f"Non-polygonal geometry type: {geom.geom_type}.")

    if is_polygonal and 0.0 < area < _TINY_AREA_M2:
        flags.append(UncertainGeometryFlag.TINY_AREA)
        notes.append(f"Tiny area {area:.4f} m² < {_TINY_AREA_M2} m².")

    # Sliver: small AND very thin relative to its perimeter (low Polsby-Popper).
    if is_polygonal and area > 0.0 and perimeter > 0.0:
        thinness = (4.0 * 3.141592653589793 * area) / (perimeter * perimeter)
        if area < _SLIVER_MIN_AREA_M2 and thinness < _SLIVER_THINNESS:
            flags.append(UncertainGeometryFlag.SLIVER)
            notes.append(
                f"Sliver-like: area {area:.4f} m², thinness {thinness:.4f} < {_SLIVER_THINNESS}."
            )

    if vcount > _HIGH_VERTEX_COUNT:
        flags.append(UncertainGeometryFlag.SUSPICIOUS_VERTEX_COUNT)
        notes.append(f"Suspicious vertex count {vcount} > {_HIGH_VERTEX_COUNT}.")

    # De-duplicate while preserving order.
    seen: set[UncertainGeometryFlag] = set()
    unique_list: list[UncertainGeometryFlag] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            unique_list.append(f)
    unique_flags = tuple(unique_list)

    return QualityReport(
        is_valid=valid,
        flags=unique_flags,
        area_m2=area,
        perimeter_m=perimeter,
        vertex_count=vcount,
        explanation=tuple(notes),
    )
