"""MCP protocol conformance sweep (Phase 16; F-0557 full-matrix gap).

The Phase 2 protocol tests cover tools/list, outputSchema PRESENCE and one
tool's payload. The gap closed here: EVERY public tool is actually CALLED over
the in-memory client/server session and its ``structuredContent`` is validated
against the tool's own advertised ``outputSchema`` (Draft 2020-12). CAVEAT:
most tools return a plain ``dict`` and therefore advertise a weak
``{"type": "object"}``-shaped schema — for those the schema check alone proves
little, so the sweep ADDITIONALLY asserts a minimal expected-keys contract per
tool (:data:`EXPECTED_KEYS`, derived from the real payloads). The two
``structured_output=False`` image tools (``map_preview``, ``propose_layout``)
have no outputSchema by design — their content-block contracts are asserted
instead (image block present; propose_layout additionally returns structured
content despite the disabled schema, per the CallToolResult passthrough).

Zero network: the Phase 7 mock connector bundle.
"""

from __future__ import annotations

import importlib
from typing import Any

import mcp.types as types
import pytest
from jsonschema import Draft202012Validator
from mcp.shared.memory import create_connected_server_and_client_session
from tests.mocks import mock_connectors

#: Tools advertising an outputSchema → minimal-arg builder (analysis-bound args
#: receive the analysis id created in the session).
STRUCTURED_TOOL_CALLS: dict[str, Any] = {
    "parcel_resolve": lambda aid: {"parcel_id": "141201_1.0001.1867/2"},
    "parcel_analyze": lambda aid: {
        "input": {"parcel_id": "141201_1.0001.1867/2"},
        "analysis_mode": "quick_screening",
    },
    "analysis_get_status": lambda aid: {"analysis_id": aid},
    "analysis_get_result": lambda aid: {"analysis_id": aid},
    "planning_fetch": lambda aid: {"parcel_id": "141201_1.0001.1867/2"},
    "planning_parse_document": lambda aid: {
        "text": "MPZP: maksymalna wysokosc zabudowy: 12 m."
    },
    "constraints_compute": lambda aid: {"analysis_id": aid},
    "capacity_generate_scenarios": lambda aid: {
        "analysis_id": aid,
        "indicators": {"max_kondygnacje": 4, "max_coverage_ratio": 0.4},
    },
    "risks_list": lambda aid: {"analysis_id": aid},
    "sources_collect": lambda aid: {"analysis_id": aid},
    "report_generate": lambda aid: {"analysis_id": aid, "format": "md"},
    "export_layers": lambda aid: {"analysis_id": aid, "format": "geojson"},
    "portfolio_analyze": lambda aid: {
        "parcels": [{"parcel_id": "141201_1.0001.1867/2"}]
    },
    "ruleset_explain": lambda aid: {},
    "source_healthcheck": lambda aid: {},
    "cache_warm": lambda aid: {"scope": "parcel", "target_id": "141201_1.0001.1867/2"},
    "document_ingest": lambda aid: {
        "file_id": "missing-file",
        "purpose": "protocol conformance sweep",
        "analysis_id": aid,
    },
    "monitoring_create": lambda aid: {
        "scope": "parcel",
        "target_id": "141201_1.0001.1867/2",
        "purpose": "protocol conformance sweep",
    },
    "manual_override": lambda aid: {
        "analysis_id": "adhoc",
        "target_type": "rule",
        "target_id": "PL-WT-12-SETBACKS-001",
        "reason": "protocol conformance sweep (TEST)",
        "user_id": "qa",
        "after": {"status": "pass", "confidence": 0.95},
    },
    "diagnostics_run": lambda aid: {"probe_connectors": False},
}

#: Minimal expected-keys contract per structured tool (m3): dict-returning tools
#: advertise only ``{"type": "object"}``, so schema validation alone is vacuous
#: for them — these key sets (a load-bearing SUBSET of each real payload, not an
#: exhaustive shape) make the sweep assert actual content.
EXPECTED_KEYS: dict[str, set[str]] = {
    "parcel_resolve": {"id", "teryt", "number", "geometry_wkt", "administrative_context"},
    "parcel_analyze": {"analysis_id", "status", "parcel", "constraints", "decision"},
    "analysis_get_status": {"analysis_id", "status", "progress", "decision"},
    "analysis_get_result": {"analysis_id", "status", "parcel", "constraints", "risks", "decision", "evidence"},
    "planning_fetch": {"parcel_id", "acts", "zones", "coverage_status"},
    "planning_parse_document": {"status", "mode", "indicators", "evidence", "unknowns"},
    "constraints_compute": {"analysis_id", "constraints", "buildable_envelope_summary", "inter_building_rules"},
    "capacity_generate_scenarios": {"analysis_id", "status", "scenarios", "unknowns"},
    "risks_list": {"analysis_id", "risks", "unknowns", "decision"},
    "sources_collect": {"analysis_id", "sources", "evidence", "evidence_count"},
    "report_generate": {"analysis_id", "status", "format", "content", "report_version"},
    "export_layers": {"analysis_id", "status", "format", "artifact_uri", "mime_type"},
    "portfolio_analyze": {"batch_id", "status", "items", "ranking", "analyzed"},
    "ruleset_explain": {"rules", "ruleset_version", "rule_count", "categories", "ruleset_errors"},
    "source_healthcheck": {"connectors"},
    "cache_warm": {"status", "scope", "target_id", "warmed_count"},
    "document_ingest": {"status", "file_id", "ingested", "analysis_id"},
    "monitoring_create": {"monitoring_id", "scope", "target_id", "active", "audit_logged"},
    "manual_override": {"override_id", "status", "target_id", "applied", "audit_logged"},
    "diagnostics_run": {"ruleset_version", "rule_count", "ruleset_errors", "dev_hot_reload", "last_reload_at", "connectors"},
}

