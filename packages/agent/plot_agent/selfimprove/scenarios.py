"""Golden scenarios for the self-improve dev-loop (Phase 4 §4.1.A.1).

A :class:`GoldenScenario` bundles a parcel + buildable-envelope + constraints + analysis
mode + investment goal + the expected scores/decision. Scenarios are built on the
Phase 3 sample geometry (``plot_reports.preview.sample_preview_layers``) so we reuse the
demo parcel/envelope/constraint shapes and need no Phase 5/7 code (task note "reuse the
sample preview").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plot_reports.preview import sample_preview_layers
from plot_reports.render import LayerRole
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry


@dataclass
class GoldenScenario:
    """A named end-to-end scenario the dev-loop scores (§4.1.A.1).

    ``expected`` holds the golden numbers the runner diffs against (e.g.
    ``{"decision": "...", "scores": {"buildability_score": {"min": 0.2}}}``). Bounds are
    soft (min/max) because the exact value depends on ruleset values that may be edited.
    """

    id: str
    parcel: BaseGeometry | dict[str, Any]
    buildable_envelope: BaseGeometry | dict[str, Any]
    hard_constraints: list[BaseGeometry | dict[str, Any]] = field(default_factory=list)
    soft_constraints: list[BaseGeometry | dict[str, Any]] = field(default_factory=list)
    analysis_mode: str = "design_feasibility"
    investment_goal: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)


def _sample_geometry() -> dict[str, BaseGeometry]:
    """Pull parcel / envelope / hard / soft geometry out of the Phase 3 sample layers."""
    layers = sample_preview_layers()
    by_role: dict[LayerRole, list[BaseGeometry]] = {}
    for layer in layers:
        by_role.setdefault(layer.role, []).extend(layer.shapely_geometries())
    return {
        "parcel": by_role[LayerRole.PARCEL][0],
        "envelope": by_role[LayerRole.BUILDABLE_ENVELOPE][0],
        "hard": by_role[LayerRole.NO_BUILD][0],
        "soft": by_role[LayerRole.CONSTRAINT_SOFT][0],
    }


def sample_scenarios() -> list[GoldenScenario]:
    """Three sample golden scenarios on the Phase 3 sample geometry (§4.1.A.1).

    1. ``sample-single-family`` — the full sample parcel (envelope ≈ 37% of parcel).
    2. ``sample-tight-envelope`` — a deliberately small envelope (low buildability).
    3. ``sample-corner-services`` — a corner-ish parcel for a services program.
    """
    g = _sample_geometry()
    parcel: BaseGeometry = g["parcel"]
    envelope: BaseGeometry = g["envelope"]
    hard: BaseGeometry = g["hard"]
    soft: BaseGeometry = g["soft"]

    # 2: a much tighter envelope (10 m x 6 m = 60 m² of a 2000 m² parcel ≈ 3%).
    tight_envelope = Polygon([(6, 10), (16, 10), (16, 16), (6, 16)])

    # 3: an L-ish corner parcel + envelope for a services program.
    corner_parcel = Polygon([(0, 0), (40, 0), (40, 18), (18, 18), (18, 40), (0, 40)])
    corner_envelope = Polygon([(4, 4), (34, 4), (34, 14), (4, 14)])

    return [
        GoldenScenario(
            id="sample-single-family",
            parcel=parcel,
            buildable_envelope=envelope,
            hard_constraints=[hard],
            soft_constraints=[soft],
            analysis_mode="design_feasibility",
            investment_goal={"type": "single_family"},
            expected={
                "decision": "OK_WITH_RISKS",
                "scores": {"buildability_score": {"min": 0.30, "max": 0.45}},
            },
        ),
        GoldenScenario(
            id="sample-tight-envelope",
            parcel=parcel,
            buildable_envelope=tight_envelope,
            hard_constraints=[hard],
            soft_constraints=[soft],
            analysis_mode="design_feasibility",
            investment_goal={"type": "single_family"},
            expected={
                "decision": "NEEDS_MANUAL_REVIEW",
                "scores": {"buildability_score": {"max": 0.10}},
            },
        ),
        GoldenScenario(
            id="sample-corner-services",
            parcel=corner_parcel,
            buildable_envelope=corner_envelope,
            hard_constraints=[],
            soft_constraints=[],
            analysis_mode="design_feasibility",
            investment_goal={"type": "services"},
            expected={
                "scores": {"buildability_score": {"min": 0.20}},
            },
        ),
    ]
