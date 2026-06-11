"""IFC4 massing export of a masterplan variant via ``ifcopenshell`` (Phase 14).

The BIM bridge for the PB/PW horizon (v2 plan PHASE 14 item 2):

* ``IfcProject`` (units: METRES) → ``IfcSite`` (parcel as a ``FootPrint``
  curve representation) → per :class:`~plot_domain.BuildingRecord`:
  ``IfcBuilding`` + ``IfcBuildingStorey`` per storey (above-ground storey count
  = max of ``floors_by_segment``; underground levels from
  ``underground_floors``; elevation = level × floor height from the capacity
  metrics config — ``variant.metadata["config_basis"]["floor_height_m"]``,
  default 3.3 m, basis ``industry_heuristic``);
* ONE extruded-footprint massing solid per building footprint part
  (``IfcExtrudedAreaSolid``, depth = floors × floor_height) attached to the
  ``IfcBuilding``. **MASSING ONLY** — no IfcWall / IfcSlab / IfcWindow are ever
  invented (v2 anti-pattern guard; test-enforced).
* Georeference: ``IfcMapConversion`` + ``IfcProjectedCRS("EPSG:2180")`` via the
  ``ifcopenshell.api.georeference`` module. Model coordinates are LOCAL metres
  (shifted to the parcel bounds min corner — BIM practice for large EPSG:2180
  coordinates); the map conversion carries the EPSG:2180 offset back.

ifcopenshell API style (verified against the installed 0.8.4.post1 in this
venv): the 0.8 module-call style ``ifcopenshell.api.root.create_entity(file,
ifc_class=..., name=...)`` exists (alongside the legacy
``ifcopenshell.api.run("root.create_entity", ...)``) — this module uses the
module-call style throughout: ``api.root.create_entity``,
``api.unit.assign_unit(file, length={"is_metric": True, "raw": "METERS"})``,
``api.context.add_context``, ``api.aggregate.assign_object``,
``api.georeference.add_georeferencing`` / ``edit_georeferencing``. The massing
solid itself is built with ``file.create_entity`` using the IFC4 schema names
(IfcCartesianPointList2D / IfcIndexedPolyCurve / IfcArbitraryClosedProfileDef /
IfcArbitraryProfileDefWithVoids / IfcExtrudedAreaSolid / IfcShapeRepresentation
/ IfcProductDefinitionShape) because no high-level "massing from polygon" API
exists in 0.8.x.

NOTE on the pinned version: the v2 plan names ifcopenshell 0.8.5, but its
``manylinux_2_31`` wheel actually links GLIBC_2.32 symbols and fails to import
on this WSL2 host (glibc 2.31). 0.8.4.post1 imports and validates fine, so
``packages/reports/pyproject.toml`` pins ``>=0.8.4,<0.8.5`` (documented
deviation; same 0.8.x API surface).
"""

from __future__ import annotations

from typing import Any

import ifcopenshell
import ifcopenshell.api.aggregate as api_aggregate
import ifcopenshell.api.context as api_context
import ifcopenshell.api.georeference as api_georeference
import ifcopenshell.api.root as api_root
import ifcopenshell.api.unit as api_unit
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

#: Default storey height when the variant metadata carries no capacity config —
#: the same industry heuristic as plot_planning.CapacityConfig.floor_height_m.
DEFAULT_FLOOR_HEIGHT_M = 3.3

_EPSG_2180 = "EPSG:2180"


def _floor_height(variant: Any) -> float:
    metadata = getattr(variant, "metadata", None)
    if isinstance(metadata, dict):
        basis = metadata.get("config_basis")
        if isinstance(basis, dict) and isinstance(basis.get("floor_height_m"), int | float):
            return float(basis["floor_height_m"])
    return DEFAULT_FLOOR_HEIGHT_M


def _local_placement(
    f: ifcopenshell.file, x: float, y: float, z: float, parent: Any | None = None
) -> Any:
    point = f.create_entity("IfcCartesianPoint", Coordinates=(x, y, z))
    axis = f.create_entity("IfcAxis2Placement3D", Location=point)
    return f.create_entity(
        "IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=axis
    )


