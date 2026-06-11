"""GIS/CAD/BIM exports of masterplan variants (Phase 14 part A).

* :func:`~plot_reports.export.dxf.export_masterplan_dxf` — CAD layers (`ezdxf`),
  layer naming convention documented in the module docstring.
* :func:`~plot_reports.export.ifc.export_masterplan_ifc` — IFC4 massing model
  (`ifcopenshell` 0.8.x): IfcProject → IfcSite → IfcBuilding/IfcBuildingStorey,
  EPSG:2180 georeference via IfcMapConversion. MASSING ONLY — no invented
  walls/slabs/windows (v2 plan anti-pattern guard).
* :func:`~plot_reports.export.layers.collect_export_layers` /
  :func:`~plot_reports.export.layers.export_geojson` /
  :func:`~plot_reports.export.layers.export_gpkg` — the shared layer collection
  + GeoJSON/GeoPackage writers used by the ``export_layers`` MCP tool.
"""

from plot_reports.export.dxf import DXF_LAYERS, export_masterplan_dxf
from plot_reports.export.ifc import export_masterplan_ifc
from plot_reports.export.layers import (
    ExportLayer,
    collect_analysis_layers,
    collect_variant_layers,
    export_geojson,
    export_gpkg,
)

__all__ = [
    "DXF_LAYERS",
    "export_masterplan_dxf",
    "export_masterplan_ifc",
    "ExportLayer",
    "collect_analysis_layers",
    "collect_variant_layers",
    "export_geojson",
    "export_gpkg",
]