#: structured_output=False image tools — no outputSchema BY DESIGN.
IMAGE_TOOLS = {"map_preview", "propose_layout"}

_SMALL_MASTERPLAN = {
    "schema_version": 2,
    "buildings": [
        {
            "name": "Sweep",
            "segments": [
                {"rectangles": [{"x": 10, "y": 12, "w": 20, "h": 10}], "floors": 3,
                 "use": "mieszkalny"}
            ],
        }
    ],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_expected_keys_table_covers_every_structured_tool() -> None:
    """The expected-keys contract must stay in lockstep with the sweep table."""
    assert set(EXPECTED_KEYS) == set(STRUCTURED_TOOL_CALLS)


def _load_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    server = importlib.reload(server)
    return server.mcp, runtime


@pytest.mark.anyio
async def test_every_public_tool_returns_schema_valid_structured_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_server, runtime = _load_server(monkeypatch)
    usecases = runtime._usecases_module
    usecases.set_connectors(mock_connectors())
    try:
        async with create_connected_server_and_client_session(mcp_server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            # The sweep table covers the COMPLETE public surface (22 tools).
            assert set(tools) == set(STRUCTURED_TOOL_CALLS) | IMAGE_TOOLS
            assert len(tools) == 22

            # One stored analysis the bound tools reference.
            analyze = await client.call_tool(
                "parcel_analyze",
                STRUCTURED_TOOL_CALLS["parcel_analyze"](None),
            )
            assert analyze.isError is False
            aid = analyze.structuredContent["analysis_id"]

            for name, build_args in sorted(STRUCTURED_TOOL_CALLS.items()):
                tool = tools[name]
                assert tool.outputSchema is not None, f"{name} lost its outputSchema"
                Draft202012Validator.check_schema(tool.outputSchema)
                result = await client.call_tool(name, build_args(aid))
                assert result.isError is False, f"{name}: {result.content}"
                assert result.structuredContent is not None, f"{name} returned none"
                errors = list(
                    Draft202012Validator(tool.outputSchema).iter_errors(
                        result.structuredContent
                    )
                )
                assert not errors, f"{name} schema violations: {errors[:3]}"
                # m3: dict-typed outputSchemas are weak ({"type": "object"}) —
                # the expected-keys contract asserts actual payload content.
                missing_keys = EXPECTED_KEYS[name] - set(result.structuredContent)
                assert not missing_keys, (
                    f"{name} payload lost expected keys: {sorted(missing_keys)}"
                )
    finally:
        usecases.set_connectors(None)


@pytest.mark.anyio
async def test_image_tools_content_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """map_preview / propose_layout: no outputSchema (structured_output=False) —
    the conformance contract is the inline IMAGE content block (+ structured
    content for propose_layout via the CallToolResult passthrough)."""
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT

    mcp_server, runtime = _load_server(monkeypatch)
    usecases = runtime._usecases_module
    usecases.set_connectors(mock_connectors())
    DEFAULT_MASTERPLAN_AUDIT.clear()
    try:
        async with create_connected_server_and_client_session(mcp_server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            for name in IMAGE_TOOLS:
                assert tools[name].outputSchema is None

            preview = await client.call_tool("map_preview", {})
            assert preview.isError is False
            assert any(isinstance(c, types.ImageContent) for c in preview.content)

            layout = await client.call_tool(
                "propose_layout", {"proposal": _SMALL_MASTERPLAN}
            )
            assert layout.isError is False
            assert any(isinstance(c, types.ImageContent) for c in layout.content)
            sc = layout.structuredContent
            assert sc is not None and {"accepted", "valid", "score", "critique"} <= set(sc)
    finally:
        usecases.set_connectors(None)
        DEFAULT_MASTERPLAN_AUDIT.clear()
