"""Typed layout proposal + constrained draw-DSL (Phase 4 §4.1.B.1; DSL v2 Phase 9 §9.1.1).

A :class:`LayoutProposal` is the model's candidate site layout, expressed as DATA, not
free pixels (Phase 4 §4.3 / NFR-SEC-003): either an explicit GeoJSON footprint polygon
OR a small constrained draw-DSL — a list of placed rectangles ``(x, y, w, h, rotation)``
with a setback offset — plus floors, program type, parking, and greenery polygons.

The DSL → shapely conversion is the ONLY way pixels enter the system, and it is fully
validated by Pydantic (extra fields forbidden, bounds enforced) so an out-of-range or
malformed proposal is rejected at parse time. ``footprint_geometry`` composes the DSL /
GeoJSON into a single shapely polygon for the validator + scorer.

Phase 9 adds the **masterplan DSL v2** (:class:`MasterplanProposal`): multiple
:class:`BuildingSpec` buildings made of :class:`BuildingSegment` wings (rectangles OR a
GeoJSON polygon — same either/or as ``LayoutProposal``), internal :class:`RoadElement`
roads, :class:`ParkingElement` parking, greenery / playground / retention polygons and a
``zabudowa_srodmiejska`` flag. Every ingested polygon is ``shapely.make_valid``-checked
with finite-coordinate + EPSG:2180 metric-plausibility bounds (reusing the
:class:`PlacedRectangle` bounds-check idiom); buildings-within-parcel is checked at
SCORING time (recorded as a violation), never hard-rejected at parse. ``parse_proposal``
discriminates: a payload with a ``buildings`` key (or ``schema_version == 2``) is a
:class:`MasterplanProposal`; anything else stays a byte-for-byte-compatible
:class:`LayoutProposal`.

``shape_class_for`` derives a coarse parcel shape class (aspect ratio / corner) used as
the exemplar-memory key (§4.1.B.5).
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely import get_coordinates
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from plot_agent.context import to_shapely

ProgramType = Literal[
    "single_family", "multifamily", "services", "warehouse", "garage", "mixed"
]

# DSL v2 vocabularies (plan Phase 9.1 item 1 — field names/Literals are normative).
SegmentUse = Literal[
    "mieszkalny", "uslugowy", "mieszkalno-uslugowy", "hotelowy", "garazowy", "techniczny"
]
GroundFloorUse = Literal["mieszkalny", "uslugowy", "garaz", "techniczny"]
BuildingStatus = Literal[
    "projektowany", "istniejacy", "w_budowie", "zrealizowany", "zabytek_do_remontu"
]
RoadFunction = Literal["kdw", "pozarowa", "pieszojezdnia", "dojscie"]
ParkingKind = Literal["naziemny", "hala_podziemna", "wbudowany"]

# EPSG:2180 metric plausibility for ingested GeoJSON (PlacedRectangle bounds-check idiom
# scaled to a masterplan site): a single proposal element may not span more than this
# many metres, and every coordinate must be a finite number within the EPSG:2180 numeric
# range. This rejects degenerate/degree-like or NaN/Inf geometry at parse time.
_MAX_EXTENT_M = 10_000.0
_MAX_ABS_COORD = 10_000_000.0


def _check_finite_metric(geom: BaseGeometry, *, what: str) -> None:
    """Finite-coordinate + metric-plausibility guard (Phase 9 §9.1.5 ingest validation)."""
    coords = get_coordinates(geom)
    if coords.size and not bool(np.isfinite(coords).all()):
        raise ValueError(f"{what}: non-finite coordinate in geometry")
    minx, miny, maxx, maxy = geom.bounds
    for v in (minx, miny, maxx, maxy):
        if not math.isfinite(v):
            raise ValueError(f"{what}: non-finite coordinate in geometry")
        if abs(v) > _MAX_ABS_COORD:
            raise ValueError(
                f"{what}: coordinate {v} outside the EPSG:2180 metric range "
                f"(|coord| <= {_MAX_ABS_COORD:g})"
            )
    if (maxx - minx) > _MAX_EXTENT_M or (maxy - miny) > _MAX_EXTENT_M:
        raise ValueError(
            f"{what}: extent {(maxx - minx):.0f}x{(maxy - miny):.0f} m exceeds the "
            f"plausible site size ({_MAX_EXTENT_M:g} m) — coordinates must be metres "
            "in the EPSG:2180 analytical frame"
        )


def ingest_geometry(geojson: dict[str, Any], *, what: str) -> BaseGeometry:
    """GeoJSON dict → validated shapely geometry (Phase 9 §9.1.5).

    Every polygon is ``shapely.make_valid``-checked (an invalid ring is repaired, an
    unrepairable/empty one rejected), coordinates must be finite and metrically
    plausible for EPSG:2180. This validates well-formedness ONLY — containment in the
    parcel/envelope is a SCORING-time check that records a violation (validate.py).
    """
    geom = to_shapely(geojson)
    _check_finite_metric(geom, what=what)
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.is_empty:
        raise ValueError(f"{what}: geometry is empty after make_valid")
    return geom


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
# Masterplan DSL v2 (Phase 9 §9.1.1) — multiple buildings/segments/roads/parking.
# --------------------------------------------------------------------------- #
class BuildingSegment(BaseModel):
    """One wing of an L/C/courtyard building (Phase 9 §9.1.1).

    Provide EITHER ``rectangles`` (the Phase 4 draw-DSL, reused) OR ``polygon`` (an
    explicit GeoJSON polygon) — the same either/or pattern as
    ``LayoutProposal.footprint``/``rectangles``. ``floors`` are kondygnacje nadziemne of
    this wing; ``ground_floor_use`` overrides the parter use (usługi w parterze).
    """

    model_config = ConfigDict(extra="forbid")

    rectangles: list[PlacedRectangle] = Field(
        default_factory=list, description="Draw-DSL placed rectangles composing this wing."
    )
    polygon: dict[str, Any] | None = Field(
        default=None, description="Explicit GeoJSON polygon (alternative to rectangles)."
    )
    floors: int = Field(ge=1, le=60, description="Kondygnacje nadziemne of this wing.")
    use: SegmentUse = Field(description="Dominant use of this wing.")
    ground_floor_use: GroundFloorUse | None = Field(
        default=None,
        description="Parter use when it differs from `use` (e.g. usługi w parterze).",
    )

    @model_validator(mode="after")
    def _validate_geometry(self) -> BuildingSegment:
        if self.polygon is None and not self.rectangles:
            raise ValueError("BuildingSegment needs either rectangles or a GeoJSON polygon.")
        # Ingest validation runs ONCE at parse time (make_valid / finite / metric bounds).
        self.geometry()
        return self

    def geometry(self) -> BaseGeometry:
        """Compose the wing footprint (GeoJSON polygon OR draw-DSL rectangles)."""
        if self.polygon is not None:
            return ingest_geometry(self.polygon, what="BuildingSegment.polygon")
        geom = unary_union([r.to_polygon() for r in self.rectangles])
        _check_finite_metric(geom, what="BuildingSegment.rectangles")
        return geom

    def area_m2(self) -> float:
        return float(self.geometry().area)


class BuildingSpec(BaseModel):
    """One building of a masterplan (Phase 9 §9.1.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description='Building name, e.g. "Budynek 1".')
    segments: list[BuildingSegment] = Field(
        min_length=1, description="Wings/segments composing the building."
    )
    stage: int | None = Field(default=None, ge=1, description="Etap realizacji.")
    status: BuildingStatus = Field(
        default="projektowany",
        description="projektowany | istniejacy | w_budowie | zrealizowany | zabytek_do_remontu.",
    )
    underground_floors: int = Field(
        default=0, ge=0, le=10, description="Hala garażowa levels (kondygnacje podziemne)."
    )

    def footprint_geometry(self) -> BaseGeometry:
        """Union of all segment footprints (powierzchnia zabudowy geometry)."""
        return unary_union([s.geometry() for s in self.segments])

    def footprint_area_m2(self) -> float:
        return float(self.footprint_geometry().area)


