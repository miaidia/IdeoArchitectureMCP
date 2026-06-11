"""Phase 4 MCP wiring test for propose_layout (IMPLEMENTATION_PLAN.md §4.1.C / §4.3).

Uses the SDK in-memory client/server helper so a real ``ClientSession`` calls the new
``propose_layout`` tool and we assert:
  (a) an image content block (mimeType image/png) is returned (the model SEES its drawing);
  (b) structured {score, critique, accepted, violations} is present;
  (c) a hard-violating proposal returns accepted=False with a violation listed, even with
      a high raw coverage score (§14.2 hard-blocker dominance).

Phase 9 adds the masterplan path (DSL v2): a payload with a ``buildings`` key returns
the renderer-v2 image PLUS capacity metrics (basis-tagged), unknowns and a stored
variant whose metrics are served by the
``analysis://{analysis_id}/masterplan/{variant_id}/metrics.json`` resource — while the
OLD LayoutProposal payloads above keep working byte-for-byte (backwards-compat tests
unchanged).
"""

from __future__ import annotations

import base64
import importlib
import json

import mcp.types as types
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

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

# Phase 9 masterplan payload — buildings inside the sample envelope (4,8)-(46,26);
# the zabytek is exempt from the envelope guard, parking/greenery sit in the parcel.
MASTERPLAN = {
    "buildings": [
        {
            "name": "Budynek 1",
            "stage": 1,
            "segments": [
                {"rectangles": [{"x": 8, "y": 10, "w": 20, "h": 6}], "floors": 4,
                 "use": "mieszkalny", "ground_floor_use": "uslugowy"},
                {"rectangles": [{"x": 8, "y": 16, "w": 6, "h": 8}], "floors": 7,
                 "use": "mieszkalny"},
            ],
        },
        {
            "name": "Zabytek",
            "status": "zabytek_do_remontu",
            "segments": [
                {"rectangles": [{"x": 32, "y": 10, "w": 10, "h": 8}], "floors": 2,
                 "use": "uslugowy"}
            ],
        },
    ],
    "roads": [
        {"centerline": {"type": "LineString", "coordinates": [[4, 7], [46, 7]]},
         "width_m": 2.0, "function": "kdw"}
    ],
    "parking": [
        {"kind": "naziemny",
         "polygon": {"type": "Polygon",
                     "coordinates": [[[30, 20], [40, 20], [40, 25], [30, 25], [30, 20]]]},
         "spaces": 20}
    ],
    "greenery_polygons": [GREENERY],
    "playgrounds": [
        {"type": "Polygon",
         "coordinates": [[[35, 30], [45, 30], [45, 38], [35, 38], [35, 30]]]}
    ],
}
MASTERPLAN_INDICATORS = {"parking_per_mieszkanie": 1.0, "parking_per_100m2_uslug": 1.0}


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


@pytest.mark.anyio
async def test_propose_layout_masterplan_returns_metrics_and_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 9: a buildings payload → renderer-v2 image + metrics + variant resource."""
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "propose_layout",
            {"proposal": MASTERPLAN, "indicators": MASTERPLAN_INDICATORS},
        )

        assert result.isError is False
        images = [c for c in result.content if isinstance(c, types.ImageContent)]
        assert len(images) == 1 and images[0].mimeType == "image/png"
        assert base64.b64decode(images[0].data)[:8] == b"\x89PNG\r\n\x1a\n"

        sc = result.structuredContent
        assert sc is not None
        assert sc["schema_version"] == 2
        assert sc["valid"] is True
        assert sc["violations"] == []
        # Capacity metrics with basis metadata (anti-pattern §0v2.4 guard).
        totals = sc["metrics"]["totals"]
        assert totals["buildings"] == 2
        assert totals["basis"]["pum_m2"] == "industry_heuristic"
        assert sc["metrics"]["stage_table"][-1]["etap"] == "SUMA"
        assert sc["metrics"]["parking"]["demand_basis"] == "planning_indicator"
        assert "PN-ISO 9836" in sc["metrics"]["config_basis"]["measurement_standard"]

        # The stored variant's metrics are served as an MCP resource (Phase 9 §9.1.5).
        variant_id = sc["variant_id"]
        res = await client.read_resource(
            AnyUrl(f"analysis://adhoc/masterplan/{variant_id}/metrics.json")
        )
        payload = json.loads(res.contents[0].text)  # type: ignore[union-attr]
        assert payload["status"] == "ok"
        assert payload["totals"] == totals
        assert len(payload["buildings"]) == 2


@pytest.mark.anyio
async def test_propose_layout_masterplan_outside_parcel_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A building spilling past the parcel is a SCORING-time hard violation (§14.2)."""
    bad = {
        "buildings": [
            {"name": "Poza działką",
             "segments": [{"rectangles": [{"x": 45, "y": 35, "w": 20, "h": 20}],
                           "floors": 3, "use": "mieszkalny"}]}
        ]
    }
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool("propose_layout", {"proposal": bad})

    assert result.isError is False
    sc = result.structuredContent
    assert sc is not None
    assert sc["accepted"] is False
    assert sc["valid"] is False
    kinds = {v["kind"] for v in sc["violations"]}
    assert "outside_parcel" in kinds


@pytest.mark.anyio
async def test_propose_layout_masterplan_self_intersecting_rings_no_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1 regression: LLM-style self-intersecting greenery/playground rings that the
    parser accepts (make_valid-repairable) must yield a RESULT through the full MCP
    propose_layout call — not an unhandled GEOSException from the capacity engine."""
    # Bowtie greenery (0,30)→(15,40)→(15,30)→(0,40): two 37.5 m² triangles → 75 m².
    bowtie_greenery = {
        "type": "Polygon",
        "coordinates": [[[0, 30], [15, 40], [15, 30], [0, 40], [0, 30]]],
    }
    # Bowtie playground overlapping the greenery (triggers the difference() crash).
    bowtie_playground = {
        "type": "Polygon",
        "coordinates": [[[5, 30], [20, 42], [20, 30], [5, 42], [5, 30]]],
    }
    payload = {
        "buildings": [
            {"name": "Budynek 1",
             "segments": [{"rectangles": [{"x": 8, "y": 10, "w": 20, "h": 6}],
                           "floors": 4, "use": "mieszkalny"}]}
        ],
        "greenery_polygons": [bowtie_greenery],
        "playgrounds": [bowtie_playground],
        "retention": [
            {"type": "Polygon",
             "coordinates": [[[40, 30], [46, 30], [46, 34], [40, 34], [40, 30]]]}
        ],
    }
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "propose_layout", {"proposal": payload, "indicators": MASTERPLAN_INDICATORS}
        )

    assert result.isError is False, "repaired-geometry payload must not crash the tool"
    sc = result.structuredContent
    assert sc is not None
    assert sc["schema_version"] == 2
    # The REPAIRED greenery area reaches the PBC balance (not the raw 0.0).
    assert sc["metrics"]["pbc"]["greenery_m2"] == pytest.approx(75.0)
    assert sc["metrics"]["playground"]["provided_m2"] > 0

    # F3 regression: retention is persisted on the stored variant for re-rendering.
    from plot_agent.drawing import DEFAULT_VARIANT_STORE

    variant = DEFAULT_VARIANT_STORE.get(sc["variant_id"])
    assert variant is not None
    assert len(variant.retention) == 1
    assert len(variant.roads) == 0


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
