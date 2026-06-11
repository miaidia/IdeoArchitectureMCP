"""Phase 10 — inter-building WT/ppoż rule validators (plan §10.1, "real architecture" gate).

Pure geometric validators over the masterplan DSL v2. Each validator constructs
the GEOMETRIC inputs (distances, heights, angles, areas, hours) and resolves
every legal threshold through :func:`plot_rules.evaluate` against the Phase 8
ruleset YAMLs — no legal value lives in this package (grep guard: plan §10.3).
All validators return ``list[RuleCheck]`` with ``geometry_evidence`` (GeoJSON of
the offending geometry pair/zone) and a human ``message`` naming the buildings.

:func:`run_inter_building_checks` orchestrates them all, consumes the audited
:class:`plot_rules.OverrideStore` (matched by ``analysis_id`` + ``rule_id`` +
optional evaluation SUBJECT — building name / sorted ``"pair:A|B"`` /
``"parking:N"`` via the ``"<rule_id>#<subject>"`` target-id syntax; a bare rule
id applies rule-wide → passed into the engine's override hook, F-0137) and
returns a deterministically sorted list — the ``inter_building_checks`` hook of
:func:`plot_agent.drawing.score.score_masterplan` (fail + hard → hard-blocker
dominance, §14.2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry
from shapely.geometry.base import BaseGeometry

from plot_planning.capacity import MasterplanMetrics
from plot_planning.wt_validators.boundary import check_boundary_setbacks
from plot_planning.wt_validators.config import (
    CONSUMED_RULE_IDS,
    RULE_PPOZ_271,
    RULE_PPOZ_DROGA,
    RULE_WT12,
    RULE_WT13,
    RULE_WT19,
    RULE_WT21,
    RULE_WT39,
    RULE_WT40,
    RULE_WT60,
    ValidatorConfig,
)
from plot_planning.wt_validators.context import (
    MasterplanLike,
    NeighborBuilding,
    ValidationContext,
    Wall,
    build_context,
)
from plot_planning.wt_validators.fire import check_fire_separation
from plot_planning.wt_validators.fire_road import check_fire_road
from plot_planning.wt_validators.parking import check_parking_distances
from plot_planning.wt_validators.pbc_playground import check_pbc_playground
from plot_planning.wt_validators.przeslanianie import check_przeslanianie
from plot_planning.wt_validators.sun import (
    check_naslonecznienie,
    playground_insolation_hours,
    shadow_polygon,
)

__all__ = [
    "CONSUMED_RULE_IDS",
    "RULE_PPOZ_271",
    "RULE_PPOZ_DROGA",
    "RULE_WT12",
    "RULE_WT13",
    "RULE_WT19",
    "RULE_WT21",
    "RULE_WT39",
    "RULE_WT40",
    "RULE_WT60",
    "MasterplanLike",
    "NeighborBuilding",
    "ValidationContext",
    "ValidatorConfig",
    "Wall",
    "build_context",
    "check_boundary_setbacks",
    "check_fire_road",
    "check_fire_separation",
    "check_naslonecznienie",
    "check_parking_distances",
    "check_pbc_playground",
    "check_przeslanianie",
    "playground_insolation_hours",
    "run_inter_building_checks",
    "shadow_polygon",
]


def run_inter_building_checks(
    proposal: MasterplanLike,
    parcel: BaseGeometry,
    registry: RulesetRegistry,
    *,
    neighbors: Sequence[NeighborBuilding] = (),
    srodmiejska: bool | None = None,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    config: ValidatorConfig | None = None,
    metrics: MasterplanMetrics | None = None,
    indicators: Mapping[str, Any] | None = None,
    strefa_pozarowa_limit_m2: float | None = None,
) -> list[RuleCheck]:
    """Run ALL Phase 10 inter-building validators over a masterplan proposal.

    Parameters mirror the per-validator contracts; ``neighbors`` are existing
    buildings outside the proposal (geometry + height — built from the fetched
    BDOT10k features by ``plot_planning.site_context.neighbors_from_features``
    since Phase 12), ``srodmiejska`` defaults to the proposal's own flag,
    ``metrics`` lets the caller REUSE already-computed Phase 9 capacity metrics
    (recomputed here otherwise), ``strefa_pozarowa_limit_m2`` enables the §273
    joint-strefa exemption (not in the YAML corpus — caller-supplied or absent
    → conservative default). The result is sorted (rule_id, message) so the
    same proposal always yields byte-identical JSON (determinism property,
    plan §10.3.h).
    """
    cfg = config or ValidatorConfig()
    ctx = build_context(
        proposal, parcel, neighbors=neighbors, srodmiejska=srodmiejska, config=cfg
    )
    common: dict[str, Any] = {
        "mode": mode,
        "overrides": overrides,
        "analysis_id": analysis_id,
    }
    checks: list[RuleCheck] = []
    checks += check_boundary_setbacks(ctx, registry, **common)
    checks += check_przeslanianie(ctx, registry, **common)
    checks += check_naslonecznienie(ctx, registry, **common)
    checks += check_parking_distances(ctx, registry, **common)
    checks += check_pbc_playground(
        ctx, registry, metrics=metrics, indicators=indicators, **common
    )
    checks += check_fire_separation(
        ctx, registry, strefa_pozarowa_limit_m2=strefa_pozarowa_limit_m2, **common
    )
    checks += check_fire_road(ctx, registry, **common)
    checks.sort(key=lambda c: (c.rule_id, c.message))
    return checks
