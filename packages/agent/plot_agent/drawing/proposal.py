"""Typed layout proposal + constrained draw-DSL (Phase 4 §4.1.B.1).

A :class:`LayoutProposal` is the model's candidate site layout, expressed as DATA, not
free pixels (Phase 4 §4.3 / NFR-SEC-003): either an explicit GeoJSON footprint polygon
OR a small constrained draw-DSL — a list of placed rectangles ``(x, y, w, h, rotation)``
with a setback offset — plus floors, program type, parking, and greenery polygons.

The DSL → shapely conversion is the ONLY way pixels enter the system, and it is fully
validated by Pydantic (extra fields forbidden, bounds enforced) so an out-of-range or
malformed proposal is rejected at parse time. ``footprint_geometry`` composes the DSL /
GeoJSON into a single shapely polygon for the validator + scorer.

``shape_class_for`` derives a coarse parcel shape class (aspect ratio / corner) used as
the exemplar-memory key (§4.1.B.5).
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from plot_agent.context import to_shapely

ProgramType = Literal[
    "single_family", "multifamily", "services", "warehouse", "garage", "mixed"
]


class PlacedRectangle(BaseModel):
    """One placed rectangle in the draw-DSL (Phase 4 §4.1.B.1).

    ``x, y`` is the lower-left corner (metres, EPSG:2180 frame), ``w, h`` the width/height
    (metres), ``rotation`` degrees CCW about the rectangle centre, and ``setback`` an
    inward shrink (metres) applied as a negative buffer to model a clearance offset.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(description="Lower-left X (metres).")
    y: float = Field(description="Lower-left Y (metres).")
    w: float = Field(gt=0, le=1000, description="Width (metres).")
    h: float = Field(gt=0, le=1000, description="Height (metres).")
    rotation: float = Field(default=0.0, ge=-360, le=360, description="Rotation degrees CCW.")
    setback: float = Field(default=0.0, ge=0, le=100, description="Inward setback offset (metres).")

    def to_polygon(self) -> Polygon:
        """Convert this rectangle to a shapely polygon (DSL → geometry)."""
        rect: BaseGeometry = Polygon(
            [(0, 0), (self.w, 0), (self.w, self.h), (0, self.h)]
        )
        if self.rotation:
            rect = rotate(rect, self.rotation, origin="centroid", use_radians=False)
        rect = translate(rect, xoff=self.x, yoff=self.y)
        if self.setback > 0:
            shrunk = rect.buffer(-self.setback)
            # If the setback collapses the rectangle, fall back to the un-shrunk shape so
            # the proposal is still a valid (if tiny) polygon the validator can reject.
            if not shrunk.is_empty and shrunk.area > 0:
                rect = shrunk
        return rect if isinstance(rect, Polygon) else Polygon(rect.exterior)


class LayoutProposal(BaseModel):
    """A typed, validated architect layout proposal (Phase 4 §4.1.B.1).

    Provide EITHER ``footprint`` (a GeoJSON polygon dict) OR ``rectangles`` (the draw-DSL).
    ``footprint_geometry`` returns the composed shapely footprint either way.
    """

    model_config = ConfigDict(extra="forbid")

    program_type: ProgramType = Field(description="Building program (§8.4 variants).")
    footprint: dict[str, Any] | None = Field(
        default=None, description="Explicit GeoJSON polygon footprint (alternative to rectangles)."
    )
    rectangles: list[PlacedRectangle] = Field(
        default_factory=list, description="Draw-DSL placed rectangles composing the footprint."
    )
    floors: int = Field(default=1, ge=1, le=60, description="Number of above-ground floors.")
    parking_count: int = Field(default=0, ge=0, description="Proposed parking spaces.")
    greenery_polygons: list[dict[str, Any]] = Field(
        default_factory=list, description="GeoJSON polygons of proposed greenery (PBC, §8.5)."
    )

    def footprint_geometry(self) -> BaseGeometry:
        """Compose the proposal footprint into a single shapely polygon/multipolygon.

        Raises ``ValueError`` if neither a GeoJSON footprint nor rectangles are supplied —
        an empty proposal is not a valid drawing.
        """
        if self.footprint is not None:
            geom = self.footprint
            if geom.get("type") == "Feature":
                return shape(geom["geometry"])
            return shape(geom)
        if self.rectangles:
            polys = [r.to_polygon() for r in self.rectangles]
            return unary_union(polys)
        raise ValueError("LayoutProposal has neither a GeoJSON footprint nor draw-DSL rectangles.")

    def footprint_area_m2(self) -> float:
        return float(self.footprint_geometry().area)

    def greenery_area_m2(self) -> float:
        if not self.greenery_polygons:
            return 0.0
        geoms = [to_shapely(g) for g in self.greenery_polygons]
        return float(unary_union(geoms).area)


# --------------------------------------------------------------------------- #
# Parcel shape classification (exemplar-memory key, §4.1.B.5)
# --------------------------------------------------------------------------- #
def shape_class_for(parcel: BaseGeometry) -> str:
    """Derive a coarse parcel shape class from simple metrics (aspect ratio / corner).

    Buckets (deliberately coarse so similar parcels collide on the same key):

    * ``corner`` — convex-deficit suggests an L / re-entrant corner shape.
    * ``narrow`` — bounding-box aspect ratio >= 2.5.
    * ``square`` — aspect ratio < 1.4.
    * ``rectangular`` — everything else.
    """
    if parcel.is_empty or parcel.area <= 0:
        return "degenerate"
    minx, miny, maxx, maxy = parcel.bounds
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return "degenerate"
    aspect = max(w, h) / min(w, h)
    # Convexity deficit: how much smaller the parcel is than its convex hull.
    hull_area = parcel.convex_hull.area
    convexity = parcel.area / hull_area if hull_area > 0 else 1.0
    if convexity < 0.92:
        return "corner"
    if aspect >= 2.5:
        return "narrow"
    if aspect < 1.4:
        return "square"
    return "rectangular"


def aspect_ratio(parcel: BaseGeometry) -> float:
    minx, miny, maxx, maxy = parcel.bounds
    w, h = maxx - minx, maxy - miny
    if w <= 0 or h <= 0:
        return math.inf
    return max(w, h) / min(w, h)
