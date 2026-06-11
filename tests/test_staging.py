"""Phase 11 part A — etapowanie consistency tests (plan §11.1.5).

Covers: stage-2-only parking hall serving a stage-1 building → warning; cumulative
mieszkania crossing the WT §40 trigger at stage 2 with the playground arriving only
in stage 3 → warning citing the wt-40 rule id (resolved via the rules ENGINE, no
literal 20); compliant staging → all pass; missing parking indicator → unknown (not
pass); road-access checks; soft-only guarantee; and the propose_layout wiring
(``staging_checks`` in the masterplan path output).
"""

from __future__ import annotations

import pytest
from plot_agent.drawing import MasterplanProposal
from plot_planning import check_staging, masterplan_metrics
from plot_planning.capacity import WT40_RULE_ID
from plot_rules import RuleStatus, load_rulesets
from tests.masterplan_fixtures import (
    staged_masterplan_payload,
    staging_parcel,
)

INDICATORS = {"parking_per_mieszkanie": 1.0}


@pytest.fixture(scope="module")
def registry():
    return load_rulesets("rulesets/PL")


def _checks(payload, registry, indicators=INDICATORS):
    proposal = MasterplanProposal.model_validate(payload)
    metrics = masterplan_metrics(
        proposal, staging_parcel(), indicators or {}, registry=registry
    )
    return check_staging(proposal, metrics, registry, indicators=indicators)


def _by(checks, name):
    return [c for c in checks if c.check == name]


def test_compliant_staging_all_pass(registry) -> None:
    checks = _checks(staged_masterplan_payload(), registry)
    assert checks, "a staged proposal must produce staging checks"
    bad = [c for c in checks if c.status is not RuleStatus.PASS]
    assert not bad, f"compliant staging must be all-pass, got: {[c.message for c in bad]}"


def test_stage2_parking_hall_serving_stage1_building_warns(registry) -> None:
    checks = _checks(staged_masterplan_payload(parking_stage=2), registry)
    dep = _by(checks, "stage_dependency")
    assert dep and dep[0].status is RuleStatus.WARNING
    assert "B1" in dep[0].message and "etap 2" in dep[0].message
    assert dep[0].severity == "soft"
    # Stage 1 also shows the supply gap (the hall does not exist yet in stage 1).
    stage1_parking = [c for c in _by(checks, "parking_balance") if c.stage == 1]
    assert stage1_parking[0].status is RuleStatus.WARNING


def test_playground_arriving_after_trigger_warns_with_wt40_rule_id(registry) -> None:
    # Cumulative mieszkania cross the §40 trigger long before stage 3, but the
    # playground is staged 3 → stages 1-2 must warn, citing the wt-40 rule id; the
    # trigger itself comes from the rules engine (no literal 20 in staging.py).
    checks = _checks(staged_masterplan_payload(playground_stage=3), registry)
    play = _by(checks, "plac_zabaw")
    early = [c for c in play if c.stage < 3]
    assert early and all(c.status is RuleStatus.WARNING for c in early)
    assert all(c.rule_id == WT40_RULE_ID for c in early)
    assert all(WT40_RULE_ID in c.message for c in early)
    late = [c for c in play if c.stage == 3]
    assert late and late[0].status is RuleStatus.PASS


def test_missing_parking_indicator_is_unknown_not_pass(registry) -> None:
    checks = _checks(staged_masterplan_payload(), registry, indicators={})
    parking = _by(checks, "parking_balance")
    assert parking and all(c.status is RuleStatus.UNKNOWN for c in parking)
    assert all("parking_per_mieszkanie" in c.message for c in parking)


def test_no_road_access_warns_per_stage(registry) -> None:
    checks = _checks(staged_masterplan_payload(with_road=False), registry)
    roads = _by(checks, "road_access")
    assert roads and all(c.status is RuleStatus.WARNING for c in roads)


def test_unstaged_proposal_yields_no_staging_checks(registry) -> None:
    payload = staged_masterplan_payload()
    for b in payload["buildings"]:
        b.pop("stage")
    assert _checks(payload, registry) == []


def test_all_staging_checks_are_soft(registry) -> None:
    # Anti-pattern guard (plan §11.1.5): staging outcomes are NEVER hard violations.
    for payload in (
        staged_masterplan_payload(parking_stage=2, playground_stage=3, with_road=False),
        staged_masterplan_payload(),
    ):
        for c in _checks(payload, registry):
            assert c.severity == "soft"


def test_propose_layout_masterplan_path_carries_staging_checks() -> None:
    # usecases wiring: the masterplan path includes staging_checks in its output
    # (soft warnings — accepted/valid are NOT driven by them).
    from plot_mcp_server.usecases import propose_layout_render

    # The drawing context uses the Phase 3 sample parcel — keep the staged plan
    # footprint inside its frame by reusing the payload as-is; staging checks are
    # geometry-independent except road access (still computed).
    out = propose_layout_render(
        staged_masterplan_payload(parking_stage=2), indicators=INDICATORS
    )
    assert "staging_checks" in out
    staging = out["staging_checks"]
    assert staging and all(s["severity"] == "soft" for s in staging)
    dep = [s for s in staging if s["check"] == "stage_dependency"]
    assert dep and dep[0]["status"] == "warning"
    # Hard validity is independent of staging warnings (soft-only guarantee).
    assert all(s["check"] != "stage_dependency" or s["status"] == "warning"
               for s in staging)
