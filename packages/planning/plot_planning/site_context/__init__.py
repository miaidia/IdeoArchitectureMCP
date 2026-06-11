"""Site-context modules (Phase 12 / v1 Phase 10 §10.1) — terrain, water,
geology, environment/heritage, roads/utilities, neighborhood, scoring.

Placement rationale (plan PHASE 12 "choose by dependency direction, document"):
the modules live in ``plot_planning`` because they (a) read legal/threshold data
through ``plot_rules`` (EIA ruleset), (b) REUSE the Phase 10 sun/shadow engine
in :mod:`plot_planning.wt_validators.sun` for neighbor shading (same package —
no new dependency edge), and (c) must stay importable by ``plot_agent`` (the
orchestration layer) WITHOUT ``plot_planning`` ever importing connectors —
every function here consumes ALREADY-FETCHED geometries/rasters (§9.4
decoupling: fetching stays above this layer).

:class:`SiteContext` aggregates the per-module results; the §14.1 scores are
computed from it by :func:`compute_site_scores` and mapped into the
``PHASE10_SCORE_HOOKS`` of ``plot_agent.selfimprove.evaluator`` (plan delta 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plot_domain import Recommendation, RiskItem, UnknownItem

from plot_planning.site_context.environment import (
    RULE_EIA_SCREENING,
    EnvironmentAnalysis,
    analyze_environment,
    eia_screening,
    heritage_interventions,
)
from plot_planning.site_context.geology import (
    GEOTECH_CLASSES,
    GeologyAnalysis,
    analyze_geology,
)
from plot_planning.site_context.neighborhood import (
    neighbor_shading_impact,
    neighbors_from_features,
    windows_at_boundary_precheck,
)
from plot_planning.site_context.roads import (
    AccessAnalysis,
    analyze_access,
    check_zjazd_kdw,
)
from plot_planning.site_context.scoring import SiteScore, compute_site_scores
from plot_planning.site_context.terrain import (
    EARTHWORKS_CLASSES,
    TerrainAnalysis,
    TerrainConfig,
    analyze_terrain,
    terrain_unavailable,
)
from plot_planning.site_context.water import WaterAnalysis, analyze_water

__all__ = [
    "EARTHWORKS_CLASSES",
    "GEOTECH_CLASSES",
    "RULE_EIA_SCREENING",
    "AccessAnalysis",
    "EnvironmentAnalysis",
    "GeologyAnalysis",
    "SiteContext",
    "SiteScore",
    "TerrainAnalysis",
    "TerrainConfig",
    "WaterAnalysis",
    "analyze_access",
    "analyze_environment",
    "analyze_geology",
    "analyze_terrain",
    "analyze_water",
    "check_zjazd_kdw",
    "compute_site_scores",
    "eia_screening",
    "heritage_interventions",
    "neighbor_shading_impact",
    "neighbors_from_features",
    "terrain_unavailable",
    "windows_at_boundary_precheck",
]


@dataclass
class SiteContext:
    """Aggregated site-context results for one analysis (v1 Phase 10 §10.1).

    A ``None`` module means it did NOT run (its themes stay unknown). Stored
    process-locally per analysis (the masterplan integration reads it back for
    earthworks/flood/heritage/zjazd checks — plan delta 1).
    """

    analysis_id: str | None = None
    terrain: TerrainAnalysis | None = None
    water: WaterAnalysis | None = None
    geology: GeologyAnalysis | None = None
    environment: EnvironmentAnalysis | None = None
    access: AccessAnalysis | None = None
    neighbors: list[Any] = field(default_factory=list)  # NeighborBuilding records
    neighbor_notes: list[str] = field(default_factory=list)
    windows_at_boundary: list[dict[str, Any]] = field(default_factory=list)

    def all_risks(self) -> list[RiskItem]:
        out: list[RiskItem] = []
        for module in (self.water, self.geology, self.environment, self.access):
            if module is not None:
                out.extend(module.risks)
        return out

    def all_unknowns(self) -> list[UnknownItem]:
        out: list[UnknownItem] = []
        for module in (
            self.terrain,
            self.water,
            self.geology,
            self.environment,
            self.access,
        ):
            if module is not None:
                out.extend(module.unknowns)
        return out

    def all_recommendations(self) -> list[Recommendation]:
        out: list[Recommendation] = []
        for module in (self.geology, self.environment, self.access):
            if module is not None:
                out.extend(module.recommendations)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "terrain": self.terrain.to_dict() if self.terrain else None,
            "water": self.water.to_dict() if self.water else None,
            "geology": self.geology.to_dict() if self.geology else None,
            "environment": self.environment.to_dict() if self.environment else None,
            "access": self.access.to_dict() if self.access else None,
            "neighbors": [
                {"name": n.name, "height_m": n.height_m} for n in self.neighbors
            ],
            "neighbor_notes": self.neighbor_notes,
            "windows_at_boundary": self.windows_at_boundary,
        }
