"""Phase 4 self-improve dev-loop tests (IMPLEMENTATION_PLAN.md §4.3 verification).

Covers the required evidence:
  * the harness runs golden scenarios and produces before/after scores + screenshots;
  * unknown §14 scores persist (Phase 10 hooks; never faked — §20.10);
  * worsening a ruleset value (lower default_max_coverage_ratio) makes the DevLoop report
    a REGRESSION (measurable negative score delta) for the affected scenario; the edit is
    done on a COPY so the committed ruleset value is restored/untouched (§12, NFR-AUD-003).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from plot_agent.context import AnalysisContext
from plot_agent.selfimprove import (
    DevLoop,
    ScoreEvaluator,
    run_scenario,
    sample_scenarios,
)
from plot_agent.selfimprove.scenarios import _sample_geometry

REPO_ROOT = Path(__file__).resolve().parents[1]
RULESETS = REPO_ROOT / "rulesets" / "PL"


def test_run_scenario_produces_scores_render_and_diff() -> None:
    scenario = sample_scenarios()[0]
    result = run_scenario(scenario, ruleset_dir=str(RULESETS))
    assert result.scenario_id == "sample-single-family"
    # Real (computable-now) scores are present and numeric.
    assert result.scores.value("buildability_score") is not None
    assert result.scores.value("coverage_vs_ruleset") is not None
    assert result.scores.value("data_confidence_score") is not None
    # Render produced PNG bytes (Phase 3 path).
    assert result.render_mime == "image/png"
    assert result.render_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    # Golden diff passes for the sample scenario.
    assert result.passed(), result.diff_vs_expected


def test_unknown_scores_persist_and_are_not_faked() -> None:
    g = _sample_geometry()
    ctx = AnalysisContext.with_loaded_rules(
        parcel=g["parcel"], buildable_envelope=g["envelope"], ruleset_dir=str(RULESETS)
    )
    scores = ScoreEvaluator().evaluate(ctx)
    unknown = scores.unknown_names()
    # The Phase 10 §14 scores we cannot compute yet are present as explicit unknowns.
    for name in ("terrain_score", "geotechnical_risk_score", "heritage_risk_score"):
        assert name in unknown, f"{name} must persist as an unknown (§20.10)"
        s = scores.scores[name]
        assert s.value is None and s.unknown is True
    # Known scores never sneak into the unknown set.
    assert "buildability_score" not in unknown


def test_devloop_reports_regression_on_worsened_ruleset(tmp_path: Path) -> None:
    # Work on a COPY of the rulesets so the committed legal value is never mutated.
    work = tmp_path / "PL"
    shutil.copytree(RULESETS, work)

    loop = DevLoop(scenarios=sample_scenarios(), ruleset_dir=str(work))
    loop.snapshot_before()

    # Worsen the coverage cap (the value the plan calls out): 0.30 -> 0.05.
    cov = work / "planning" / "mn-coverage.yaml"
    text = cov.read_text()
    assert "default_max_coverage_ratio: 0.30" in text
    cov.write_text(text.replace("default_max_coverage_ratio: 0.30", "default_max_coverage_ratio: 0.05"))

    loop.reload()  # same reload path dev_reload uses (fresh load_rulesets + importlib)
    loop.snapshot_after()
    verdict = loop.verdict(persist_screenshots=True)

    # The affected scenario regressed with a measurable negative coverage delta.
    assert "sample-single-family" in verdict.regressions(), verdict.per_scenario
    delta = verdict.score_deltas["sample-single-family"]["coverage_vs_ruleset"]
    assert delta < 0, f"expected a coverage regression, got delta={delta}"
    # Screenshots were captured for the scenarios.
    assert "sample-single-family" in verdict.screenshots
    # The committed ruleset on disk is untouched (we edited a copy).
    assert "default_max_coverage_ratio: 0.30" in (RULESETS / "planning" / "mn-coverage.yaml").read_text()


def test_devloop_no_edit_is_unchanged(tmp_path: Path) -> None:
    work = tmp_path / "PL"
    shutil.copytree(RULESETS, work)
    loop = DevLoop(scenarios=sample_scenarios(), ruleset_dir=str(work))
    loop.snapshot_before()
    loop.reload()
    loop.snapshot_after()
    verdict = loop.verdict(persist_screenshots=False)
    # With no external edit, every scenario is unchanged (no fake improvement).
    assert set(verdict.per_scenario.values()) == {"unchanged"}