def _ring_curve(f: ifcopenshell.file, coords: list[tuple[float, float]]) -> Any:
    """A closed 2D IfcIndexedPolyCurve from ring coordinates (IFC4 schema)."""
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]  # IFC curves close implicitly; drop duplicate vertex
    pointlist = f.create_entity(
        "IfcCartesianPointList2D", CoordList=[(float(x), float(y)) for x, y in coords]
    )
    return f.create_entity("IfcIndexedPolyCurve", Points=pointlist)


def _footprint_profile(f: ifcopenshell.file, polygon: BaseGeometry) -> Any:
    """An AREA profile from a polygon (holes → IfcArbitraryProfileDefWithVoids)."""
    outer = _ring_curve(f, list(polygon.exterior.coords))
    interiors = list(polygon.interiors)
    if not interiors:
        return f.create_entity(
            "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=outer
        )
    inner = [_ring_curve(f, list(ring.coords)) for ring in interiors]
    return f.create_entity(
        "IfcArbitraryProfileDefWithVoids",
        ProfileType="AREA",
        OuterCurve=outer,
        InnerCurves=inner,
    )


def _extruded_solid(
    f: ifcopenshell.file, polygon: BaseGeometry, depth: float
) -> Any:
    """An IfcExtrudedAreaSolid: footprint profile extruded ``depth`` m along +Z."""
    profile = _footprint_profile(f, polygon)
    origin = f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    position = f.create_entity("IfcAxis2Placement3D", Location=origin)
    z_dir = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
    return f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=position,
        ExtrudedDirection=z_dir,
        Depth=float(depth),
    )


def _shifted(geom: BaseGeometry, dx: float, dy: float) -> BaseGeometry:
    from shapely.affinity import translate

    return translate(geom, xoff=dx, yoff=dy)


