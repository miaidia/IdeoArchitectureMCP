"""Phase 12 §14.1 site scores + evaluator hook wiring (v1 Phase 10 §10.1.7).

* every COMPUTED score is explainable: positive/negative factors + confidence
  (NFR-AUD-006);
* a score whose source data is absent stays an explicit UNKNOWN with the
  module's reason (§20.10 — never faked);
* the ``PHASE10_SCORE_HOOKS`` in ``ScoreEvaluator`` consume the SiteScores
  (plan delta 2) — real values replace the former placeholders;
* §14.2 hard-blocker dominance: flood zone A over the whole parcel forces
  ``LIKELY_BLOCKED`` regardless of the other scores.
"""

from __future__ import annotations

import pytest
from plot_agent.context import AnalysisContext
from plot_agent.selfimprove.evaluator import PHASE10_SCORE_HOOKS, ScoreEvaluator
from plot_planning.site_context import (
    SiteContext,
    analyze_access,
    analyze_environment,
    analyze_geology,
    analyze_terrain,
    analyze_water,
    compute_site_scores,
)
from plot_rules import load_rulesets
from shapely.geometry import LineString, box
from tests.site_fixtures import building_feature, synthetic_dem_bytes
from tests.wt_fixtures import X0, Y0, parcel_square

REGISTRY = load_rulesets("rulesets/PL")


def _full_site(*, flood_whole_parcel: bool = False) -> SiteContext:
    """A SiteContext with EVERY module run on synthetic fixtures."""
    parcel = parcel_square()
    site = SiteContext(analysis_id="an-score")
    raster = synthetic_dem_bytes(
        west=X0 - 50, north=Y0 + 250, width=300, height=300, east_slope_pct=0.0
    )
    site.terrain = analyze_terrain(raster, parcel)
    flood = [parcel_square()] if flood_whole_parcel else []
    site.water = analyze_water(
        parcel,
        flood_geoms=flood,
        watercourse_geoms=[LineString([(X0 - 10, Y0 - 10), (X0 + 210, Y0 - 10)])],
        mean_slope_pct=site.terrain.slope.get("mean_pct"),
    )
    site.geology = analyze_geology(parcel, landslide_geoms=[])
    site.environment = analyze_environment(
        parcel,
        REGISTRY,
        protected_geoms=[],
        heritage_geoms=[],
        investment_type="multifamily",
        investment_area_m2=8_000.0,
    )
    site.access = analyze_access(
        parcel,
        road_geoms=[box(X0 - 20, Y0 - 22, X0 + 220, Y0 - 2)],
        utility_features=[
            building_feature(
                {
                    "type": "LineString",
                    "coordinates": [[X0 - 10, Y0 - 5], [X0 + 210, Y0 - 5]],
                },
                rodzajSieci="wodociagowa",
            )
        ],
    )
    return site


def test_every_computed_score_is_explainable_with_confidence() -> None:
    scores = compute_site_scores(
        _full_site(),
        investment_goal={"type": "multifamily", "target_gfa_m2": 10_000.0},
        envelope_area_m2=20_000.0,
        parcel_area_m2=40_000.0,
    )
    computed = {n: s for n, s in scores.items() if not s.unknown}
    # With every module run, only planning certainty stays unknown (no parsed acts).
    assert set(computed) >= {
        "terrain_score",
        "environmental_risk_score",
        "geotechnical_risk_score",
        "heritage_risk_score",
        "infrastructure_score",
        "procedural_risk_score",
        "cost_driver_score",
        "investment_fit_score",
    }
    for name, s in computed.items():
        assert s.value is not None and 0.0 <= s.value <= 1.0, name
        assert s.confidence > 0.0, name
        assert s.positive_factors or s.negative_factors, f"{name} must be explainable"
    # No parsed planning acts → certainty honestly unknown (not low, not 0).
    assert scores["planning_certainty_score"].unknown is True
    assert scores["planning_certainty_score"].unknown_reason == "no_parsed_planning_acts_for_parcel"


def test_scores_unknown_when_source_data_absent() -> None:
    site = SiteContext()  # nothing ran
    scores = compute_site_scores(site)
    for name in (
        "terrain_score",
        "environmental_risk_score",
        "geotechnical_risk_score",
        "heritage_risk_score",
        "infrastructure_score",
        "procedural_risk_score",
        "cost_driver_score",
        "investment_fit_score",
        "planning_certainty_score",
    ):
        assert scores[name].unknown is True, name
        assert scores[name].unknown_reason, name


