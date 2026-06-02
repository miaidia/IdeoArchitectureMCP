"""Phase 4 MCP wiring test for propose_layout (IMPLEMENTATION_PLAN.md §4.1.C / §4.3).

Uses the SDK in-memory client/server helper so a real ``ClientSession`` calls the new
``propose_layout`` tool and we assert:
  (a) an image content block (mimeType image/png) is returned (the model SEES its drawing);
  (b) structured {score, critique, accepted, violations} is present;
  (c) a hard-violating proposal returns accepted=False with a violation listed, even with
      a high raw coverage score (§14.2 hard-blocker dominance).
"""

from __future__ import annotations

import base64
import importlib

import mcp.types as types
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

GREENERY = {"type": "Polygon", "coordinates": [[[0, 30], [30, 30], [30, 40], [0, 40], [0, 30]]]}

# Inside the sample buildable envelope (4,8)-(46,26).
COMPLIANT = {
    "program_type": "single_family",
    "rectangles": [{"x": 8, "y": 10, "w": 30, "h": 14}],
    "floors": 1,
    "parking_count": 2,
    "greenery_polygons": [GREENERY],
}
# 44x34 from (5,5): high coverage but spills outside envelope + into the hard no-build strip.
SPILLING = {
    "program_type": "single_family",
    "rectangles": [{"x": 5, "y": 5, "w": 44, "h": 34}],
}


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
async def test_propose_layout_returns_image_and_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool("propose_layout", {"proposal": COMPLIANT})

    assert result.isError is False
    # (a) an inline PNG image content block (Phase 0.2 image path).
    images = [c for c in result.content if isinstance(c, types.ImageContent)]
    assert len(images) == 1
    assert images[0].mimeType == "image/png"
    raw = base64.b64decode(images[0].data)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    # (b) structured score/critique/accepted present.
    sc = result.structuredContent
    assert sc is not None
    assert "score" in sc and "critique" in sc and "violations" in sc
    assert sc["accepted"] is True  # compliant proposal is accepted
    assert sc["valid"] is True
    assert sc["violations"] == []
    assert sc["audit"] is not None  # audit-logged (F-0446)


@pytest.mark.anyio
async def test_propose_layout_rejects_hard_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool("propose_layout", {"proposal": SPILLING})

    assert result.isError is False
    sc = result.structuredContent
    assert sc is not None
    # High raw coverage ...
    assert sc["score"]["components"]["coverage_ratio"] > 0.5
    # ... but rejected because it violates a hard blocker (§14.2).
    assert sc["accepted"] is False
    assert sc["valid"] is False
    assert sc["violations"], "a hard-violating proposal must list its violations"
    kinds = {v["kind"] for v in sc["violations"]}
    assert "outside_envelope" in kinds or "intersects_hard_constraint" in kinds


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
