"""Phase 14 part A — GIS/CAD/BIM export tests (DXF/IFC/GeoJSON/GPKG + MCP e2e).

Over the riverside golden variant (8 buildings incl. 2 retained zabytki, road
loop, 2 underground halls — the Phase 11 acceptance fixture):

* DXF: re-opened with ezdxf — PA-* layer convention present, building polyline
  counts match the variant (6 projektowane / 2 istniejące), $INSUNITS == 6
  (metres), TEXT labels (name + floor count) present.
* IFC: re-opened with ifcopenshell — project units are METRES, 8 IfcBuildings,
  per-building storey counts == floors (+ underground), the golden extrusion
  depth == floors × 3.3 (the metrics-config storey height), IfcMapConversion +
  IfcProjectedCRS "EPSG:2180" present, and the MASSING-ONLY guarantee: zero
  IfcWall/IfcSlab/IfcWindow entities (v2 anti-pattern guard, test-enforced).
* GeoJSON/GPKG writers round-trip the layer set.
* MCP end-to-end: ``export_layers`` over the in-memory client for each format —
  artifact URI returned, file exists, bytes are NOT inlined (NFR-PERF-009), and
  the ``analysis://{id}/export/{file}`` resource serves the same bytes.

ZERO network; all geometry is the local fixture frame.
"""

from __future__ import annotations

import base64
import importlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import ezdxf
import ifcopenshell
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl
from tests.masterplan_fixtures import (
    riverside_e2e_indicators,
    riverside_masterplan_payload,
    riverside_parcel,
)

FLOOR_HEIGHT_M = 3.3  # CapacityConfig.floor_height_m — the metrics-config basis


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _uri_path(uri: str) -> Path:
    assert uri.startswith("file://")
    return Path(uri.removeprefix("file://"))


