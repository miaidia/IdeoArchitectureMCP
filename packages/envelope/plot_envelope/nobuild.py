"""No-build zones from hard constraints + statutory setbacks (Phase 7 §7.1.2).

A :class:`~plot_domain.NoBuildZone` is an excluded area inside the parcel: building may
NOT be placed there. v1 sources two kinds of no-build zone:

1. **Hard constraints** — the geometry of each hard :class:`~plot_domain.Constraint`
   (flood hazard, landslide, …) is excluded outright.
2. **Statutory boundary setback** — a building must keep a minimum distance from the
   parcel boundary (WT setback, ``rulesets/PL/building-technical/setback-granica.yaml``).
   The no-build ring is ``parcel − parcel.buffer(-setback)`` (an inward erosion), computed
   in metres via :func:`plot_geo.buffer_m` (never degrees).

Each zone records the constraint / rule that produced it (``source_constraint_id`` /
``reason``) for the area-attribution trace (§30). This module consumes geometry only — no
network, no ``plot_connectors`` import (§9.4).
"""

from __future__ import annotations

import uuid

import plot_geo
from plot_domain import Constraint, NoBuildZone
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from plot_envelope.overlay import hard_constraints


def _constraint_geom(constraint: Constraint) -> BaseGeometry | None:
    if constraint.geometry is None:
        return None
    geom = shape(constraint.geometry)
    return None if geom.is_empty else geom


def setback_no_build_zone(
    parcel_geom: BaseGeometry,
    *,
    setback_m: float,
    rule_id: str | None,
    rule_version: str | None = None,
) -> NoBuildZone | None:
    """The inward boundary-setback ring as a no-build zone (§7.1.2).

    ``parcel − erode(parcel, setback_m)``. Returns ``None`` for a non-positive setback or
    when the erosion swallows the whole parcel (then the *whole* parcel is effectively
    no-build, which the envelope step surfaces as a near-zero buildable area).
    """
    if setback_m <= 0.0:
        return None
    inner = plot_geo.buffer_m(parcel_geom, -abs(setback_m))
    ring = plot_geo.difference(parcel_geom, inner)
    if ring.is_empty:
        return None
    area = float(ring.area)
    return NoBuildZone(
        id=f"nbz:setback:{uuid.uuid4().hex[:8]}",
        reason=f"statutory_boundary_setback_{setback_m:g}m",
        geometry=mapping(ring),
        area_m2=round(area, 3),
        source_constraint_id=rule_id,
    )


def constraint_no_build_zone(parcel_geom: BaseGeometry, constraint: Constraint) -> NoBuildZone | None:
    """A no-build zone from one hard constraint, clipped to the parcel (§7.1.2)."""
    geom = _constraint_geom(constraint)
    if geom is None:
        return None
    clipped = plot_geo.intersection(geom, parcel_geom)
    if clipped.is_empty:
        return None
    return NoBuildZone(
        id=f"nbz:{constraint.constraint_type}:{uuid.uuid4().hex[:8]}",
        reason=f"hard_constraint_{constraint.constraint_type}",
        geometry=mapping(clipped),
        area_m2=round(float(clipped.area), 3),
        source_constraint_id=constraint.constraint_id,
    )


def no_build_zones(
    parcel_geom: BaseGeometry,
    constraints: list[Constraint],
    *,
    boundary_setback_m: float = 0.0,
    setback_rule_id: str | None = None,
    setback_rule_version: str | None = None,
) -> list[NoBuildZone]:
    """All no-build zones for a parcel: hard constraints + statutory boundary setback.

    Soft constraints are intentionally NOT excluded here (they are penalised in scoring /
    flagged as risks, not subtracted — §7.1). ``boundary_setback_m`` is the resolved WT
    setback (the orchestrator reads it from the ruleset); when 0 it is omitted.
    """
    zones: list[NoBuildZone] = []
    for con in hard_constraints(constraints):
        zone = constraint_no_build_zone(parcel_geom, con)
        if zone is not None:
            zones.append(zone)
    setback_zone = setback_no_build_zone(
        parcel_geom,
        setback_m=boundary_setback_m,
        rule_id=setback_rule_id,
        rule_version=setback_rule_version,
    )
    if setback_zone is not None:
        zones.append(setback_zone)
    return zones