def export_masterplan_ifc(
    variant: Any,
    parcel: BaseGeometry,
    *,
    project_name: str = "Plot Analyzer — koncepcja (massing)",
) -> bytes:
    """Export a masterplan variant as an IFC4 massing model; return file bytes.

    See the module docstring for the structure, georeference and the
    massing-only guarantee. ``variant`` is a domain MasterplanVariant (or any
    duck-typed object with ``buildings`` carrying ``name`` / ``geometry``
    (GeoJSON) / ``floors_by_segment`` / ``underground_floors`` / ``stage`` /
    ``status``).
    """
    floor_h = _floor_height(variant)
    # Local origin: parcel bounds min corner (kept on whole metres for legibility).
    minx, miny = (float(v) for v in parcel.bounds[:2])
    ox, oy = float(int(minx)), float(int(miny))

    f = ifcopenshell.file(schema="IFC4")
    project = api_root.create_entity(f, ifc_class="IfcProject", name=project_name)
    # Project units: METRES (assign_unit defaults to millimetres — overridden).
    api_unit.assign_unit(f, length={"is_metric": True, "raw": "METERS"})
    model_ctx = api_context.add_context(f, context_type="Model")
    body_ctx = api_context.add_context(
        f,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_ctx,
    )
    footprint_ctx = api_context.add_context(
        f,
        context_type="Model",
        context_identifier="FootPrint",
        target_view="MODEL_VIEW",
        parent=model_ctx,
    )

    # Georeference (IfcMapConversion + IfcProjectedCRS "EPSG:2180"): the local
    # engineering origin (0,0) maps to (ox, oy) in EPSG:2180.
    api_georeference.add_georeferencing(f, name=_EPSG_2180)
    api_georeference.edit_georeferencing(
        f,
        projected_crs={
            "Name": _EPSG_2180,
            "Description": "PL-1992 / Poland CS92",
            "GeodeticDatum": "ETRS89",
            "MapUnit": next(
                u for u in f.by_type("IfcSIUnit") if u.UnitType == "LENGTHUNIT"
            ),
        },
        coordinate_operation={
            "Eastings": ox,
            "Northings": oy,
            "OrthogonalHeight": 0.0,
            "Scale": 1.0,
        },
    )

    # IfcSite with the parcel as a FootPrint curve representation (honest: the
    # parcel BOUNDARY, no invented terrain solid).
    site = api_root.create_entity(f, ifc_class="IfcSite", name="Działka")
    api_aggregate.assign_object(f, products=[site], relating_object=project)
    site.ObjectPlacement = _local_placement(f, 0.0, 0.0, 0.0)
    parcel_local = _shifted(parcel, -ox, -oy)
    parcel_polys = (
        list(parcel_local.geoms) if parcel_local.geom_type == "MultiPolygon" else [parcel_local]
    )
    site_curves = [_ring_curve(f, list(p.exterior.coords)) for p in parcel_polys]
    site_shape = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=footprint_ctx,
        RepresentationIdentifier="FootPrint",
        RepresentationType="Curve2D",
        Items=site_curves,
    )
    site.Representation = f.create_entity(
        "IfcProductDefinitionShape", Representations=[site_shape]
    )

    for record in getattr(variant, "buildings", []) or []:
        geometry = getattr(record, "geometry", None)
        if geometry is None:
            continue
        footprint = shape(geometry) if isinstance(geometry, dict) else geometry
        footprint = _shifted(footprint, -ox, -oy)
        floors_list = [int(x) for x in (getattr(record, "floors_by_segment", []) or [])]
        floors = max(floors_list, default=0)
        underground = int(getattr(record, "underground_floors", 0) or 0)
        name = " ".join(str(getattr(record, "name", "")).split()) or "Budynek"

        building = api_root.create_entity(f, ifc_class="IfcBuilding", name=name)
        api_aggregate.assign_object(f, products=[building], relating_object=site)
        building.ObjectPlacement = _local_placement(
            f, 0.0, 0.0, 0.0, parent=site.ObjectPlacement
        )
        building.Description = (
            f"masa koncepcyjna; kondygnacje nadziemne: {floors}; podziemne: "
            f"{underground}; etap: {getattr(record, 'stage', None)}; status: "
            f"{getattr(record, 'status', '?')}"
        )

        # Storeys: underground levels (negative) + above-ground levels; elevation
        # = level × floor height (the heuristic metrics config — see docstring).
        storeys = []
        for level in range(-underground, floors):
            label = (
                f"Kondygnacja {level}" if level >= 0 else f"Kondygnacja {level} (podziemna)"
            )
            storey = api_root.create_entity(
                f, ifc_class="IfcBuildingStorey", name=label
            )
            storey.Elevation = float(level) * floor_h
            storey.ObjectPlacement = _local_placement(
                f, 0.0, 0.0, float(level) * floor_h, parent=building.ObjectPlacement
            )
            storeys.append(storey)
        if storeys:
            api_aggregate.assign_object(f, products=storeys, relating_object=building)

        # ONE massing solid per footprint part (a BuildingRecord stores the
        # segment-union footprint; parts of a MultiPolygon are extruded with the
        # matching per-segment floor count when the counts align part-for-part,
        # else honestly with the max — never an invented per-part value).
        parts = (
            list(footprint.geoms)
            if footprint.geom_type == "MultiPolygon"
            else [footprint]
        )
        per_part_floors = (
            floors_list if len(floors_list) == len(parts) else [floors] * len(parts)
        )
        solids = [
            _extruded_solid(f, part, max(part_floors, 0) * floor_h)
            for part, part_floors in zip(parts, per_part_floors, strict=True)
            if part_floors > 0 and not part.is_empty
        ]
        if solids:
            body_shape = f.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=body_ctx,
                RepresentationIdentifier="Body",
                RepresentationType="SweptSolid",
                Items=solids,
            )
            building.Representation = f.create_entity(
                "IfcProductDefinitionShape", Representations=[body_shape]
            )

    # MASSING-ONLY guarantee (anti-pattern guard, test-enforced downstream).
    assert not f.by_type("IfcWall") and not f.by_type("IfcSlab") and not f.by_type("IfcWindow")
    payload: str = f.to_string()
    return payload.encode("utf-8")
