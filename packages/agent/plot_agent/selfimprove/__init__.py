"""Self-improvement dev-loop harness (IMPLEMENTATION_PLAN.md Phase 4 §4.1.A).

The loop Claude Code uses to build later phases: run named golden scenarios end-to-end,
score them (the §14 scores computable NOW from geometry + rulesets), snapshot
before/after an external edit, and emit an objective improved/regressed verdict.

* :mod:`plot_agent.selfimprove.scenarios` — :class:`GoldenScenario` + sample scenarios.
* :mod:`plot_agent.selfimprove.evaluator` — :class:`ScoreEvaluator` (explainable scores).
* :mod:`plot_agent.selfimprove.runner` — :func:`run_scenario` (result + scores + render).
* :mod:`plot_agent.selfimprove.loop` — :class:`DevLoop` (snapshot/reload/verdict).
"""

from plot_agent.selfimprove.evaluator import (
    Score,
    ScoreEvaluator,
    Scores,
)
from plot_agent.selfimprove.loop import DevLoop, Verdict
from plot_agent.selfimprove.runner import ScenarioResult, run_scenario
from plot_agent.selfimprove.scenarios import GoldenScenario, sample_scenarios

__all__ = [
    "Score",
    "Scores",
    "ScoreEvaluator",
    "GoldenScenario",
    "sample_scenarios",
    "ScenarioResult",
    "run_scenario",
    "DevLoop",
    "Verdict",
]
