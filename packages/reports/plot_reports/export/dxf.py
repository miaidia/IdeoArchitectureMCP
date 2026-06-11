"""DXF export of a masterplan variant via ``ezdxf`` (Phase 14 part A, F-0394).

Layer naming convention (documented contract — stable for CAD interop):

================== ============================================================
Layer              Content
================== ============================================================
``PA-DZIALKA``     parcel boundary (closed LWPolyline)
``PA-BUDYNKI-PROJ`` planned buildings: status ``projektowany`` / ``w_budowie``
``PA-BUDYNKI-ISTN`` existing buildings: ``istniejacy`` / ``zrealizowany`` /
                   ``zabytek_do_remontu``
``PA-DROGI``       internal roads (buffered centerlines — renderer-v2 assembly)
``PA-PARKING``     parking (surface + underground hall outlines)
``PA-ZIELEN``      greenery (PBC) + retention polygons
``PA-PLAC-ZABAW``  playgrounds
``PA-ENVELOPE``    buildable envelope
``PA-NARUSZENIA``  failing rule-check evidence geometries (violations)
``PA-OPISY``       TEXT annotations: building name + floor count at centroids
================== ============================================================

Geometry: closed LWPolylines from every polygon exterior AND interior ring
(holes); open LWPolylines for line geometries (violation evidence may be lines).
Units are metres: EPSG:2180 coordinates are written AS-IS and the header sets
``$INSUNITS = 6`` (6 = meters per the DXF reference / ezdxf docs on
INSUNITS header values).

ezdxf API used (verified against the installed ezdxf 1.4.4 in this venv —
``ezdxf.new()``, ``doc.layers.add(name, color=...)``,
``msp.add_lwpolyline(points, close=..., dxfattribs={"layer": ...})``,
``msp.add_text(text, dxfattribs={"insert": (x, y), "height": ...})``,
``doc.write(text_stream)``; matches https://ezdxf.readthedocs.io/ tutorials for
layers / lwpolyline / text).
"""

from __future__ import annotations

import io
from typing import Any

import ezdxf
from shapely.geometry.base import BaseGeometry

from plot_reports.export.layers import ExportLayer, collect_variant_layers

#: Export-layer name (layers.py) → DXF layer name + ACI colour (deterministic).
DXF_LAYERS: dict[str, tuple[str, int]] = {
    "dzialka": ("PA-DZIALKA", 7),  # white/black
    "budynki_proj": ("PA-BUDYNKI-PROJ", 1),  # red
    "budynki_istn": ("PA-BUDYNKI-ISTN", 8),  # grey
    "drogi": ("PA-DROGI", 9),  # light grey
    "parking": ("PA-PARKING", 4),  # cyan
    "zielen": ("PA-ZIELEN", 3),  # green
    "plac_zabaw": ("PA-PLAC-ZABAW", 2),  # yellow
    "envelope": ("PA-ENVELOPE", 5),  # blue
    "naruszenia": ("PA-NARUSZENIA", 6),  # magenta
}

#: Annotation layer (building names + floor counts).
DXF_TEXT_LAYER = "PA-OPISY"

#: DXF $INSUNITS value for metres (DXF reference: 6 = Meters).
_INSUNITS_METERS = 6

_TEXT_HEIGHT_M = 2.0  # deterministic annotation height in drawing units (m)


def _add_geometry(msp: Any, geom: BaseGeometry, layer: str) -> int:
    """Add a shapely geometry as LWPolyline(s); return the entity count added."""
    count = 0
    geom_type = geom.geom_type
    if geom_type == "Polygon":
        rings = [geom.exterior, *geom.interiors]
        for ring in rings:
            coords = list(ring.coords)
            # closed LWPolyline: drop the GeoJSON-duplicated closing vertex.
            if len(coords) > 1 and coords[0] == coords[-1]:
                coords = coords[:-1]
            if len(coords) >= 3:
                msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": layer})
                count += 1
    elif geom_type == "LineString":
        coords = list(geom.coords)
        if len(coords) >= 2:
            msp.add_lwpolyline(coords, close=False, dxfattribs={"layer": layer})
            count += 1
    elif geom_type in ("MultiPolygon", "MultiLineString", "GeometryCollection"):
        for part in geom.geoms:
            count += _add_geometry(msp, part, layer)
    # Points are not part of the export convention; skipped silently is dishonest —
    # they simply never occur in the variant layer assembly (polygons/lines only).
    return count


def export_masterplan_dxf(
    variant: Any,
    parcel: BaseGeometry,
    *,
    envelope: BaseGeometry | None = None,
    violations: list[BaseGeometry] | None = None,
) -> bytes:
    """Export a masterplan variant to DXF bytes (layer convention above).

    ``variant`` is duck-typed like the renderer v2 (a domain
    :class:`~plot_domain.MasterplanVariant` or a DSL proposal). ``violations``
    are failing rule-check evidence geometries → ``PA-NARUSZENIA``.
    """
    layers: list[ExportLayer] = collect_variant_layers(variant, parcel, envelope=envelope)
    if violations:
        layers.append(
            ExportLayer(
                "naruszenia",
                list(violations),
                [{"layer": "naruszenia"} for _ in violations],
            )
        )

    doc = ezdxf.new(dxfversion="R2010", setup=False)
    doc.header["$INSUNITS"] = _INSUNITS_METERS  # 6 = meters (EPSG:2180 coords as-is)
    msp = doc.modelspace()

    created: set[str] = set()
    for layer in layers:
        dxf_name, color = DXF_LAYERS[layer.name]
        if dxf_name not in created:
            doc.layers.add(dxf_name, color=color)
            created.add(dxf_name)
        for geom in layer.geometries:
            _add_geometry(msp, geom, dxf_name)

    # TEXT annotations: building name + floor count at the footprint centroid
    # (mirrors the renderer-v2 labels; one TEXT per building keeps CAD tidy).
    doc.layers.add(DXF_TEXT_LAYER, color=7)
    for b in getattr(variant, "buildings", []) or []:
        geometry = getattr(b, "geometry", None)
        if geometry is None:
            continue
        from shapely.geometry import shape

        footprint = shape(geometry) if isinstance(geometry, dict) else geometry
        c = footprint.centroid
        floors = list(getattr(b, "floors_by_segment", []) or [])
        floors_label = "/".join(str(int(f)) for f in floors) or "?"
        name = " ".join(str(getattr(b, "name", "")).split())  # collapse whitespace
        msp.add_text(
            f"{name} ({floors_label} kond.)",
            dxfattribs={
                "layer": DXF_TEXT_LAYER,
                "height": _TEXT_HEIGHT_M,
                "insert": (c.x, c.y),
            },
        )

    # DXF R2010 is a TEXT format — ezdxf writes to a text stream; encode after.
    buffer = io.StringIO()
    doc.write(buffer)
    return buffer.getvalue().encode("utf-8")
