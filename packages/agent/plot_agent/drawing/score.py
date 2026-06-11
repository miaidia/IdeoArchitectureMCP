"""Explainable proposal scoring (Phase 4 §4.1.B.3 / §14).

``score_proposal`` computes the layout components currently derivable from geometry +
rulesets:

* ``coverage_ratio`` — footprint / parcel area vs the ruleset ``default_max_coverage_ratio``.
* ``footprint_area`` — absolute footprint size (utilisation signal, §8.5).
* ``biologically_active_area`` (PBC) — greenery / parcel area estimate (§8.5 min PBC).
* ``parking_fit`` — proposed parking vs a coarse demand from program + floors (§8.5).
* ``envelope_utilization`` — footprint / buildable-envelope area (§8.4 fit).

Each component is EXPLAINABLE (positive/negative factors). Hard violations run FIRST
(``validate_hard``); if any exist the proposal CANNOT be valid (``valid=False``) and the
total is capped, no matter how good the geometry looks (§14.2, §4.4). Unknown §14 scores
(sun/geotech/...) are carried via the self-improve evaluator hooks, never faked (§20.10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plot_agent.context import AnalysisContext
from plot_agent.drawing.proposal import LayoutProposal, MasterplanProposal
from plot_agent.drawing.validate import Violation, validate_hard, validate_hard_masterplan
from plot_agent.rules_access import max_coverage_ratio

# Coarse parking demand per program type (spaces per floor-unit). Real demand indicators
# come from MPZP/WT parsing in Phase 8/9; this is a placeholder ratio, flagged as such.
_PARKING_DEMAND_PER_FLOOR: dict[str, float] = {
    "single_family": 1.0,
    "multifamily": 1.2,
    "services": 2.0,
    "warehouse": 1.0,
    "garage": 0.0,
    "mixed": 1.5,
}
# Soft target minimum biologically-active-area fraction (placeholder; real min PBC from
# ruleset/MPZP in Phase 8/9). Used only to explain the PBC component, not as a hard rule.
_TARGET_PBC_FRACTION = 0.25


@dataclass
class ProposalScore:
    """Explainable score of a layout proposal (§4.1.B.3)."""

    components: dict[str, float] = field(default_factory=dict)
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    total: float = 0.0
    valid: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "components": self.components,
            "positive_factors": self.positive_factors,
            "negative_factors": self.negative_factors,
            "violations": [v.to_dict() for v in self.violations],
            "total": self.total,
            "valid": self.valid,
        }


def score_proposal(proposal: LayoutProposal, context: AnalysisContext) -> ProposalScore:
    """Score a proposal AFTER the hard-constraint guard (§14.2 hard-blocker dominance)."""
    # 1) Hard guard FIRST — a hard violation can never be a valid proposal (§4.4).
    violations = validate_hard(proposal, context)

    components: dict[str, float] = {}
    pos: list[str] = []
    neg: list[str] = []

    parcel_area = context.parcel_area_m2()
    env_area = context.envelope_area_m2()
    try:
        footprint_area = proposal.footprint_area_m2()
    except ValueError:
        footprint_area = 0.0

    # coverage_ratio vs ruleset max --------------------------------------- #
    ratio_max, rule_id, found = max_coverage_ratio(context.ruleset)
    coverage = footprint_area / parcel_area if parcel_area > 0 else 0.0
    components["footprint_area_m2"] = round(footprint_area, 2)
    components["coverage_ratio"] = round(coverage, 4)
    if found:
        components["coverage_ratio_max"] = round(ratio_max, 4)
        if coverage > ratio_max:
            neg.append(f"coverage {coverage:.0%} exceeds ruleset max {ratio_max:.0%} (rule {rule_id})")
            coverage_component = max(0.0, 0.5 * (ratio_max / coverage)) if coverage > 0 else 0.0
        else:
            pos.append(f"coverage {coverage:.0%} within ruleset max {ratio_max:.0%} (rule {rule_id})")
            coverage_component = 0.5 + 0.5 * (coverage / ratio_max) if ratio_max > 0 else 0.0
    else:
        # Ruleset value missing — do NOT fake a pass; treat coverage as low-confidence.
        neg.append("ruleset max coverage unknown (default_max_coverage_ratio missing)")
        coverage_component = 0.3
    components["coverage_component"] = round(coverage_component, 4)

    # envelope_utilization ------------------------------------------------ #
    utilization = min(1.0, footprint_area / env_area) if env_area > 0 else 0.0
    components["envelope_utilization"] = round(utilization, 4)
    if utilization > 0:
        pos.append(f"uses {utilization:.0%} of the buildable envelope")

    # biologically-active-area (PBC) estimate ----------------------------- #
    greenery = proposal.greenery_area_m2()
    pbc_fraction = greenery / parcel_area if parcel_area > 0 else 0.0
    components["biologically_active_area_fraction"] = round(pbc_fraction, 4)
    if pbc_fraction >= _TARGET_PBC_FRACTION:
        pos.append(f"biologically-active area {pbc_fraction:.0%} meets soft target {_TARGET_PBC_FRACTION:.0%}")
        pbc_component = 1.0
    else:
        neg.append(f"biologically-active area {pbc_fraction:.0%} below soft target {_TARGET_PBC_FRACTION:.0%}")
        pbc_component = pbc_fraction / _TARGET_PBC_FRACTION if _TARGET_PBC_FRACTION > 0 else 0.0
    components["pbc_component"] = round(pbc_component, 4)

    # parking_fit --------------------------------------------------------- #
    demand_per_floor = _PARKING_DEMAND_PER_FLOOR.get(proposal.program_type, 1.0)
    demand = demand_per_floor * proposal.floors
    components["parking_demand_estimate"] = round(demand, 2)
    if demand <= 0:
        parking_component = 1.0
    elif proposal.parking_count >= demand:
        pos.append(f"parking {proposal.parking_count} meets estimated demand {demand:.1f}")
        parking_component = 1.0
    else:
        neg.append(f"parking {proposal.parking_count} below estimated demand {demand:.1f}")
        parking_component = proposal.parking_count / demand
    components["parking_component"] = round(parking_component, 4)

    # Aggregate (geometry-weighted) — only meaningful when valid. --------- #
    raw_total = round(
        0.35 * coverage_component
        + 0.30 * utilization
        + 0.20 * pbc_component
        + 0.15 * parking_component,
        4,
    )

    if violations:
        # Hard blocker dominates: cap total and mark invalid (§14.2, §4.4). The raw
        # components stay visible (explainability) but the proposal is NOT valid.
        for v in violations:
            neg.append(f"HARD VIOLATION: {v.detail}")
        return ProposalScore(
            components=components,
            positive_factors=pos,
            negative_factors=neg,
            violations=violations,
            total=min(raw_total, 0.0),  # hard violation zeroes the usable total
            valid=False,
        )

    return ProposalScore(
        components=components,
        positive_factors=pos,
        negative_factors=neg,
        violations=[],
        total=raw_total,
        valid=True,
    )


def score_masterplan(
    proposal: MasterplanProposal,
    context: AnalysisContext,
    metrics: Any,
    *,
    inter_building_checks: list[Any] | None = None,
) -> ProposalScore:
    """Score a masterplan proposal from its capacity metrics (Phase 9 §9.1.4).

    ``metrics`` is a :class:`plot_planning.MasterplanMetrics` (typed ``Any`` to keep the
    plot_planning → plot_agent dependency direction one-way). The hard guard
    (:func:`validate_hard_masterplan`) runs FIRST and keeps §14.2 hard-blocker
    dominance. Components: coverage (ruleset/indicator max), envelope utilization (of
    the NEW buildings), PBC balance, parking balance — an UNKNOWN parking demand (no
    MPZP indicator) lowers the component and is explained, never faked as a pass.

    ``inter_building_checks`` is the Phase 10 hook: WT §12/§13/§19/§39/§40/§60 + ppoż
    RuleChecks evaluated between buildings. It defaults to EMPTY (no checks yet); any
    provided check with ``status == "fail"`` and ``severity == "hard"`` becomes a hard
    violation here.
    """
    violations = validate_hard_masterplan(proposal, context)
    for check in inter_building_checks or []:
        if isinstance(check, dict):
            status = str(check.get("status", ""))
            severity = str(check.get("severity", "hard"))
            message = str(check.get("message", check))
        else:  # RuleCheck-like (Phase 10 wt_validators)
            status = str(getattr(check, "status", ""))
            severity = str(getattr(check, "severity", "hard"))
            message = str(getattr(check, "message", check))
        if "fail" in status and severity == "hard":
            violations.append(Violation(kind="inter_building_rule", detail=message))

    components: dict[str, float] = {}
    pos: list[str] = []
    neg: list[str] = []

    parcel_area = context.parcel_area_m2()
    env_area = context.envelope_area_m2()
    totals = metrics.totals
    coverage = float(totals.get("coverage_ratio", 0.0))
    components["powierzchnia_zabudowy_m2"] = float(totals.get("powierzchnia_zabudowy_m2", 0.0))
    components["coverage_ratio"] = round(coverage, 4)
    components["pum_m2"] = float(totals.get("pum_m2", 0.0))
    components["puu_m2"] = float(totals.get("puu_m2", 0.0))
    components["mieszkania_estimate"] = float(totals.get("mieszkania_estimate", 0))

    # coverage vs the indicator limit (when extracted) or the ruleset max ------- #
    indicator_limit = next(
        (
            c.get("limit")
            for c in metrics.indicator_checks
            if c.get("indicator") == "max_coverage_ratio" and isinstance(c.get("limit"), float)
        ),
        None,
    )
    ratio_max: float
    rule_id: str | None
    found: bool
    if indicator_limit is not None:
        ratio_max, rule_id, found = float(indicator_limit), "max_coverage_ratio (MPZP)", True
    else:
        ratio_max, rule_id, found = max_coverage_ratio(context.ruleset)
    if found:
        components["coverage_ratio_max"] = round(ratio_max, 4)
        if coverage > ratio_max:
            neg.append(f"coverage {coverage:.0%} exceeds max {ratio_max:.0%} ({rule_id})")
            coverage_component = max(0.0, 0.5 * (ratio_max / coverage)) if coverage > 0 else 0.0
        else:
            pos.append(f"coverage {coverage:.0%} within max {ratio_max:.0%} ({rule_id})")
            coverage_component = 0.5 + 0.5 * (coverage / ratio_max) if ratio_max > 0 else 0.0
    else:
        neg.append("max coverage unknown (no MPZP indicator, no ruleset value)")
        coverage_component = 0.3
    components["coverage_component"] = round(coverage_component, 4)

    # envelope utilization of the NEW buildings --------------------------------- #
    proposed_area = float(proposal.proposed_footprint_geometry().area)
    utilization = min(1.0, proposed_area / env_area) if env_area > 0 else 0.0
    components["envelope_utilization"] = round(utilization, 4)
    if utilization > 0:
        pos.append(f"new buildings use {utilization:.0%} of the buildable envelope")

    # PBC balance from the capacity metrics ------------------------------------- #
    pbc_ratio = metrics.pbc.get("pbc_ratio")
    required_pbc = metrics.pbc.get("required_pbc_ratio")
    if isinstance(pbc_ratio, float):
        components["pbc_ratio"] = round(pbc_ratio, 4)
    if isinstance(pbc_ratio, float) and isinstance(required_pbc, float) and required_pbc > 0:
        if pbc_ratio >= required_pbc:
            pos.append(f"PBC {pbc_ratio:.0%} meets required {required_pbc:.0%}")
            pbc_component = 1.0
        else:
            neg.append(f"PBC {pbc_ratio:.0%} below required {required_pbc:.0%}")
            pbc_component = pbc_ratio / required_pbc
    else:
        neg.append("PBC requirement or balance unknown — not faked as a pass")
        pbc_component = 0.3
    components["pbc_component"] = round(pbc_component, 4)

    # parking balance from the capacity metrics (MPZP indicators only) ---------- #
    demand = metrics.parking.get("demand_spaces")
    supply = float(metrics.parking.get("supply_spaces", 0))
    components["parking_supply"] = supply
    if isinstance(demand, int | float):
        demand_f = float(demand)
        components["parking_demand"] = round(demand_f, 2)
        if demand_f <= 0 or supply >= demand_f:
            pos.append(f"parking {supply:.0f} covers MPZP demand {demand_f:.1f}")
            parking_component = 1.0
        else:
            neg.append(f"parking {supply:.0f} below MPZP demand {demand_f:.1f}")
            parking_component = supply / demand_f
    else:
        neg.append(
            "parking demand unknown — MPZP indicator missing; ask the gmina "
            "(never defaulted, §0v2.4)"
        )
        parking_component = 0.3
    components["parking_component"] = round(parking_component, 4)

    parcel_note = f"parcel {parcel_area:,.0f} m²"
    raw_total = round(
        0.35 * coverage_component
        + 0.30 * utilization
        + 0.20 * pbc_component
        + 0.15 * parking_component,
        4,
    )

    if violations:
        for v in violations:
            neg.append(f"HARD VIOLATION: {v.detail}")
        return ProposalScore(
            components=components,
            positive_factors=pos,
            negative_factors=neg,
            violations=violations,
            total=min(raw_total, 0.0),  # hard violation zeroes the usable total (§14.2)
            valid=False,
        )

    pos.append(parcel_note)
    return ProposalScore(
        components=components,
        positive_factors=pos,
        negative_factors=neg,
        violations=[],
        total=raw_total,
        valid=True,
    )
