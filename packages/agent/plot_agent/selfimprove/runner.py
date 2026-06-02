"""Golden-scenario runner (Phase 4 §4.1.A.3).

``run_scenario`` ties together the reloadable rules, the score evaluator, and the Phase 3
renderer: it builds an :class:`AnalysisContext`, computes the §14 scores, renders the
scenario map (parcel + envelope + constraints) via ``plot_reports.render_map``, and
diffs the scores/decision against the scenario's golden ``expected``.

The structured result re-uses the stubbed MCP use-cases where available so the harness
exercises the same path Claude Code drives (the use-cases still return schema-valid
PARTIAL stubs until Phase 7; their ``unknowns`` are carried through as score unknowns).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plot_reports.render import Layer, LayerRole, render_map

from plot_agent.context import AnalysisContext
from plot_agent.selfimprove.evaluator import ScoreEvaluator, Scores
from plot_agent.selfimprove.scenarios import GoldenScenario

# Default analytical ruleset dir (Phase 2 load_rulesets; loaded fresh per run for reload).
DEFAULT_RULESET_DIR = "rulesets/PL"


@dataclass
class ScenarioResult:
    """Output of running one golden scenario (§4.1.A.3)."""

    scenario_id: str
    structured_result: dict[str, Any]
    scores: Scores
    render_bytes: bytes
    render_mime: str
    diff_vs_expected: dict[str, Any] = field(default_factory=dict)

    def passed(self) -> bool:
        """True when no expectation was violated (empty diff failures)."""
        return not self.diff_vs_expected.get("failures")


def _render_scenario(scenario: GoldenScenario) -> tuple[bytes, str]:
    """Render parcel + envelope + constraints to PNG via the Phase 3 renderer.

    Reuses ``plot_reports.render_map`` (Phase 3 §3.1) — the same deterministic Agg-backed
    path the MCP image-content channel uses, so the model can SEE the scenario.
    """
    layers = [
        Layer(name="Parcel", geometries=[scenario.parcel], role=LayerRole.PARCEL),
    ]
    for i, hc in enumerate(scenario.hard_constraints):
        layers.append(Layer(name=f"No-build {i + 1}", geometries=[hc], role=LayerRole.NO_BUILD))
    for i, sc in enumerate(scenario.soft_constraints):
        layers.append(
            Layer(name=f"Soft constraint {i + 1}", geometries=[sc], role=LayerRole.CONSTRAINT_SOFT)
        )
    layers.append(
        Layer(
            name="Buildable envelope",
            geometries=[scenario.buildable_envelope],
            role=LayerRole.BUILDABLE_ENVELOPE,
        )
    )
    result = render_map(layers, title=f"Scenario {scenario.id}")
    return result.data, result.mime_type


def _structured_result(scenario: GoldenScenario, scores: Scores) -> dict[str, Any]:
    """Assemble a structured scenario result (scores + carried unknowns).

    We surface the unknown §14 scores as the result's ``unknowns`` so the canonical
    ``{result, ... unknowns}`` contract (§9.4) holds and unknowns persist (§20.10).
    """
    return {
        "scenario_id": scenario.id,
        "analysis_mode": scenario.analysis_mode,
        "investment_goal": scenario.investment_goal,
        "scores": scores.to_dict(),
        "unknowns": scores.unknown_names(),
    }


def _diff_vs_expected(scenario: GoldenScenario, scores: Scores) -> dict[str, Any]:
    """Machine-readable diff of computed scores vs the golden ``expected`` bounds."""
    failures: list[str] = []
    checked: dict[str, Any] = {}
    expected_scores = scenario.expected.get("scores", {})
    for name, bounds in expected_scores.items():
        actual = scores.value(name)
        checked[name] = actual
        if actual is None:
            failures.append(f"{name}: expected a value, got unknown/None")
            continue
        lo = bounds.get("min")
        hi = bounds.get("max")
        if lo is not None and actual < lo:
            failures.append(f"{name}={actual:.4f} below expected min {lo}")
        if hi is not None and actual > hi:
            failures.append(f"{name}={actual:.4f} above expected max {hi}")
    return {"checked": checked, "failures": failures}


def run_scenario(
    scenario: GoldenScenario, *, ruleset_dir: str = DEFAULT_RULESET_DIR
) -> ScenarioResult:
    """Run a golden scenario end-to-end → :class:`ScenarioResult` (§4.1.A.3).

    Rules are loaded FRESH (Phase 2 hot-reload) so an edit to a ruleset value is
    reflected on the next run — this is what makes the before/after verdict meaningful.
    """
    context = AnalysisContext.with_loaded_rules(
        parcel=scenario.parcel,
        buildable_envelope=scenario.buildable_envelope,
        ruleset_dir=ruleset_dir,
        hard_constraints=scenario.hard_constraints,
        soft_constraints=scenario.soft_constraints,
        analysis_mode=scenario.analysis_mode,
        investment_goal=scenario.investment_goal,
    )
    scores = ScoreEvaluator().evaluate(context)
    render_bytes, render_mime = _render_scenario(scenario)
    structured = _structured_result(scenario, scores)
    diff = _diff_vs_expected(scenario, scores)
    return ScenarioResult(
        scenario_id=scenario.id,
        structured_result=structured,
        scores=scores,
        render_bytes=render_bytes,
        render_mime=render_mime,
        diff_vs_expected=diff,
    )
