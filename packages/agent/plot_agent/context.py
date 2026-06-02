"""Shared analysis context for both Phase 4 loops (IMPLEMENTATION_PLAN.md §4.1).

An :class:`AnalysisContext` bundles the geometry + rules a loop needs to score a
parcel or a drawing proposal:

* the parcel polygon (analytical EPSG:2180 metres),
* the buildable-envelope polygon (where you may build, after setbacks/no-build),
* hard constraints / no-build zones (a footprint may NEVER intersect these — §14.2),
* soft constraints (penalised, not forbidden),
* the loaded :class:`plot_rules.RulesetRegistry` (content-hashed ``ruleset_version``).

Geometry is accepted as shapely geometries OR GeoJSON dicts and normalised to shapely
(reusing the same coercion idea as ``plot_reports.render.layer``). Nothing here imports
matplotlib or plot_connectors (Phase 4 §4.4 decoupling).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plot_rules import RulesetRegistry, load_rulesets
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

# A GeoJSON geometry is a plain dict; accept either that or a shapely geometry.
type GeometryLike = BaseGeometry | dict[str, Any]


def to_shapely(geom: GeometryLike) -> BaseGeometry:
    """Coerce a shapely geometry or GeoJSON mapping into a shapely geometry.

    Mirrors ``plot_reports.render.layer._to_shapely`` (documented GeoJSON → shapely
    entry point ``shapely.geometry.shape``) but is kept local so plot_agent does not
    depend on a private helper.
    """
    if isinstance(geom, BaseGeometry):
        return geom
    if isinstance(geom, dict):
        if geom.get("type") == "Feature":
            return shape(geom["geometry"])
        return shape(geom)
    raise TypeError(f"Unsupported geometry type: {type(geom)!r}")


def _coerce_list(geoms: list[GeometryLike]) -> list[BaseGeometry]:
    return [to_shapely(g) for g in geoms]


@dataclass
class AnalysisContext:
    """Geometry + rules a Phase 4 loop scores against (§4.1).

    Coordinates are plain metres in the analytical EPSG:2180 frame (their absolute
    location is irrelevant; only shape + relative layout matter, as in the Phase 3
    sample preview). ``ruleset`` is content-hashed so the self-improve loop can detect
    that an edited legal value changed the version (it re-loads, never mutates — §12).
    """

    parcel: GeometryLike
    buildable_envelope: GeometryLike
    hard_constraints: list[GeometryLike] = field(default_factory=list)
    soft_constraints: list[GeometryLike] = field(default_factory=list)
    ruleset: RulesetRegistry = field(default_factory=lambda: RulesetRegistry())
    analysis_mode: str = "design_feasibility"
    investment_goal: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Geometry accessors (lazy shapely coercion)
    # ------------------------------------------------------------------ #
    def parcel_geom(self) -> BaseGeometry:
        return to_shapely(self.parcel)

    def envelope_geom(self) -> BaseGeometry:
        return to_shapely(self.buildable_envelope)

    def hard_geoms(self) -> list[BaseGeometry]:
        return _coerce_list(self.hard_constraints)

    def soft_geoms(self) -> list[BaseGeometry]:
        return _coerce_list(self.soft_constraints)

    def parcel_area_m2(self) -> float:
        return float(self.parcel_geom().area)

    def envelope_area_m2(self) -> float:
        return float(self.envelope_geom().area)

    @classmethod
    def with_loaded_rules(
        cls,
        *,
        parcel: GeometryLike,
        buildable_envelope: GeometryLike,
        ruleset_dir: str = "rulesets/PL",
        hard_constraints: list[GeometryLike] | None = None,
        soft_constraints: list[GeometryLike] | None = None,
        analysis_mode: str = "design_feasibility",
        investment_goal: dict[str, Any] | None = None,
    ) -> AnalysisContext:
        """Build a context, loading the ruleset fresh from disk (Phase 2 hot-reload).

        Loading fresh (no cross-call cache) is what lets the self-improve loop observe
        an edited ruleset value on the next run (F-0133 / F-0440).
        """
        return cls(
            parcel=parcel,
            buildable_envelope=buildable_envelope,
            hard_constraints=hard_constraints or [],
            soft_constraints=soft_constraints or [],
            ruleset=load_rulesets(ruleset_dir),
            analysis_mode=analysis_mode,
            investment_goal=investment_goal or {},
        )