def test_infrastructure_score_counts_on_parcel_networks_as_near() -> None:
    """Review M2: a network at 0.0 m (crossing the parcel) is the BEST case —
    the falsy-zero ``or 1e9`` must never push it past the 100 m near band."""
    from plot_domain import RoadAccess, UtilityNetwork
    from plot_planning.site_context import AccessAnalysis

    def _site(distance_m: float) -> SiteContext:
        site = SiteContext()
        access = AccessAnalysis(status="ok")
        access.road_access = RoadAccess(
            id="ra-fixture", has_public_road_access=True, frontage_length_m=20.0
        )
        access.utilities = [
            UtilityNetwork(id="u-fixture", network_type="water", distance_m=distance_m)
        ]
        site.access = access
        return site

    on_parcel = compute_site_scores(_site(0.0))["infrastructure_score"]
    near_90 = compute_site_scores(_site(90.0))["infrastructure_score"]
    assert on_parcel.unknown is False and on_parcel.value is not None
    assert near_90.value is not None
    assert any("w zasięgu" in f for f in on_parcel.positive_factors)
    assert on_parcel.value >= near_90.value


def test_planning_certainty_from_phase8_stability_trace() -> None:
    block = {
        "coverage_status": "parsed",
        "acts": [
            {"id": "mpzp-1", "name": "MPZP X", "stability": {"score": 0.8}},
            {"id": "mpzp-2", "name": "MPZP Y", "stability": {"score": 0.6}},
        ],
    }
    score = compute_site_scores(SiteContext(), planning_block=block)["planning_certainty_score"]
    assert score.unknown is False
    assert score.value == pytest.approx(0.7)
    assert any("MPZP X" in f for f in score.positive_factors)


def test_investment_fit_requires_explicit_goal() -> None:
    no_goal = compute_site_scores(SiteContext(), envelope_area_m2=1_000.0)
    assert no_goal["investment_fit_score"].unknown_reason == "no_explicit_investment_goal"
    overshoot = compute_site_scores(
        _full_site(),
        investment_goal={"type": "multifamily", "target_gfa_m2": 1_000_000.0},
        envelope_area_m2=10_000.0,
        parcel_area_m2=40_000.0,
    )["investment_fit_score"]
    assert overshoot.unknown is False and overshoot.value < 0.5
    assert any("przekracza" in f for f in overshoot.negative_factors)


# --------------------------------------------------------------------------- #
# Evaluator hook wiring (delta 2)
# --------------------------------------------------------------------------- #
def test_evaluator_hooks_consume_site_scores() -> None:
    parcel = parcel_square()
    ctx = AnalysisContext(parcel=parcel, buildable_envelope=parcel, ruleset=REGISTRY)
    site_scores = compute_site_scores(_full_site())

    wired = ScoreEvaluator(site_scores=site_scores).evaluate(ctx)
    terrain = wired.scores["terrain_score"]
    assert terrain.unknown is False and terrain.value is not None
    assert terrain.positive_factors or terrain.negative_factors
    # Hooks WITHOUT computed data carry the module's reason verbatim.
    certainty = wired.scores["planning_certainty_score"]
    assert certainty.unknown is True
    assert "no_parsed_planning_acts_for_parcel" in certainty.negative_factors

    # Without site scores every hook stays an explicit unknown (quick mode).
    bare = ScoreEvaluator().evaluate(ctx)
    for name in PHASE10_SCORE_HOOKS:
        assert bare.scores[name].unknown is True, name


# --------------------------------------------------------------------------- #
# §14.2 hard-blocker dominance survives the new scores
# --------------------------------------------------------------------------- #
def test_flood_whole_parcel_forces_likely_blocked_regardless_of_scores() -> None:
    from plot_domain import BuildableEnvelope
    from plot_envelope import decision
    from shapely.geometry import mapping

    parcel = parcel_square()
    site = _full_site(flood_whole_parcel=True)
    # Scores other than environmental stay favourable — the decision must NOT
    # average past the hard blocker (§14.2: never sum past a hard blocker).
    scores = compute_site_scores(site, parcel_area_m2=float(parcel.area))
    assert scores["terrain_score"].value and scores["terrain_score"].value > 0.8
    envelope = BuildableEnvelope(
        id="env-1",
        area_m2=float(parcel.area) * 0.8,  # big envelope — still blocked
        geometry=mapping(parcel),
        confidence=0.8,
    )
    verdict = decision(site.all_risks(), envelope, parcel_area_m2=float(parcel.area))
    assert verdict.value == "LIKELY_BLOCKED"
