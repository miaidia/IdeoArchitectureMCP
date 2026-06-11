"""MCP image-content path tests (Phase 3 §3.1.3 / required evidence #5).

Uses the SDK in-memory client/server helper so a real ``ClientSession`` calls the
preview path and we assert a proper MCP image content block reaches the client:

  (a) ``map_preview`` returns an ``image`` content block with mimeType "image/png"
      and non-empty base64 ``data`` (the "screenshot for verification" channel).
  (b) ``report_generate(format="png")`` returns a ``resource_link``-shaped descriptor
      by DEFAULT (NFR-PERF-009) — image bytes are NOT inlined into the result.
  (c) the ``analysis://{id}/map-preview.png`` resource returns PNG blob bytes.
"""

from __future__ import annotations

import base64
import importlib

import mcp.types as types
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl


def _load_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    server = importlib.reload(server)
    return server.mcp


@pytest.mark.anyio
async def test_map_preview_returns_inline_image_content_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        # No analysis_id → the documented Phase 3 sample preview. (An UNKNOWN id
        # is a hard error since the review m1 fix — no silent sample fallback.)
        result = await client.call_tool("map_preview", {})

    assert result.isError is False
    # Exactly one content block, and it is an MCP image block (Phase 0.2).
    image_blocks = [c for c in result.content if isinstance(c, types.ImageContent)]
    assert len(image_blocks) == 1, f"expected one image block, got {result.content!r}"
    block = image_blocks[0]
    assert block.type == "image"
    assert block.mimeType == "image/png"
    assert block.data, "image data must be non-empty base64"
    # Valid base64 decoding to PNG magic bytes.
    raw = base64.b64decode(block.data)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(raw) > 1000


@pytest.mark.anyio
async def test_report_generate_png_returns_resource_link_not_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "report_generate", {"analysis_id": "demo-2", "format": "png"}
        )
    assert result.isError is False
    sc = result.structuredContent
    assert sc is not None
    # Default delivery is a resource_link descriptor — no inline image (NFR-PERF-009).
    assert sc["status"] == "rendered"
    assert sc["resource_link"] == "analysis://demo-2/map-preview.png"
    assert sc["mime_type"] == "image/png"
    assert sc["byte_size"] > 1000
    assert sc["style_metadata"]["crs"] == "EPSG:2180"
    # No image content block inlined into the structured result path.
    assert not any(isinstance(c, types.ImageContent) for c in result.content)


@pytest.mark.anyio
async def test_map_preview_resource_returns_png_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        res = await client.read_resource(AnyUrl("analysis://demo-3/map-preview.png"))
    assert len(res.contents) == 1
    content = res.contents[0]
    # Bytes-returning resource → BlobResourceContents with base64 blob (NFR-PERF-009).
    assert isinstance(content, types.BlobResourceContents)
    assert content.mimeType == "image/png"
    raw = base64.b64decode(content.blob)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
