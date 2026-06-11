"""Typed geometric context for the Phase 10 inter-building validators.

Builds, ONCE per proposal, everything the validators consume:

* **wall-plane decomposition** (WT §12, Dz.U. 2024/726: każda płaszczyzna
  powstała w wyniku załamania lub uskoku ściany = oddzielna ściana): each
  building segment's composed polygon is canonically oriented
  (:func:`shapely.geometry.polygon.orient`, exterior CCW / interiors CW) and its
  consecutive ring edges become indexed wall planes with outward normals. Edge
  indices are what ``BuildingSegment.windowed_walls`` refers to. Walls whose
  outward-offset midpoint falls INSIDE the building footprint (edges shared with
  a sibling segment) are excluded from validation — they are internal planes,
  not external walls. The midpoint-only test is a documented simplification
  (a wall partially covered by a sibling wing is kept whole, not re-split).
* **obstructor parts** — every segment footprint extruded to its own height
  (floors × ``floor_height_m`` config, ``basis: industry_heuristic``) plus the
  optional neighbor buildings (geometry + explicit height) from BDOT10k or user
  input (live fetch is Phase 12; here they arrive as a typed list).

The module is duck-typed against :class:`plot_agent.drawing.proposal
.MasterplanProposal` via structural protocols (dependency direction §9.4:
``plot_planning`` never imports ``plot_agent``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from plot_planning.wt_validators.config import ValidatorConfig

# Geometric epsilons (numerical precision only — not legal values).
_MIN_EDGE_LEN_M = 1e-6
_NORMAL_PROBE_M = 0.05

#: Uses mapped to "budynek mieszkalny wielorodzinny" for §12/§39/§40/§60
#: (mieszkalno-usługowy = dominant residential → wielorodzinny; documented
#: simplification, basis: simplified_classification).
RESIDENTIAL_USES: tuple[str, ...] = ("mieszkalny", "mieszkalno-uslugowy")
#: Uses whose rooms count as pomieszczenia przeznaczone na stały pobyt ludzi
#: (§13/§19 window protection); garaż/techniczny have no such rooms.
STALY_POBYT_USES: tuple[str, ...] = (
    "mieszkalny",
    "mieszkalno-uslugowy",
    "uslugowy",
    "hotelowy",
)
#: WT §209 simplified ZL mapping (basis: simplified_classification — documented
#: in plan §10.1.6): mieszkalny/mieszkalno-usługowy → ZL IV, hotelowy → ZL V,
#: usługowy → ZL III; garażowy/techniczny → PM (Q from ValidatorConfig).
ZL_BY_USE: dict[str, str] = {
    "mieszkalny": "ZL IV",
    "mieszkalno-uslugowy": "ZL IV",
    "hotelowy": "ZL V",
    "uslugowy": "ZL III",
}
#: Building statuses that make a building NEW (the investment under assessment).
NEW_STATUSES: tuple[str, ...] = ("projektowany", "w_budowie")


# --------------------------------------------------------------------------- #
# Structural protocols (satisfied by plot_agent.drawing.proposal DSL v2 models)
# --------------------------------------------------------------------------- #
class SegmentLike(Protocol):
    @property
    def floors(self) -> int: ...

    @property
    def use(self) -> str: ...

    @property
    def ground_floor_use(self) -> str | None: ...

    @property
    def windowed_walls(self) -> list[int] | None: ...

    def geometry(self) -> BaseGeometry: ...


class BuildingLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def segments(self) -> Sequence[SegmentLike]: ...

    @property
    def stage(self) -> int | None: ...

    @property
    def status(self) -> str: ...

    @property
    def underground_floors(self) -> int: ...

    def footprint_geometry(self) -> BaseGeometry: ...


class RoadLike(Protocol):
    @property
    def width_m(self) -> float: ...

    @property
    def function(self) -> str: ...

    def centerline_geometry(self) -> BaseGeometry: ...

    def to_polygon(self) -> BaseGeometry: ...


class ParkingLike(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def spaces(self) -> int: ...

    def geometry(self) -> BaseGeometry: ...


@runtime_checkable
class MasterplanLike(Protocol):
    """Duck-typed surface of ``MasterplanProposal`` the validators read."""

    @property
    def buildings(self) -> Sequence[BuildingLike]: ...

    @property
    def roads(self) -> Sequence[RoadLike]: ...

    @property
    def parking(self) -> Sequence[ParkingLike]: ...

    @property
    def playgrounds(self) -> list[dict[str, Any]]: ...

    @property
    def zabudowa_srodmiejska(self) -> bool: ...

    def buildings_footprint_geometry(self) -> BaseGeometry: ...

    def greenery_geometry(self) -> BaseGeometry: ...

    def playgrounds_geometry(self) -> BaseGeometry: ...


# --------------------------------------------------------------------------- #
# Typed context records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NeighborBuilding:
    """An existing building outside the proposal (BDOT10k shape or user input).

    ``geometry`` is a GeoJSON dict or shapely polygon (EPSG:2180); ``height_m``
    is the explicit building height (neighbors carry no floor count).
    """

    name: str
    geometry: Any
    height_m: float

    def shapely(self) -> BaseGeometry:
        if isinstance(self.geometry, BaseGeometry):
            return self.geometry
        geom = self.geometry
        if isinstance(geom, dict) and geom.get("type") == "Feature":
            geom = geom["geometry"]
        return shape(geom)


@dataclass(frozen=True)
class Wall:
    """One external wall plane (§12 decomposition unit)."""

    building: str
    segment_index: int
    edge_index: int
    line: LineString
    #: Unit outward normal (away from the building mass).
    normal: tuple[float, float]
    windowed: bool
    #: True when ``windowed_walls`` was None → the windowed status is the
    #: CONSERVATIVE assumption, not a model declaration (marked in evidence).
    assumed_windowed: bool
    use: str
    floors: int

    @property
    def direction(self) -> tuple[float, float]:
        """Unit vector along the wall plane (parallel to the window plane)."""
        (x1, y1), (x2, y2) = self.line.coords[0], self.line.coords[-1]
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        return (dx / length, dy / length) if length else (1.0, 0.0)


@dataclass(frozen=True)
class ObstructorPart:
    """A 2.5D obstructing volume: a segment/neighbor footprint + its height."""

    owner: str
    geometry: BaseGeometry
    height_m: float
    source: str  # "proposal" | "neighbor"
    is_new: bool
    #: Segment index within the owning building (proposal parts only) — lets
    #: §13 treat OTHER segments of the same building as obstructors while never
    #: charging a wall with its own segment's footprint.
    segment_index: int | None = None


@dataclass(frozen=True)
class RoadInfo:
    function: str
    width_m: float
    centerline: BaseGeometry
    polygon: BaseGeometry


@dataclass(frozen=True)
class ParkingInfo:
    index: int
    kind: str
    spaces: int
    geometry: BaseGeometry


@dataclass(frozen=True)
class BuildingContext:
    """All geometry/attributes one building contributes to the validators."""

    name: str
    status: str
    is_new: bool
    footprint: BaseGeometry
    max_floors: int
    height_m: float  # floors × floor_height_m config (basis: industry_heuristic)
    is_multifamily: bool
    uses: tuple[str, ...]
    walls: tuple[Wall, ...]
    parts: tuple[ObstructorPart, ...]

    @property
    def is_zl(self) -> bool:
        """Any segment in a ZL kategoria zagrożenia ludzi (simplified mapping)."""
        return any(u in ZL_BY_USE for u in self.uses)


@dataclass(frozen=True)
class ValidationContext:
    """Typed geometric context shared by all Phase 10 validators."""

    parcel: BaseGeometry
    buildings: tuple[BuildingContext, ...]
    neighbor_parts: tuple[ObstructorPart, ...]
    roads: tuple[RoadInfo, ...]
    parking: tuple[ParkingInfo, ...]
    playgrounds: tuple[BaseGeometry, ...]
    greenery: BaseGeometry
    srodmiejska: bool
    config: ValidatorConfig
    proposal: Any  # the original proposal (for capacity-metrics reuse)

    def all_parts(self) -> tuple[ObstructorPart, ...]:
        """All 2.5D obstructor parts: proposal segments + neighbor buildings."""
        parts: list[ObstructorPart] = []
        for b in self.buildings:
            parts.extend(b.parts)
        parts.extend(self.neighbor_parts)
        return tuple(parts)

    def windowed_staly_pobyt_walls(self) -> tuple[Wall, ...]:
        """Windowed walls of rooms for stały pobyt ludzi (§13/§19/§40 targets)."""
        return tuple(
            w
            for b in self.buildings
            for w in b.walls
            if w.windowed and w.use in STALY_POBYT_USES
        )


# --------------------------------------------------------------------------- #
# Wall decomposition + sampling
# --------------------------------------------------------------------------- #
def _polygons(geom: BaseGeometry) -> list[Any]:
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [g for g in geom.geoms if g.geom_type == "Polygon"]
    return []


def segment_walls(
    segment: SegmentLike,
    *,
    building_name: str,
    segment_index: int,
    building_footprint: BaseGeometry,
) -> list[Wall]:
    """Decompose one segment into indexed external wall planes (§12 ust. 1).

    Edge indices enumerate the canonically oriented (exterior CCW, interiors CW)
    ring edges of the composed polygon — exterior ring(s) first, then interior
    (courtyard) rings — and are STABLE regardless of sibling segments, so
    ``windowed_walls`` indexing survives union context. For a CCW exterior ring
    the outward normal of edge (dx, dy) is (dy, −dx); the same formula points
    INTO the hole for CW interior rings, i.e. away from the building mass.
    """
    declared = segment.windowed_walls
    assumed = declared is None
    windowed_set = None if declared is None else set(declared)

    walls: list[Wall] = []
    edge_index = 0
    for poly in _polygons(segment.geometry()):
        canonical = orient(poly, sign=1.0)  # exterior CCW, interiors CW
        for ring in [canonical.exterior, *canonical.interiors]:
            coords = list(ring.coords)
            for (x1, y1), (x2, y2) in zip(coords, coords[1:], strict=False):
                dx, dy = x2 - x1, y2 - y1
                length = (dx * dx + dy * dy) ** 0.5
                if length <= _MIN_EDGE_LEN_M:
                    continue
                idx = edge_index
                edge_index += 1
                nx, ny = dy / length, -dx / length  # outward normal (see docstring)
                mid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
                probe = Point(mid[0] + nx * _NORMAL_PROBE_M, mid[1] + ny * _NORMAL_PROBE_M)
                if building_footprint.covers(probe):
                    # Internal plane shared with a sibling segment — not an
                    # external wall (index still consumed for stability).
                    continue
                walls.append(
                    Wall(
                        building=building_name,
                        segment_index=segment_index,
                        edge_index=idx,
                        line=LineString([(x1, y1), (x2, y2)]),
                        normal=(nx, ny),
                        windowed=True if windowed_set is None else idx in windowed_set,
                        assumed_windowed=assumed,
                        use=str(segment.use),
                        floors=int(segment.floors),
                    )
                )
    return walls


def sample_wall_points(wall: Wall, spacing_m: float) -> list[Point]:
    """Deterministic window-axis samples along a wall (≥1 point: the midpoint).

    ``n = max(1, floor(length / spacing))`` samples at the centres of equal
    sub-intervals — a 1-sample wall yields exactly its midpoint (plan §10.1.2:
    configurable spacing, midpoint minimum).
    """
    length = float(wall.line.length)
    n = max(1, int(length // spacing_m))
    return [wall.line.interpolate(((i + 0.5) / n) * length) for i in range(n)]


def offset_point(point: Point, normal: tuple[float, float], offset_m: float) -> Point:
    """The window point displaced just OUTSIDE its wall along the outward normal."""
    return Point(point.x + normal[0] * offset_m, point.y + normal[1] * offset_m)


# --------------------------------------------------------------------------- #
# Context builder
# --------------------------------------------------------------------------- #
def build_context(
    proposal: MasterplanLike,
    parcel: BaseGeometry,
    *,
    neighbors: Sequence[NeighborBuilding] = (),
    srodmiejska: bool | None = None,
    config: ValidatorConfig | None = None,
) -> ValidationContext:
    """Build the shared :class:`ValidationContext` from a masterplan proposal."""
    cfg = config or ValidatorConfig()

    buildings: list[BuildingContext] = []
    for building in proposal.buildings:
        footprint = building.footprint_geometry()
        walls: list[Wall] = []
        parts: list[ObstructorPart] = []
        uses: list[str] = []
        max_floors = 0
        is_new = building.status in NEW_STATUSES
        for i, segment in enumerate(building.segments):
            walls.extend(
                segment_walls(
                    segment,
                    building_name=building.name,
                    segment_index=i,
                    building_footprint=footprint,
                )
            )
            uses.append(str(segment.use))
            max_floors = max(max_floors, int(segment.floors))
            parts.append(
                ObstructorPart(
                    owner=building.name,
                    geometry=segment.geometry(),
                    # Height = floors × floor_height_m config (the DSL carries no
                    # explicit height) — basis: industry_heuristic.
                    height_m=int(segment.floors) * cfg.floor_height_m,
                    source="proposal",
                    is_new=is_new,
                    segment_index=i,
                )
            )
        buildings.append(
            BuildingContext(
                name=building.name,
                status=str(building.status),
                is_new=is_new,
                footprint=footprint,
                max_floors=max_floors,
                height_m=max_floors * cfg.floor_height_m,
                is_multifamily=any(u in RESIDENTIAL_USES for u in uses),
                uses=tuple(uses),
                walls=tuple(walls),
                parts=tuple(parts),
            )
        )

    neighbor_parts = tuple(
        ObstructorPart(
            owner=n.name,
            geometry=n.shapely(),
            height_m=float(n.height_m),
            source="neighbor",
            is_new=False,
        )
        for n in neighbors
    )

    roads = tuple(
        RoadInfo(
            function=str(r.function),
            width_m=float(r.width_m),
            centerline=r.centerline_geometry(),
            polygon=r.to_polygon(),
        )
        for r in proposal.roads
    )
    parking = tuple(
        ParkingInfo(index=i, kind=str(p.kind), spaces=int(p.spaces), geometry=p.geometry())
        for i, p in enumerate(proposal.parking)
    )
    playgrounds = tuple(
        shape(g["geometry"] if g.get("type") == "Feature" else g)
        for g in proposal.playgrounds
    )

    return ValidationContext(
        parcel=parcel,
        buildings=tuple(buildings),
        neighbor_parts=neighbor_parts,
        roads=roads,
        parking=parking,
        playgrounds=playgrounds,
        greenery=proposal.greenery_geometry(),
        srodmiejska=bool(
            proposal.zabudowa_srodmiejska if srodmiejska is None else srodmiejska
        ),
        config=cfg,
        proposal=proposal,
    )