class RoadElement(BaseModel):
    """An internal road: KDW / droga pożarowa / pieszojezdnia / dojście (Phase 9 §9.1.1)."""

    model_config = ConfigDict(extra="forbid")

    centerline: dict[str, Any] = Field(description="GeoJSON LineString centerline (EPSG:2180).")
    width_m: float = Field(gt=0, le=30, description="Road width in metres.")
    function: RoadFunction = Field(description="kdw | pozarowa | pieszojezdnia | dojscie.")

    @model_validator(mode="after")
    def _validate_geometry(self) -> RoadElement:
        self.centerline_geometry()
        return self

    def centerline_geometry(self) -> BaseGeometry:
        geom = to_shapely(self.centerline)
        _check_finite_metric(geom, what="RoadElement.centerline")
        if geom.is_empty or geom.length <= 0:
            raise ValueError("RoadElement.centerline: empty/zero-length centerline")
        return geom

    def to_polygon(self) -> BaseGeometry:
        """Road corridor polygon = centerline buffered by half the width (render/metrics)."""
        return self.centerline_geometry().buffer(self.width_m / 2.0, cap_style="flat")


class ParkingElement(BaseModel):
    """A parking element: surface lot, underground hall or built-in (Phase 9 §9.1.1)."""

    model_config = ConfigDict(extra="forbid")

    kind: ParkingKind = Field(description="naziemny | hala_podziemna | wbudowany.")
    polygon: dict[str, Any] = Field(description="GeoJSON polygon of the parking element.")
    spaces: int = Field(ge=0, description="Number of parking spaces provided.")
    serves_buildings: list[str] = Field(
        default_factory=list, description="Names of the buildings this parking serves."
    )

    @model_validator(mode="after")
    def _validate_geometry(self) -> ParkingElement:
        self.geometry()
        return self

    def geometry(self) -> BaseGeometry:
        return ingest_geometry(self.polygon, what="ParkingElement.polygon")


