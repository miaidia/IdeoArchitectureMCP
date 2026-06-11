"""Shared layer collection + GeoJSON/GPKG writers for the export tool (Phase 14).

:func:`collect_variant_layers` flattens a masterplan variant (+ parcel/envelope)
into named :class:`ExportLayer` groups using the SAME geometry assembly as the
renderer v2 (:func:`plot_reports.render.masterplan.masterplan_layers` — ONE road
buffering / parking-kind / status grouping implementation, never a second one).
:func:`collect_analysis_layers` does the same for a variant-less analysis
(parcel + buildable envelope + constraints — the screening layer set).

Writers:

* :func:`export_geojson` — a FeatureCollection (EPSG:2180 coordinates as-is,
  ``crs_note`` like the buildable-envelope resource);
* :func:`export_gpkg` — a GeoPackage via ``geopandas.GeoDataFrame.to_file``
  (driver="GPKG", pyogrio write path — verified working in this environment),
  one GPKG layer per export layer.

All coordinates stay in EPSG:2180 metres (no degree buffering — §0.5 guard).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from plot_reports.render.layer import LayerRole
from plot_reports.render.masterplan import masterplan_layers

#: LayerRole → stable export layer name (GPKG layer / GeoJSON ``layer`` property).
#: Retention polygons join the greenery layer (zieleń urządzona + retencja are one
#: PBC-adjacent CAD/GIS layer in the documented convention — see dxf.py).
_ROLE_TO_EXPORT_NAME: dict[LayerRole, str] = {
    LayerRole.PARCEL: "dzialka",
    LayerRole.BUILDABLE_ENVELOPE: "envelope",
    LayerRole.BUILDING_PLANNED: "budynki_proj",
    LayerRole.BUILDING_UNDER_CONSTRUCTION: "budynki_proj",
    LayerRole.BUILDING_EXISTING: "budynki_istn",
    LayerRole.BUILDING_COMPLETED: "budynki_istn",
    LayerRole.ROAD: "drogi",
    LayerRole.PARKING: "parking",
    LayerRole.GREENERY: "zielen",
    LayerRole.RETENTION: "zielen",
    LayerRole.PLAYGROUND: "plac_zabaw",
    LayerRole.VIOLATION: "naruszenia",
}


@dataclass(frozen=True)
class ExportLayer:
    """One named export layer: geometries + per-geometry attribute dicts."""

    name: str
    geometries: list[BaseGeometry]
    attributes: list[dict[str, Any]]


def _building_attrs(variant: Any) -> list[dict[str, Any]]:
    out = []
    for b in getattr(variant, "buildings", []) or []:
        floors = list(getattr(b, "floors_by_segment", []) or [])
        out.append(
            {
                "name": str(getattr(b, "name", "")),
                "status": str(getattr(b, "status", "")),
                "floors": max((int(f) for f in floors), default=0),
                "stage": getattr(b, "stage", None),
            }
        )
    return out


def collect_variant_layers(
    variant: Any,
    parcel: BaseGeometry,
    *,
    envelope: BaseGeometry | None = None,
) -> list[ExportLayer]:
    """Flatten a masterplan variant into named export layers (renderer-v2 assembly).

    Buildings carry ``name``/``status``/``floors``/``stage`` attributes (matched
    by footprint geometry against the variant's building records); other layers
    carry their renderer layer name.
    """
    render_layers, _annotations = masterplan_layers(variant, parcel, envelope=envelope)
    # Footprint → attribute lookup (WKB hex is exact for identical geometries).
    attr_by_wkb: dict[bytes, dict[str, Any]] = {}
    for b, attrs in zip(
        getattr(variant, "buildings", []) or [], _building_attrs(variant), strict=True
    ):
        geom = getattr(b, "geometry", None)
        if geom is not None:
            attr_by_wkb[shape(geom).wkb] = attrs

    grouped: dict[str, tuple[list[BaseGeometry], list[dict[str, Any]]]] = {}
    for layer in render_layers:
        name = _ROLE_TO_EXPORT_NAME.get(layer.role)
        if name is None:  # roles outside the export convention (e.g. OTHER)
            continue
        geoms, geom_attrs = grouped.setdefault(name, ([], []))
        for geom in layer.shapely_geometries():
            geoms.append(geom)
            # `render_layer` (NOT `layer`) so the export layer name stays the
            # canonical `layer` property in the GeoJSON writer.
            geom_attrs.append(attr_by_wkb.get(geom.wkb, {"render_layer": layer.name}))
    return [
        ExportLayer(name=name, geometries=geoms, attributes=attrs)
        for name, (geoms, attrs) in grouped.items()
    ]


def collect_analysis_layers(
    parcel_geojson: dict[str, Any] | None,
    envelope_geojson: dict[str, Any] | None,
    constraints: list[dict[str, Any]],
) -> list[ExportLayer]:
    """Variant-less (screening) export layers: parcel + envelope + constraints.

    ``constraints`` are ``{"constraint_type": ..., "geometry": GeoJSON, "hard": bool}``
    dicts (the use-case layer adapts the stored :class:`~plot_domain.Constraint`
    records — this module keeps the plot_reports decoupling rule).
    """
    layers: list[ExportLayer] = []
    if parcel_geojson is not None:
        layers.append(ExportLayer("dzialka", [shape(parcel_geojson)], [{}]))
    if envelope_geojson is not None:
        layers.append(ExportLayer("envelope", [shape(envelope_geojson)], [{}]))
    geoms: list[BaseGeometry] = []
    attrs: list[dict[str, Any]] = []
    for con in constraints:
        geometry = con.get("geometry")
        if geometry is None:
            continue
        geom = shape(geometry)
        if geom.is_empty:
            continue
        geoms.append(geom)
        attrs.append(
            {
                "constraint_type": str(con.get("constraint_type", "")),
                "hard": bool(con.get("hard")),
            }
        )
    if geoms:
        layers.append(ExportLayer("ograniczenia", geoms, attrs))
    return layers


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def export_geojson(layers: list[ExportLayer]) -> bytes:
    """Serialise export layers as one FeatureCollection (EPSG:2180 as-is)."""
    features: list[dict[str, Any]] = []
    for layer in layers:
        for geom, attrs in zip(layer.geometries, layer.attributes, strict=True):
            features.append(
                {
                    "type": "Feature",
                    "properties": {"layer": layer.name, **attrs},
                    "geometry": mapping(geom),
                }
            )
    doc = {"type": "FeatureCollection", "crs_note": "EPSG:2180", "features": features}
    return json.dumps(doc).encode("utf-8")


def export_gpkg(layers: list[ExportLayer]) -> bytes:
    """Write export layers into a GeoPackage; return the file bytes.

    Uses ``geopandas.GeoDataFrame.to_file(..., driver="GPKG", layer=<name>)`` —
    the documented multi-layer GPKG write path (pyogrio backend; the second and
    later layers append into the same file). GPKG is a SQLite container, so it
    needs a real file path — written to a temp file and read back as bytes.
    """
    import tempfile
    from pathlib import Path

    import geopandas as gpd

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "layers.gpkg"
        for layer in layers:
            if not layer.geometries:
                continue
            frame = gpd.GeoDataFrame(
                layer.attributes or [{} for _ in layer.geometries],
                geometry=layer.geometries,
                crs="EPSG:2180",
            )
            frame.to_file(path, layer=layer.name, driver="GPKG")
        return path.read_bytes()
