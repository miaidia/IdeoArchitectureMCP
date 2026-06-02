"""MCP protocol tests for the thin FastMCP server (Phase 2 §2.3 verification).

Uses the SDK's in-memory client/server helper
``mcp.shared.memory.create_connected_server_and_client_session`` (verified in
.venv/.../mcp/shared/memory.py) so a real ``ClientSession`` talks to the server
over in-memory streams — no subprocess, no sockets.

Covered (task "Required evidence" #3):
  (a) tools/list contains the 20 public tools (dev_reload absent when dev off);
  (b) every tool exposes an outputSchema;
  (c) parcel_analyze stub structuredContent validates against analysis-result.schema.json;
  (d) schema://analysis-result resource equals the committed schema file;
  (e) with dev_hot_reload=True, dev_reload runs and a tools/list_changed notification
      is observed via the client message_handler.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import mcp.types as types
import pytest
from jsonschema import Draft202012Validator
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = REPO_ROOT / "schemas"

PUBLIC_TOOLS = {
    "parcel_resolve",
    "parcel_analyze",
    "analysis_get_status",
    "analysis_get_result",
    "planning_fetch",
    "planning_parse_document",
    "constraints_compute",
    "capacity_generate_scenarios",
    "risks_list",
    "sources_collect",
    "report_generate",
    "export_layers",
    "portfolio_analyze",
    "monitoring_create",
    "ruleset_explain",
    "source_healthcheck",
    "cache_warm",
    "document_ingest",
    "manual_override",
    "diagnostics_run",
    # Phase 3 §3.1.3: inline map-preview / verify tool (image content block).
    "map_preview",
    # Phase 4 §4.1.C: generative drawing design-feasibility tool (image + structured).
    "propose_layout",
}


def _load_server(monkeypatch: pytest.MonkeyPatch, *, dev_hot_reload: bool):
    """(Re)import the server module with PLOT_DEV_HOT_RELOAD set, so the dev_reload
    tool registration (module-level ``if _settings.dev_hot_reload``) takes effect."""
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "true" if dev_hot_reload else "false")
    # get_settings is lru_cached; clear it so the env change is picked up.
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    server = importlib.reload(server)
    return server.mcp


@pytest.mark.anyio
async def test_lists_public_tools_dev_off(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch, dev_hot_reload=False)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.list_tools()
        names = {t.name for t in result.tools}
    assert PUBLIC_TOOLS <= names, f"missing: {PUBLIC_TOOLS - names}"
    assert "dev_reload" not in names, "dev_reload must be absent when dev_hot_reload=False"
    assert "selfimprove_run" not in names, "selfimprove_run must be absent when dev off"
    # 20 §10.3 tools + Phase 3 map_preview + Phase 4 propose_layout = 22 public tools.
    assert len(names) == 22


@pytest.mark.anyio
async def test_dev_tools_present_when_dev_on(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch, dev_hot_reload=True)
    async with create_connected_server_and_client_session(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "dev_reload" in names
    assert "selfimprove_run" in names
    assert PUBLIC_TOOLS <= names
    # 22 public + dev_reload + selfimprove_run = 24 when dev_hot_reload=True (≤ ~22 guard
    # applies to the PRODUCTION public surface; dev tools are dev-only).
    assert len(names) == 24


# map_preview returns an MCP image content block and propose_layout returns a
# CallToolResult (image + structuredContent), both structured_output=False, so they
# intentionally have no outputSchema; every other tool must have one.
_NO_OUTPUT_SCHEMA = {"map_preview", "propose_layout"}


@pytest.mark.anyio
async def test_every_tool_has_output_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch, dev_hot_reload=False)
    async with create_connected_server_and_client_session(mcp) as client:
        tools = (await client.list_tools()).tools
    for t in tools:
        if t.name in _NO_OUTPUT_SCHEMA:
            assert t.outputSchema is None, f"{t.name} should have no outputSchema (image tool)"
            continue
        assert t.outputSchema is not None, f"{t.name} has no outputSchema"


@pytest.mark.anyio
async def test_parcel_analyze_structured_content_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch, dev_hot_reload=False)
    schema = json.loads((SCHEMAS / "analysis-result.schema.json").read_text())
    validator = Draft202012Validator(schema)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "parcel_analyze",
            {"input": {"parcel_id": "141201_1.0001.1867/2"}, "analysis_mode": "quick_screening"},
        )
    assert result.isError is False
    assert result.structuredContent is not None
    errors = sorted(validator.iter_errors(result.structuredContent), key=str)
    assert not errors, f"schema violations: {[e.message for e in errors]}"
    assert result.structuredContent["status"] == "partial"
    assert result.structuredContent["decision"] == "NEEDS_MANUAL_REVIEW"


@pytest.mark.anyio
async def test_schema_resource_equals_committed_file(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch, dev_hot_reload=False)
    committed = (SCHEMAS / "analysis-result.schema.json").read_text()
    async with create_connected_server_and_client_session(mcp) as client:
        res = await client.read_resource(AnyUrl("schema://analysis-result"))
    assert len(res.contents) == 1
    content = res.contents[0]
    assert isinstance(content, types.TextResourceContents)
    # Compare parsed JSON (byte-for-byte text and semantic equality both hold).
    assert json.loads(content.text) == json.loads(committed)
    assert content.text == committed


@pytest.mark.anyio
async def test_dev_reload_emits_tools_list_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch, dev_hot_reload=True)
    observed: list[str] = []

    async def message_handler(message: object) -> None:
        # ServerNotification is a RootModel; .root is the concrete notification.
        if isinstance(message, types.ServerNotification) and isinstance(
            message.root, types.ToolListChangedNotification
        ):
            observed.append(message.root.method)

    async with create_connected_server_and_client_session(
        mcp, message_handler=message_handler
    ) as client:
        result = await client.call_tool("dev_reload", {})
        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["reloaded"] is True
        # Let the in-memory transport deliver the notification.
        import anyio

        with anyio.move_on_after(2.0):
            while not observed:
                await anyio.sleep(0.01)

    assert observed == ["notifications/tools/list_changed"], (
        "expected exactly one tools/list_changed notification from dev_reload"
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
