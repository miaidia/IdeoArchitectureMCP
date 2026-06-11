"""Shared geometry builders for the Phase 10 wt_validators golden tests.

All fixtures live in a local EPSG:2180-plausible metric frame anchored at
``(X0, Y0)`` (the DSL ingest guard rejects degree-like coordinates). Buildings
are axis-aligned GeoJSON rectangles whose canonical CCW edge indices are::

    0 = south wall (outward normal (0, -1))
    1 = east  wall (outward normal (+1, 0))
    2 = north wall (outward normal (0, +1))
    3 = west  wall (outward normal (-1, 0))

— the indexing ``BuildingSegment.windowed_walls`` refers to (wall-plane
decomposition, WT §12 ust. 1).
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon

X0 = 500_000.0
Y0 = 500_000.0

#: Canonical edge indices of an axis-aligned rectangle (module docstring).
SOUTH, EAST, NORTH, WEST = 0, 1, 2, 3


def gj_rect(x: float, y: float, w: float, h: float) -> dict[str, Any]:
    """GeoJSON rectangle at frame offset (x, y), CCW from the SW corner."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [X0 + x, Y0 + y],
                [X0 + x + w, Y0 + y],
                [X0 + x + w, Y0 + y + h],
                [X0 + x, Y0 + y + h],
                [X0 + x, Y0 + y],
            ]
        ],
    }


def parcel_square(size: float = 200.0) -> Polygon:
    return Polygon(
        [(X0, Y0), (X0 + size, Y0), (X0 + size, Y0 + size), (X0, Y0 + size)]
    )


def building(
    name: str,
    x: float,
    y: float,
    w: float,
    h: float,
    floors: int,
    *,
    use: str = "mieszkalny",
    windowed_walls: list[int] | None = None,
    declare_windowless: bool = False,
    status: str = "projektowany",
) -> dict[str, Any]:
    """One single-segment rectangular building payload (DSL v2).

    ``windowed_walls=None`` keeps the conservative all-windowed default;
    ``declare_windowless=True`` declares an explicit EMPTY windowed set.
    """
    segment: dict[str, Any] = {
        "polygon": gj_rect(x, y, w, h),
        "floors": floors,
        "use": use,
    }
    if declare_windowless:
        segment["windowed_walls"] = []
    elif windowed_walls is not None:
        segment["windowed_walls"] = windowed_walls
    return {"name": name, "segments": [segment], "status": status}


def masterplan(buildings: list[dict[str, Any]], **extra: Any) -> Any:
    """Validated MasterplanProposal from building payloads (+ roads/parking/...)."""
    from plot_agent.drawing import MasterplanProposal

    return MasterplanProposal.model_validate({"buildings": buildings, **extra})


def checks_for(checks: list[Any], rule_id: str, building: str | None = None) -> list[Any]:
    """Filter RuleChecks by rule id (and building name appearing in the message)."""
    out = [c for c in checks if c.rule_id == rule_id]
    if building is not None:
        out = [c for c in out if building in c.message]
    return out
