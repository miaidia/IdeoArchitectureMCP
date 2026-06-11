"""API ⇄ MCP parity test (Phase 14B; §27, v1 Phase 12 §12.3).

The SAME input goes through BOTH surfaces (in-memory MCP client session and the
FastAPI TestClient) against the SAME mock connector bundle; after normalising
the envelope (ids/timestamps differ per run) the DOMAIN NUMBERS must be equal —
the anti-drift guarantee of the shared use-case layer (§12.4 anti-pattern:
logic drift between API and MCP).
"""

from __future__ import annotations

import importlib
from typing import Any

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from tests.api_helpers import ANALYST_KEY, auth, build_api
from tests.mocks import mock_connectors

ANALYZE_INPUT = {"parcel_id": "141201_1.0001.1867/2"}


def _normalize(result: dict[str, Any]) -> dict[str, Any]:
    """Project the run-independent DOMAIN content of an AnalysisResult."""
    envelope = result.get("buildable_envelope") or {}
    scores = result.get("scores") or {}
    return {
        "status": result["status"],
        "decision": result["decision"],
        "envelope_area_m2": envelope.get("area_m2"),
        "envelope_confidence": envelope.get("confidence"),
        "constraint_types": sorted(
            c["constraint_type"] for c in result.get("constraints", [])
        ),
        "risk_types": sorted(r["risk_type"] for r in result.get("risks", [])),
        "unknown_topics": sorted(u["topic"] for u in result.get("unknowns", [])),
        "buildability": scores.get("buildability"),
        "evidence_count": len(result.get("evidence", [])),
        "layer_status": (result.get("planning") or {}).get("_risk_layer_status"),
    }


def _mcp_analyze(monkeypatch) -> dict[str, Any]:
    """Run parcel_analyze over the in-memory MCP session (Phase 7 idiom)."""
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    server = importlib.reload(server)
    runtime._usecases_module.set_connectors(mock_connectors())

    async def _run() -> dict[str, Any]:
        async with create_connected_server_and_client_session(server.mcp) as client:
            out = await client.call_tool(
                "parcel_analyze",
                {"input": ANALYZE_INPUT, "analysis_mode": "quick_screening"},
            )
            assert out.isError is False
            assert out.structuredContent is not None
            return out.structuredContent

    return anyio.run(_run)


def test_same_input_same_domain_result_via_both_surfaces(monkeypatch) -> None:
    mcp_result = _mcp_analyze(monkeypatch)

    harness = build_api(monkeypatch)  # injects the same mock bundle shape
    api_response = harness.client.post(
        "/v1/analyses",
        json={"input": ANALYZE_INPUT, "analysis_mode": "quick_screening"},
        headers=auth(ANALYST_KEY),
    )
    assert api_response.status_code == 201
    api_result = api_response.json()

    assert _normalize(api_result) == _normalize(mcp_result)

    # the envelope GeoJSON exposed by both surfaces is identical too
    from plot_mcp_server import usecases

    mcp_envelope = usecases.buildable_envelope_geojson(mcp_result["analysis_id"])
    api_envelope = harness.client.get(
        f"/v1/analyses/{api_result['analysis_id']}/buildable-envelope",
        headers=auth(ANALYST_KEY),
    ).json()
    # JSON round-trip: the direct use-case call carries tuples where the HTTP
    # wire carries lists — same numbers, different Python containers.
    import json

    mcp_geoms = json.loads(json.dumps([f["geometry"] for f in mcp_envelope["features"]]))
    api_geoms = [f["geometry"] for f in api_envelope["features"]]
    assert api_geoms == mcp_geoms

    usecases.set_connectors(None)
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
