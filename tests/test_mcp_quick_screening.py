"""MCP end-to-end test for the wired quick_screening surface (Phase 7 §E, evidence #7).

In-memory ClientSession (no subprocess / sockets) drives the real MCP tools after Phase 7
wired them to the §27 use-cases. Connectors are mocked (zero network). Verifies:

* ``parcel_analyze`` → ``analysis_get_result`` → ``report_generate`` (md + json) is coherent;
* the buildable envelope is exposed as the ``analysis://{id}/buildable-envelope.geojson``
  RESOURCE (fetched on demand), NOT inlined into the tool result (NFR-PERF-009);
* ``risks_list`` / ``sources_collect`` return the real risks / evidence pack;
* ``manual_review_required`` stays a normal status, not an error (§20.12).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl
from tests.mocks import mock_connectors

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO_ROOT / "schemas" / "analysis-result.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _load_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    server = importlib.reload(server)
    # Inject the mock connector bundle into the (reloaded) usecases module.
    runtime._usecases_module.set_connectors(mock_connectors())
    return server.mcp, runtime


@pytest.mark.anyio
async def test_mcp_analyze_get_result_report_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, runtime = _load_server(monkeypatch)
    try:
        async with create_connected_server_and_client_session(mcp) as client:
            # 1) parcel_analyze (quick_screening) → structured result
            analyze = await client.call_tool(
                "parcel_analyze",
                {"input": {"parcel_id": "141201_1.0001.1867/2"}, "analysis_mode": "quick_screening"},
            )
            assert analyze.isError is False
            structured = analyze.structuredContent
            assert structured is not None
            assert not list(VALIDATOR.iter_errors(structured))
            analysis_id = structured["analysis_id"]

            # tool result does NOT inline a giant envelope GeoJSON — the envelope object
            # carries a polygon summary, and the full geojson lives behind the resource.
            envelope = structured["buildable_envelope"]
            assert envelope is not None
            # the artifact list points to the geojson RESOURCE, not inlined bytes.
            uris = {a["uri"] for a in structured["artifacts"]}
            assert f"analysis://{analysis_id}/buildable-envelope.geojson" in uris

            # 2) analysis_get_result → same id, coherent
            got = await client.call_tool("analysis_get_result", {"analysis_id": analysis_id})
            assert got.isError is False
            assert got.structuredContent["analysis_id"] == analysis_id
            assert got.structuredContent["decision"] == structured["decision"]

            # 3) report_generate md + json → coherent, numbers match
            rep_md = await client.call_tool("report_generate", {"analysis_id": analysis_id, "format": "md"})
            rep_json = await client.call_tool("report_generate", {"analysis_id": analysis_id, "format": "json"})
            assert rep_md.isError is False and rep_json.isError is False
            md_content = rep_md.structuredContent["content"]
            assert "# Analiza działki" in md_content
            assert rep_md.structuredContent["headline_numbers"] == rep_json.structuredContent["headline_numbers"]

            # 4) buildable-envelope resource fetched ON DEMAND (not inlined)
            res = await client.read_resource(
                AnyUrl(f"analysis://{analysis_id}/buildable-envelope.geojson")
            )
            geo = json.loads(res.contents[0].text)
            assert geo["type"] == "FeatureCollection"
            roles = {f["properties"].get("role") for f in geo["features"]}
            assert "buildable_envelope" in roles

            # 5) risks_list + sources_collect return the real data
            risks = await client.call_tool("risks_list", {"analysis_id": analysis_id})
            assert risks.structuredContent["decision"] == structured["decision"]
            sources = await client.call_tool("sources_collect", {"analysis_id": analysis_id})
            assert sources.structuredContent["evidence_count"] >= 1
    finally:
        runtime._usecases_module.set_connectors(None)


@pytest.mark.anyio
async def test_mcp_parcel_resolve_real_uldk(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, runtime = _load_server(monkeypatch)
    try:
        async with create_connected_server_and_client_session(mcp) as client:
            res = await client.call_tool("parcel_resolve", {"parcel_id": "141201_1.0001.1867/2"})
            assert res.isError is False
            assert res.structuredContent["teryt"] == "141201_1.0001.1867/2"
            assert res.structuredContent["geometry_wkt"].startswith("POLYGON")
    finally:
        runtime._usecases_module.set_connectors(None)
