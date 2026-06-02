"""Layer abstraction for the deterministic map renderer (Phase 3 §3.1).

A :class:`Layer` carries a name, one-or-more geometries (shapely geoms OR GeoJSON
dicts — deliberately NOT tied to ``plot_geo``, which does not exist yet, Phase 3
§3.1), a semantic *role* that drives deterministic styling, optional per-layer style
overrides, and optional *area attribution* (which constraint removed how many m² /
what %, §30 / §17). The renderer reads all of this; nothing here imports matplotlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

# A GeoJSON geometry is a plain dict; we accept either that or a shapely geometry.
# PEP 695 type alias (Py3.12) so mypy/ruff treat it as a first-class type alias.
type GeometryLike = BaseGeometry | dict[str, Any]


class LayerRole(str, Enum):
    """Semantic role of a layer — drives deterministic colour + z-order (Phase 3 §3.4).

    Values are stable strings so they serialise predictably into style metadata
    (NFR-AUD-009) and golden snapshots.
    """

    PARCEL = "parcel"
    BUILDABLE_ENVELOPE = "buildable_envelope"
    NO_BUILD = "no_build"
    CONSTRAINT_HARD = "constraint_hard"
    CONSTRAINT_SOFT = "constraint_soft"
    NETWORK = "network"
    OTHER = "other"


def _to_shapely(geom: GeometryLike) -> BaseGeometry:
    """Coerce a shapely geometry or a GeoJSON mapping into a shapely geometry.

    Uses :func:`shapely.geometry.shape` for GeoJSON dicts (the documented GeoJSON →
    shapely entry point). Shapely geometries pass through unchanged.
    """
    if isinstance(geom, BaseGeometry):
        return geom
    if isinstance(geom, dict):
        # A GeoJSON Feature wraps the geometry under "geometry"; unwrap it.
        if geom.get("type") == "Feature":
            return shape(geom["geometry"])
        return shape(geom)
    raise TypeError(f"Unsupported geometry type: {type(geom)!r}")


@dataclass
class Layer:
    """A styled, renderable layer of geometries (Phase 3 §3.1).

    Parameters
    ----------
    name:
        Human-readable layer name (appears in the legend + style metadata).
    geometries:
        One or more shapely geometries or GeoJSON dicts. A single geometry is
        accepted for convenience and normalised to a list.
    role:
        :class:`LayerRole` controlling deterministic colour + z-order.
    style:
        Optional per-layer style overrides merged over the role defaults
        (e.g. ``{"facecolor": "#aabbcc", "alpha": 0.4}``).
    removed_area_m2 / removed_area_percent:
        Area attribution for the caption / legend (§30 "which constraint removed
        which area"). For Phase 3 these are *input* data on the layer; Phase 7/9
        supply the real numbers.
    attribution_label:
        Optional explicit label for the attribution caption line; defaults to ``name``.
    """

    name: str
    geometries: list[GeometryLike] = field(default_factory=list)
    role: LayerRole = LayerRole.OTHER
    style: dict[str, Any] = field(default_factory=dict)
    removed_area_m2: float | None = None
    removed_area_percent: float | None = None
    attribution_label: str | None = None

    def __post_init__(self) -> None:
        # Accept a single geometry (shapely or GeoJSON) and normalise to a list.
        if isinstance(self.geometries, BaseGeometry | dict):
            self.geometries = [self.geometries]

    def shapely_geometries(self, *, simplify_tolerance: float | None = None) -> list[BaseGeometry]:
        """Return geometries as shapely objects, optionally simplified for preview.

        ``simplify_tolerance`` applies topology-preserving simplification for previews
        (F-0514 / §26.3 / NFR-PERF-012) while analysis keeps full precision elsewhere.
        ``None`` (default) means no simplification.
        """
        geoms = [_to_shapely(g) for g in self.geometries]
        if simplify_tolerance is not None and simplify_tolerance > 0:
            geoms = [g.simplify(simplify_tolerance, preserve_topology=True) for g in geoms]
        return geoms

    def is_hard_constraint(self) -> bool:
        """Whether this layer is a *hard* constraint (legend distinction, §30)."""
        return self.role in (LayerRole.CONSTRAINT_HARD, LayerRole.NO_BUILD)

    def has_attribution(self) -> bool:
        """Whether this layer carries area-attribution data for the caption (§30)."""
        return self.removed_area_m2 is not None or self.removed_area_percent is not None
