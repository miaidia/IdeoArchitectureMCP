"""Explainable score evaluator for the self-improve loop (Phase 4 §4.1.A.2 / §14).

Implements only the §14.1 scores that are **computable NOW** from geometry + rulesets:

* ``buildability_score`` — buildable-envelope area / parcel area, optionally adjusted by
  whether a proposed footprint fits inside the envelope (§8.4 envelope/footprint variants).
* ``data_confidence_score`` — a §14 ``data_confidence_score`` proxy from how much input is
  actually present (envelope known? rules loaded? constraints supplied?).
* ``coverage_vs_ruleset`` — proposed/expected footprint coverage vs the ruleset
  ``default_max_coverage_ratio`` from ``rulesets/PL/planning/mn-coverage.yaml``.

Every score is EXPLAINABLE (§14.2 / NFR-AUD-006): positive + negative factor lists and a
confidence. Phase 12 wires the site-context §14 scores (terrain/geotech/heritage/
infrastructure/...) through ``site_scores`` (computed by
``plot_planning.site_context.compute_site_scores``); a hook without computed site
data stays ``None`` / ``unknown`` with the module's reason — NOT faked (§20.10
anti-pattern "don't optimise by hiding unknowns").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from plot_agent.context import AnalysisContext
from plot_agent.rules_access import max_coverage_ratio

# §14.1 scores produced by the Phase 12 site-context modules
# (plot_planning.site_context.compute_site_scores). When a computed SiteScore is
# supplied for a hook it becomes a REAL Score; otherwise the hook stays an
# explicit unknown — only when the source data is genuinely absent (§20.10).
PHASE10_SCORE_HOOKS: tuple[str, ...] = (
    "infrastructure_score",  # site_context.roads (BDOT10k/GESUT)
    "terrain_score",  # site_context.terrain (NMT/DEM)
    "environmental_risk_score",  # site_context.environment (GDOŚ + EIA ruleset)
    "geotechnical_risk_score",  # site_context.geology (SOPO)
    "heritage_risk_score",  # site_context.environment (NID)
    "procedural_risk_score",  # site_context.scoring (procedures + unknowns)
    "cost_driver_score",  # site_context.scoring (earthworks/networks/flood)
    "planning_certainty_score",  # Phase 8 stability via site_context.scoring
    "investment_fit_score",  # site_context.scoring (investor goal vs envelope)
)


class Score(BaseModel):
    """A single explainable §14 score (NFR-AUD-006).

    ``value`` is ``None`` when the score is *unknown* (not computable yet); in that case
    ``unknown`` is ``True`` and the reason is carried in ``negative_factors``. A known
    score lists at least one positive or negative factor so it is always explainable.
    """

    name: str = Field(description="Score name (§14.1).")
    value: float | None = Field(default=None, description="0.0-1.0 score, or None if unknown.")
    unknown: bool = Field(default=False, description="True when the score is not computable yet.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence in the score.")
    positive_factors: list[str] = Field(default_factory=list, description="What raises the score.")
    negative_factors: list[str] = Field(default_factory=list, description="What lowers the score.")

    @classmethod
    def unknown_score(cls, name: str, reason: str) -> Score:
        """Build an explicit unknown score (Phase 10 hook; never faked — §20.10)."""
        return cls(name=name, value=None, unknown=True, confidence=0.0, negative_factors=[reason])


class Scores(BaseModel):
    """The score set produced by :class:`ScoreEvaluator` for one context/proposal."""

    scores: dict[str, Score] = Field(default_factory=dict, description="name -> Score.")

    def value(self, name: str) -> float | None:
        s = self.scores.get(name)
        return None if s is None else s.value

    def known_values(self) -> dict[str, float]:
        """Only the computed (non-unknown) numeric scores — used for verdict deltas."""
        return {
            n: s.value
            for n, s in self.scores.items()
            if s.value is not None and not s.unknown
        }

    def unknown_names(self) -> list[str]:
        return sorted(n for n, s in self.scores.items() if s.unknown)

    def to_dict(self) -> dict[str, Any]:
        return {n: s.model_dump() for n, s in self.scores.items()}


@dataclass
class ScoreEvaluator:
    """Compute the §14 scores currently derivable from geometry + rulesets (§4.1.A.2)."""

    # Optional proposed footprint area (m²) when scoring a concrete layout proposal.
    proposed_footprint_area_m2: float | None = None
    proposed_footprint_inside_envelope: bool | None = None
    # Extra unknowns to record verbatim (e.g. from a stubbed analysis result).
    extra_unknowns: list[str] = field(default_factory=list)
    # Phase 12: computed site-context scores (plot_planning.site_context.SiteScore
    # by name). Supplied by the full-due-diligence pipeline; each hook with a
    # computed (non-unknown) SiteScore becomes a real Score, the rest stay honest
    # unknowns with the module's reason (§20.10 — unknown ONLY when the source
    # data is genuinely absent).
    site_scores: dict[str, Any] | None = None

    def evaluate(self, context: AnalysisContext) -> Scores:
        """Return the explainable score set for ``context`` (§14.2 / NFR-AUD-006)."""
        scores: dict[str, Score] = {}
        scores["buildability_score"] = self._buildability(context)
        scores["data_confidence_score"] = self._data_confidence(context)
        scores["coverage_vs_ruleset"] = self._coverage_vs_ruleset(context)

        # Phase 12: §14 site scores — real when the site-context module computed
        # them, explicit unknowns (with the module's reason) otherwise (§20.10).
        for name in PHASE10_SCORE_HOOKS:
            scores[name] = self._site_score(name)
        for u in self.extra_unknowns:
            # Stash arbitrary upstream unknowns under a stable key prefix.
            scores[f"unknown::{u}"] = Score.unknown_score(f"unknown::{u}", reason=u)

        return Scores(scores=scores)

    # ------------------------------------------------------------------ #
    # Individual scores
    # ------------------------------------------------------------------ #
    def _site_score(self, name: str) -> Score:
        """Map a Phase 12 SiteScore onto the §14 Score model (hook consumer).

        Accepts any object with the ``SiteScore`` attribute surface (duck-typed:
        plot_agent must not depend on plot_planning internals beyond the public
        dataclass). No site data / module didn't run → explicit unknown.
        """
        site = (self.site_scores or {}).get(name)
        if site is None:
            return Score.unknown_score(
                name, reason="site_context_not_run_for_this_analysis_mode"
            )
        unknown = bool(getattr(site, "unknown", False))
        value = getattr(site, "value", None)
        if unknown or value is None:
            reason = getattr(site, "unknown_reason", None) or "source_data_absent"
            return Score.unknown_score(name, reason=str(reason))
        return Score(
            name=name,
            value=round(float(value), 4),
            unknown=False,
            confidence=float(getattr(site, "confidence", 0.0)),
            positive_factors=list(getattr(site, "positive_factors", [])),
            negative_factors=list(getattr(site, "negative_factors", [])),
        )

    def _buildability(self, ctx: AnalysisContext) -> Score:
        """buildable-envelope area / parcel area, adjusted by proposed-footprint fit (§8.4)."""
        parcel_area = ctx.parcel_area_m2()
        env_area = ctx.envelope_area_m2()
        pos: list[str] = []
        neg: list[str] = []
        if parcel_area <= 0:
            return Score.unknown_score("buildability_score", reason="parcel_area_is_zero")
        ratio = max(0.0, min(1.0, env_area / parcel_area))
        pos.append(f"buildable envelope is {ratio * 100:.1f}% of parcel area")
        if ratio < 0.2:
            neg.append("very little buildable area remains after constraints")

        value = ratio
        # If a footprint was proposed, reward fit inside the envelope; penalise overspill.
        if self.proposed_footprint_inside_envelope is True:
            pos.append("proposed footprint fits inside the buildable envelope")
        elif self.proposed_footprint_inside_envelope is False:
            neg.append("proposed footprint spills outside the buildable envelope")
            value = ratio * 0.5  # geometry-only penalty (hard guard handled in drawing loop)

        return Score(
            name="buildability_score",
            value=round(value, 4),
            unknown=False,
            confidence=0.7,
            positive_factors=pos,
            negative_factors=neg,
        )

    def _data_confidence(self, ctx: AnalysisContext) -> Score:
        """§14 data_confidence_score proxy from how much input is actually present."""
        pos: list[str] = []
        neg: list[str] = []
        value = 0.0
        if ctx.envelope_area_m2() > 0:
            value += 0.4
            pos.append("buildable envelope geometry is present")
        else:
            neg.append("no buildable envelope geometry")
        if len(ctx.ruleset.rules) > 0:
            value += 0.4
            pos.append(f"{len(ctx.ruleset.rules)} ruleset rule(s) loaded ({ctx.ruleset.ruleset_version})")
        else:
            neg.append("no rulesets loaded")
        if ctx.hard_constraints or ctx.soft_constraints:
            value += 0.2
            pos.append("constraint geometry supplied")
        else:
            neg.append("no constraint geometry supplied (coverage of risks unknown)")
        return Score(
            name="data_confidence_score",
            value=round(min(1.0, value), 4),
            unknown=False,
            confidence=0.6,
            positive_factors=pos,
            negative_factors=neg,
        )

    def _coverage_vs_ruleset(self, ctx: AnalysisContext) -> Score:
        """Footprint coverage vs ruleset max (rulesets/PL/planning/mn-coverage.yaml)."""
        ratio_max, rule_id, found = max_coverage_ratio(ctx.ruleset)
        if not found:
            return Score.unknown_score(
                "coverage_vs_ruleset",
                reason="default_max_coverage_ratio_missing_from_ruleset",
            )
        parcel_area = ctx.parcel_area_m2()
        if parcel_area <= 0:
            return Score.unknown_score("coverage_vs_ruleset", reason="parcel_area_is_zero")

        # Use the proposed footprint if scoring a layout; otherwise score the envelope's
        # implied max coverage (envelope area capped by the ruleset ratio).
        footprint = self.proposed_footprint_area_m2
        pos: list[str] = [f"ruleset max coverage {ratio_max:.0%} (rule {rule_id})"]
        neg: list[str] = []
        if footprint is None:
            # No concrete footprint: score the realistically-buildable footprint as a
            # fraction of the parcel — the lesser of the envelope and the legal cap.
            # A STRICTER (lower) ruleset cap permits less building, so this score DROPS:
            # that directionality is what lets the self-improve loop flag a worsened
            # ruleset as a regression (§4.3).
            allowed = parcel_area * ratio_max
            env_area = ctx.envelope_area_m2()
            usable = min(env_area, allowed)
            value = max(0.0, min(1.0, usable / parcel_area)) if parcel_area > 0 else 0.0
            pos.append(f"buildable footprint up to {usable:,.0f} m² ({value:.0%} of parcel)")
            return Score(
                name="coverage_vs_ruleset",
                value=round(value, 4),
                unknown=False,
                confidence=0.6,
                positive_factors=pos,
                negative_factors=neg,
            )

        coverage = footprint / parcel_area
        if coverage > ratio_max:
            # Over the legal cap — this is a NEGATIVE factor; score drops sharply.
            neg.append(
                f"proposed coverage {coverage:.0%} EXCEEDS ruleset max {ratio_max:.0%}"
            )
            # Inverse-overshoot score in (0, 0.5): closer to the cap scores higher.
            value = max(0.0, 0.5 * (ratio_max / coverage))
        else:
            pos.append(f"proposed coverage {coverage:.0%} within ruleset max {ratio_max:.0%}")
            # Reward using the allowance without exceeding it (utilisation, §8.5).
            value = 0.5 + 0.5 * (coverage / ratio_max)
        return Score(
            name="coverage_vs_ruleset",
            value=round(min(1.0, value), 4),
            unknown=False,
            confidence=0.65,
            positive_factors=pos,
            negative_factors=neg,
        )