class MasterplanProposal(BaseModel):
    """A typed multi-building masterplan proposal (Phase 9 §9.1.1, DSL v2).

    Discrimination from the v1 :class:`LayoutProposal`: the presence of the
    ``buildings`` key (and/or ``schema_version: 2``) — see :func:`parse_proposal`.
    All polygons are ingest-validated at parse time (make_valid / finite coords /
    EPSG:2180 metric plausibility); containment in parcel/envelope is recorded as a
    scoring-time violation, consistent with ``validate.py``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = Field(
        default=2, description="Proposal schema version (2 = masterplan DSL v2)."
    )
    buildings: list[BuildingSpec] = Field(min_length=1, description="Buildings of the plan.")
    roads: list[RoadElement] = Field(
        default_factory=list, description="Internal roads (KDW, droga pożarowa, dojścia)."
    )
    parking: list[ParkingElement] = Field(
        default_factory=list, description="Parking elements (naziemne/hale/wbudowane)."
    )
    greenery_polygons: list[dict[str, Any]] = Field(
        default_factory=list, description="GeoJSON polygons of greenery (PBC, §8.5)."
    )
    playgrounds: list[dict[str, Any]] = Field(
        default_factory=list, description="GeoJSON polygons of place zabaw (WT §40)."
    )
    retention: list[dict[str, Any]] = Field(
        default_factory=list, description="GeoJSON polygons of retention (§8.5 bilans retencji)."
    )
    zabudowa_srodmiejska: bool = Field(
        default=False, description="Śródmiejska flag (drives §13/§60/§40 reductions)."
    )

    @model_validator(mode="after")
    def _validate_geometry(self) -> MasterplanProposal:
        # Buildings/roads/parking validate themselves; validate the loose polygon lists
        # AND persist the repaired canonical mapping back onto the model (design choice
        # documented here): a make_valid repair must never be discarded after parse —
        # every consumer of these lists (``greenery_geometry``/``playgrounds_geometry``,
        # the renderer reading the raw dicts, the variant store, the audit record) sees
        # the REPAIRED geometry, so an accepted bowtie ring keeps its area and can never
        # crash downstream set operations (GEOSException) on geometry the parser took.
        for label, polys in (
            ("greenery_polygons", self.greenery_polygons),
            ("playgrounds", self.playgrounds),
            ("retention", self.retention),
        ):
            for i, p in enumerate(polys):
                geom = ingest_geometry(p, what=f"MasterplanProposal.{label}[{i}]")
                polys[i] = dict(mapping(geom))
        return self

    # ------------------------------------------------------------------ #
    # Composition helpers (renderer / metrics / validators)
    # ------------------------------------------------------------------ #
    def buildings_footprint_geometry(self) -> BaseGeometry:
        """Union of ALL building footprints (powierzchnia zabudowy geometry)."""
        return unary_union([b.footprint_geometry() for b in self.buildings])

    def proposed_footprint_geometry(self) -> BaseGeometry:
        """Union of NEW (projektowany / w_budowie) building footprints — the geometry the
        hard envelope/no-build guard applies to (existing buildings already stand)."""
        geoms = [
            b.footprint_geometry()
            for b in self.buildings
            if b.status in ("projektowany", "w_budowie")
        ]
        return unary_union(geoms) if geoms else Polygon()

    def greenery_geometry(self) -> BaseGeometry:
        # ingest_geometry (not plain to_shapely): the stored dicts are already repaired
        # by the validator, but route through the repair anyway so the helper stays safe
        # even for instances built without validation (e.g. ``model_construct``).
        if not self.greenery_polygons:
            return Polygon()
        return unary_union(
            [
                ingest_geometry(g, what=f"MasterplanProposal.greenery_polygons[{i}]")
                for i, g in enumerate(self.greenery_polygons)
            ]
        )

    def playgrounds_geometry(self) -> BaseGeometry:
        if not self.playgrounds:
            return Polygon()
        return unary_union(
            [
                ingest_geometry(g, what=f"MasterplanProposal.playgrounds[{i}]")
                for i, g in enumerate(self.playgrounds)
            ]
        )

    def greenery_area_m2(self) -> float:
        return float(self.greenery_geometry().area)


def parse_proposal(payload: dict[str, Any]) -> LayoutProposal | MasterplanProposal:
    """Discriminate + parse a ``propose_layout`` payload (Phase 9 §9.1.1).

    A payload carrying a ``buildings`` key OR ``schema_version == 2`` is parsed as a
    :class:`MasterplanProposal`; anything else takes the UNCHANGED v1
    :class:`LayoutProposal` path (old payloads keep working byte-for-byte).
    """
    if "buildings" in payload or payload.get("schema_version") == 2:
        return MasterplanProposal.model_validate(payload)
    return LayoutProposal.model_validate(payload)


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