@pytest.fixture()
def riverside_variant(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Store the compliant riverside masterplan as a variant via the real path."""
    from plot_agent.context import AnalysisContext
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT
    from plot_mcp_server import usecases

    DEFAULT_MASTERPLAN_AUDIT.clear()
    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-export")
    parcel = riverside_parcel()
    usecases.set_drawing_context(
        AnalysisContext.with_loaded_rules(parcel=parcel, buildable_envelope=parcel)
    )
    try:
        out = usecases.propose_layout_render(
            riverside_masterplan_payload(flawed=False),
            riverside_e2e_indicators(),
            "eksportowy wariant golden",
        )
        assert out["valid"] is True
        yield {"usecases": usecases, "variant_id": out["variant_id"]}
    finally:
        usecases.set_drawing_context(None)
        DEFAULT_MASTERPLAN_AUDIT.clear()


# --------------------------------------------------------------------------- #
# DXF (Task 2)
# --------------------------------------------------------------------------- #
def test_dxf_export_layers_and_labels(riverside_variant: dict[str, Any], tmp_path: Path) -> None:
    usecases = riverside_variant["usecases"]
    out = usecases.export_layers(None, "dxf", riverside_variant["variant_id"])
    assert out["status"] == "exported"
    data = _uri_path(out["artifact_uri"]).read_bytes()
    assert len(data) == out["byte_size"] > 0

    dxf_file = tmp_path / "export.dxf"
    dxf_file.write_bytes(data)
    doc = ezdxf.readfile(dxf_file)

    # metric units: 6 = meters (EPSG:2180 coordinates as-is).
    assert doc.header["$INSUNITS"] == 6

    layer_names = {layer.dxf.name for layer in doc.layers}
    for expected in (
        "PA-DZIALKA", "PA-BUDYNKI-PROJ", "PA-BUDYNKI-ISTN", "PA-DROGI",
        "PA-PARKING", "PA-ZIELEN", "PA-PLAC-ZABAW", "PA-ENVELOPE", "PA-OPISY",
    ):
        assert expected in layer_names, f"missing DXF layer {expected}"
    # the compliant variant has ZERO failing checks → no violations layer.
    assert "PA-NARUSZENIA" not in layer_names

    counts = Counter((e.dxftype(), e.dxf.layer) for e in doc.modelspace())
    # 6 projektowane buildings / 2 retained zabytki → closed polylines per layer.
    assert counts[("LWPOLYLINE", "PA-BUDYNKI-PROJ")] == 6
    assert counts[("LWPOLYLINE", "PA-BUDYNKI-ISTN")] == 2
    assert counts[("LWPOLYLINE", "PA-DZIALKA")] == 1
    # one TEXT label per building: name + floor count at the centroid.
    assert counts[("TEXT", "PA-OPISY")] == 8
    texts = {e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT"}
    assert "A1 (6 kond.)" in texts
    assert "Hala elektrowni (1 kond.)" in texts
    # building polylines are CLOSED (polygon exteriors).
    proj = [e for e in doc.modelspace()
            if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "PA-BUDYNKI-PROJ"]
    assert all(p.closed for p in proj)


# --------------------------------------------------------------------------- #
# IFC (Task 3)
# --------------------------------------------------------------------------- #
def test_ifc_export_massing_model(riverside_variant: dict[str, Any], tmp_path: Path) -> None:
    usecases = riverside_variant["usecases"]
    out = usecases.export_layers(None, "ifc", riverside_variant["variant_id"])
    assert out["status"] == "exported"
    data = _uri_path(out["artifact_uri"]).read_bytes()

    ifc_file = tmp_path / "export.ifc"
    ifc_file.write_bytes(data)
    model = ifcopenshell.open(ifc_file)

    # IfcProject with METRES as the length unit (assign_unit override verified).
    project = model.by_type("IfcProject")[0]
    length_units = [
        u for u in project.UnitsInContext.Units
        if getattr(u, "UnitType", None) == "LENGTHUNIT"
    ]
    assert len(length_units) == 1
    assert length_units[0].Name == "METRE" and length_units[0].Prefix is None

    # Spatial structure: site + 8 buildings (6 proj + 2 zabytki).
    assert len(model.by_type("IfcSite")) == 1
    buildings = {b.Name: b for b in model.by_type("IfcBuilding")}
    assert len(buildings) == 8
    assert {"A1", "A2", "B1", "B2", "C1", "C2", "Hala elektrowni", "Komin"} == set(buildings)

    # Storey counts: 6-storey rows → 6 IfcBuildingStorey; zabytki → 1.
    def storeys_of(building: Any) -> list[Any]:
        out: list[Any] = []
        for rel in model.by_type("IfcRelAggregates"):
            if rel.RelatingObject == building:
                out.extend(
                    o for o in rel.RelatedObjects if o.is_a("IfcBuildingStorey")
                )
        return out

    a1_storeys = storeys_of(buildings["A1"])
    assert len(a1_storeys) == 6
    assert len(storeys_of(buildings["Komin"])) == 1
    # elevation = level × floor height (metrics config 3.3).
    elevations = sorted(s.Elevation for s in a1_storeys)
    assert elevations == [pytest.approx(lv * FLOOR_HEIGHT_M) for lv in range(6)]

    # GOLDEN: massing extrusion depth == floors × 3.3 for A1 (6 kondygnacji).
    a1_solid = buildings["A1"].Representation.Representations[0].Items[0]
    assert a1_solid.is_a("IfcExtrudedAreaSolid")
    assert a1_solid.Depth == pytest.approx(6 * FLOOR_HEIGHT_M)  # 19.8 m
    hala_solid = buildings["Hala elektrowni"].Representation.Representations[0].Items[0]
    assert hala_solid.Depth == pytest.approx(1 * FLOOR_HEIGHT_M)

    # Georeference: IfcMapConversion + IfcProjectedCRS "EPSG:2180".
    crs = model.by_type("IfcProjectedCRS")
    assert len(crs) == 1 and crs[0].Name == "EPSG:2180"
    conversions = model.by_type("IfcMapConversion")
    assert len(conversions) == 1
    parcel = riverside_parcel()
    minx, miny = parcel.bounds[:2]
    assert conversions[0].Eastings == pytest.approx(float(int(minx)))
    assert conversions[0].Northings == pytest.approx(float(int(miny)))
    assert conversions[0].Scale == pytest.approx(1.0)

    # MASSING ONLY (anti-pattern guard): no invented walls/slabs/windows.
    assert model.by_type("IfcWall") == []
    assert model.by_type("IfcSlab") == []
    assert model.by_type("IfcWindow") == []

    # schema-level validation (ifcopenshell.validate IS available — use it).
    import logging

    import ifcopenshell.validate as ifc_validate

    class _Collect(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
            self.records.append(record)

    logger = logging.getLogger("test-ifc-validate")
    handler = _Collect()
    logger.addHandler(handler)
    ifc_validate.validate(model, logger)
    errors = [r for r in handler.records if r.levelno >= logging.ERROR]
    assert errors == [], [r.getMessage() for r in errors]


# --------------------------------------------------------------------------- #
# GeoJSON / GPKG (Task 4 writers)
# --------------------------------------------------------------------------- #
def test_geojson_export_round_trip(riverside_variant: dict[str, Any]) -> None:
    usecases = riverside_variant["usecases"]
    out = usecases.export_layers(None, "geojson", riverside_variant["variant_id"])
    assert out["status"] == "exported"
    doc = json.loads(_uri_path(out["artifact_uri"]).read_text(encoding="utf-8"))
    assert doc["type"] == "FeatureCollection"
    assert doc["crs_note"] == "EPSG:2180"
    layers = {f["properties"]["layer"] for f in doc["features"]}
    assert {"dzialka", "envelope", "budynki_proj", "budynki_istn",
            "drogi", "parking", "zielen", "plac_zabaw"} <= layers
    buildings = [f for f in doc["features"]
                 if f["properties"]["layer"].startswith("budynki")]
    assert len(buildings) == 8
    named = {f["properties"].get("name") for f in buildings}
    assert {"A1", "Hala elektrowni"} <= named


def test_gpkg_export_layers_readable(riverside_variant: dict[str, Any], tmp_path: Path) -> None:
    import geopandas as gpd
    import pyogrio

    usecases = riverside_variant["usecases"]
    out = usecases.export_layers(None, "gpkg", riverside_variant["variant_id"])
    assert out["status"] == "exported"
    gpkg = tmp_path / "layers.gpkg"
    gpkg.write_bytes(_uri_path(out["artifact_uri"]).read_bytes())
    layer_names = {entry[0] for entry in pyogrio.list_layers(gpkg)}
    assert {"dzialka", "budynki_proj", "budynki_istn", "drogi", "zielen"} <= layer_names
    frame = gpd.read_file(gpkg, layer="budynki_proj")
    assert len(frame) == 6
    assert str(frame.crs) == "EPSG:2180"
    assert set(frame["floors"]) == {6}


def test_report_generate_html_pdf_for_variant(riverside_variant: dict[str, Any]) -> None:
    """report_generate(html|pdf, variant_id=...) → the koncepcja deliverable
    rendered from the ONE ReportModel (§31), artifacts linked, never inlined."""
    from plot_reports import pdf_available

    usecases = riverside_variant["usecases"]
    variant_id = riverside_variant["variant_id"]

    out = usecases.report_generate(None, "html", variant_id)
    assert out["status"] == "rendered" and out["kind"] == "koncepcja"
    assert out["audience"] == "architect" and out["variant_id"] == variant_id
    html_path = _uri_path(out["artifact_uri"])
    html = html_path.read_text(encoding="utf-8")
    assert "Koncepcja zagospodarowania (chłonność)" in html
    assert "Zestawienie etapów" in html and "SUMA" in html
    assert "Legenda planu (4 statusy)" in html
    # the model JSON travels with the render (same numbers, snapshot hash inside).
    model_doc = json.loads(_uri_path(out["model_json_uri"]).read_text(encoding="utf-8"))
    assert model_doc["analysis_snapshot_hash"] == out["analysis_snapshot_hash"]
    assert model_doc["totals"]["pum_m2"] == out["headline_numbers"]["pum_m2"]
    # bytes are linked, not inlined.
    assert "content" not in out and out["resource_link"].endswith(".html")

    pdf_out = usecases.report_generate(None, "pdf", variant_id, "investor")
    ok, _reason = pdf_available()
    if ok:  # pragma: no cover - host-dependent
        assert pdf_out["status"] == "rendered"
        assert _uri_path(pdf_out["artifact_uri"]).read_bytes()[:5] == b"%PDF-"
    else:
        assert pdf_out["status"] == "pdf_unavailable"
        assert pdf_out["artifact_uri"] is None
        assert "weasyprint" in pdf_out["note"]


def test_unsupported_format_and_missing_variant_are_honest(
    riverside_variant: dict[str, Any],
) -> None:
    usecases = riverside_variant["usecases"]
    out = usecases.export_layers(None, "shp", riverside_variant["variant_id"])
    assert out["status"] == "unsupported_format" and out["artifact_uri"] is None
    # dxf/ifc without ANY variant: not_found, never a fabricated file (§21).
    out = usecases.export_layers("analysis-without-variant", "dxf")
    assert out["status"] == "not_found" and out["artifact_uri"] is None


# --------------------------------------------------------------------------- #
# MCP end-to-end (Task 4): tool result → resource_link → resource bytes
# --------------------------------------------------------------------------- #
def _load_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    server = importlib.reload(server)
    return server.mcp, runtime


_MAGIC = {
    "geojson": b'{"type": "FeatureCollection"',
    "gpkg": b"SQLite format 3\x00",  # GPKG is a SQLite container
    "dxf": b"  0\nSECTION",  # ezdxf text output starts with the HEADER section
    "ifc": b"ISO-10303-21;",  # STEP physical file header (IFC SPF)
}


@pytest.mark.anyio
async def test_export_layers_mcp_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp_server, runtime = _load_server(monkeypatch)
    usecases = runtime._usecases_module
    from plot_agent.context import AnalysisContext

    parcel = riverside_parcel()
    usecases.set_drawing_context(
        AnalysisContext.with_loaded_rules(parcel=parcel, buildable_envelope=parcel)
    )
    try:
        async with create_connected_server_and_client_session(mcp_server) as client:
            proposed = await client.call_tool(
                "propose_layout",
                {
                    "proposal": riverside_masterplan_payload(flawed=False),
                    "indicators": riverside_e2e_indicators(),
                },
            )
            assert proposed.isError is False
            variant_id = proposed.structuredContent["variant_id"]

            for fmt, magic in _MAGIC.items():
                result = await client.call_tool(
                    "export_layers", {"format": fmt, "variant_id": variant_id}
                )
                assert result.isError is False, fmt
                sc = result.structuredContent
                assert sc["status"] == "exported"
                assert sc["variant_id"] == variant_id

                # artifact persisted…
                path = _uri_path(sc["artifact_uri"])
                assert path.exists() and path.stat().st_size == sc["byte_size"]
                # …and NOT inlined into the tool result (NFR-PERF-009): the
                # structured payload carries links/numbers only.
                assert "content" not in sc and "data" not in sc
                assert len(json.dumps(sc)) < 2_000

                # the resource_link serves the SAME bytes on demand.
                res = await client.read_resource(AnyUrl(sc["resource_link"]))
                blob = base64.b64decode(res.contents[0].blob)  # type: ignore[union-attr]
                assert blob == path.read_bytes()
                assert blob.startswith(magic), f"{fmt}: unexpected file magic"
    finally:
        usecases.set_drawing_context(None)
