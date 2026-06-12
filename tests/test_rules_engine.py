"""Rule evaluation engine tests (Phase 8, base_assumptions §12, F-0128-0138).

Covers every status (pass/fail/warning/unknown/not_applicable), the
missing-input -> unknown guarantee (never a silent pass), the three evaluation
modes, bracket-table selects, threshold modifiers, the audited expert-override
hook, JSON Schema validation of malformed rule documents, and determinism.

Synthetic rules are built in-memory (no legal values in this file — the WT
corpus goldens live in test_rulesets_wt.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from plot_domain.models import Override
from plot_rules import (
    EvaluationMode,
    Rule,
    RuleCheck,
    RuleStatus,
    evaluate,
    load_rulesets,
    validate_rule_document,
)


def make_rule(severity: str = "hard", **raw: Any) -> Rule:
    """Build a synthetic in-memory rule for engine-semantics tests."""
    doc: dict[str, Any] = {
        "id": "TEST-RULE-001",
        "title": "Synthetic test rule",
        "jurisdiction": "PL",
        "valid_from": "2026-01-01",
        "source_reference": "Dz.U. TEST poz. 1 par. 1",
        "inputs": [],
        "outputs": [],
        "severity": severity,
        **raw,
    }
    return Rule(
        id=doc["id"],
        title=doc["title"],
        jurisdiction="PL",
        category="building-technical",
        valid_from="2026-01-01",
        valid_to=None,
        source_reference=doc["source_reference"],
        inputs=[str(x) for x in doc.get("inputs", [])],
        outputs=[],
        logic="declarative_checks_v1",
        path="<memory>",
        severity=severity,
        raw=doc,
    )


SIMPLE = dict(
    inputs=["distance_m"],
    thresholds={"min_distance_m": 4.0},
    checks=[
        {
            "name": "min-distance",
            "input": "distance_m",
            "op": "gte",
            "threshold": "min_distance_m",
            "source_reference": "par. 1 ust. 1",
        }
    ],
)


# --------------------------------------------------------------------------- #
# Statuses: pass / fail / warning / unknown / not_applicable
# --------------------------------------------------------------------------- #
def test_pass() -> None:
    check = evaluate(make_rule(**SIMPLE), {"distance_m": 5.0})
    assert check.status is RuleStatus.PASS
    assert check.severity == "hard"


def test_fail_hard() -> None:
    check = evaluate(make_rule(**SIMPLE), {"distance_m": 2.0})
    assert check.status is RuleStatus.FAIL


def test_soft_rule_failure_becomes_warning() -> None:
    check = evaluate(make_rule(severity="soft", **SIMPLE), {"distance_m": 2.0})
    assert check.status is RuleStatus.WARNING
    assert check.severity == "soft"


def test_not_applicable_via_rule_applies_when() -> None:
    rule = make_rule(applies_when={"is_multifamily": True}, **SIMPLE)
    check = evaluate(rule, {"is_multifamily": False, "distance_m": 2.0})
    assert check.status is RuleStatus.NOT_APPLICABLE


def test_unknown_when_input_missing_optimistic() -> None:
    check = evaluate(make_rule(**SIMPLE), {}, mode=EvaluationMode.OPTIMISTIC)
    assert check.status is RuleStatus.UNKNOWN
    assert check.status is not RuleStatus.PASS  # NEVER a silent pass
    assert "distance_m" in check.trace["missing_inputs"]


def test_rule_without_checks_is_unknown_not_pass() -> None:
    # The two legacy example rules have no machine checks: engine reports unknown.
    rule = make_rule(inputs=["x"])
    check = evaluate(rule, {"x": 1}, mode=EvaluationMode.OPTIMISTIC)
    assert check.status is RuleStatus.UNKNOWN


# --------------------------------------------------------------------------- #
# Evaluation modes (F-0134-0138)
# --------------------------------------------------------------------------- #
def test_mode_strict_unknown_on_hard_rule_fails() -> None:
    check = evaluate(make_rule(**SIMPLE), {}, mode="strict")
    assert check.status is RuleStatus.FAIL


def test_mode_conservative_unknown_on_hard_rule_warns_with_blocker_note() -> None:
    check = evaluate(make_rule(**SIMPLE), {}, mode="conservative")
    assert check.status is RuleStatus.WARNING
    assert "blocker" in check.trace["blocker_note"].lower()


def test_mode_default_is_conservative() -> None:
    assert evaluate(make_rule(**SIMPLE), {}).status is RuleStatus.WARNING


def test_modes_do_not_change_decided_results() -> None:
    for mode in EvaluationMode:
        assert evaluate(make_rule(**SIMPLE), {"distance_m": 9.0}, mode=mode).status is (
            RuleStatus.PASS
        )
        assert evaluate(make_rule(**SIMPLE), {"distance_m": 1.0}, mode=mode).status is (
            RuleStatus.FAIL
        )


def test_unknown_on_soft_rule_stays_unknown_in_all_modes() -> None:
    for mode in EvaluationMode:
        check = evaluate(make_rule(severity="soft", **SIMPLE), {}, mode=mode)
        assert check.status is RuleStatus.UNKNOWN, mode


# --------------------------------------------------------------------------- #
# Bracket-table select + per-unit values + modifiers + other_input
# --------------------------------------------------------------------------- #
BRACKETS = dict(
    inputs=["spaces", "distance_m"],
    select={
        "required_m": [
            {"when": {"spaces": {"max": 10}}, "value": 7.0},
            {"when": {"spaces": {"min": 11, "max": 60}}, "value": 10.0},
            {"when": {"spaces": {"min": 61}}, "value": 20.0},
        ]
    },
    checks=[{"name": "bracket", "input": "distance_m", "op": "gte", "select": "required_m"}],
)


@pytest.mark.parametrize(
    ("spaces", "distance", "expected"),
    [
        (10, 7.0, RuleStatus.PASS),
        (10, 6.9, RuleStatus.FAIL),
        (25, 9.0, RuleStatus.FAIL),
        (25, 10.0, RuleStatus.PASS),
        (61, 19.0, RuleStatus.FAIL),
        (61, 20.0, RuleStatus.PASS),
    ],
)
def test_select_bracket_table(spaces: int, distance: float, expected: RuleStatus) -> None:
    check = evaluate(make_rule(**BRACKETS), {"spaces": spaces, "distance_m": distance})
    assert check.status is expected


def test_select_missing_bracket_param_is_unknown() -> None:
    check = evaluate(make_rule(**BRACKETS), {"distance_m": 50.0}, mode="optimistic")
    assert check.status is RuleStatus.UNKNOWN


def test_missing_inputs_trace_names_the_missing_select_param() -> None:
    """m1 regression: the comparison input IS provided; only the select bracket
    parameter is missing — the trace must name the actually-missing parameter,
    not the provided comparison input."""
    check = evaluate(make_rule(**BRACKETS), {"distance_m": 50.0}, mode="optimistic")
    assert check.status is RuleStatus.UNKNOWN
    assert check.trace["missing_inputs"] == ["spaces"]


def test_applies_when_numeric_brackets_gate_checks() -> None:
    """Generic range conditions in check-level applies_when: min/max inclusive,
    gt/lt exclusive (the B2 mechanism — regime split by a value range)."""
    rule = make_rule(
        inputs=["width_m", "distance_m"],
        thresholds={"narrow_min": 10.0, "wide_min": 20.0},
        checks=[
            {
                "name": "narrow",
                "applies_when": {"width_m": {"max": 3.0}},
                "input": "distance_m",
                "op": "gte",
                "threshold": "narrow_min",
            },
            {
                "name": "wide",
                "applies_when": {"width_m": {"gt": 3.0}},
                "input": "distance_m",
                "op": "gte",
                "threshold": "wide_min",
            },
        ],
    )
    assert evaluate(rule, {"width_m": 2.0, "distance_m": 12.0}).status is RuleStatus.PASS
    assert evaluate(rule, {"width_m": 2.0, "distance_m": 9.0}).status is RuleStatus.FAIL
    # boundary: max is inclusive, gt is exclusive -> exactly one check applies at 3.0
    assert evaluate(rule, {"width_m": 3.0, "distance_m": 12.0}).status is RuleStatus.PASS
    assert evaluate(rule, {"width_m": 4.0, "distance_m": 12.0}).status is RuleStatus.FAIL
    assert evaluate(rule, {"width_m": 4.0, "distance_m": 20.0}).status is RuleStatus.PASS
    # a missing range-condition input is undetermined -> unknown, never a pass
    assert (
        evaluate(rule, {"distance_m": 50.0}, mode="optimistic").status is RuleStatus.UNKNOWN
    )


def test_modifier_when_range_gates_at_least_clamp_off_zero_base() -> None:
    """The B1 mechanism: an `at_least` clamp gated on the obligation-creating
    range never lifts a 0.0 no-obligation base."""
    rule = make_rule(
        inputs=["units", "area_m2", "flag"],
        input_defaults={"flag": False},
        select={
            "required": [
                {"when": {"units": {"max": 20}}, "value": 0.0},
                {"when": {"units": {"min": 21}}, "value_per_unit": 1.0},
            ]
        },
        checks=[
            {
                "name": "area",
                "input": "area_m2",
                "op": "gte",
                "select": "required",
                "modifiers": [
                    {
                        "when": {"flag": True, "units": {"min": 21}},
                        "multiply": 0.5,
                        "at_least": 20.0,
                    }
                ],
            }
        ],
    )
    # No obligation (units <= 20): the gated clamp must not create one.
    assert evaluate(rule, {"units": 15, "area_m2": 0.0, "flag": True}).status is RuleStatus.PASS
    # Obligation exists: 30 * 1.0 * 0.5 = 15 clamped to 20.
    check = evaluate(rule, {"units": 30, "area_m2": 20.0, "flag": True})
    assert check.status is RuleStatus.PASS
    assert check.trace["checks"][0]["target_value"] == 20.0
    assert evaluate(rule, {"units": 30, "area_m2": 19.0, "flag": True}).status is RuleStatus.FAIL


def test_select_value_per_unit() -> None:
    rule = make_rule(
        inputs=["units", "area_m2"],
        select={
            "required_area": [
                {"when": {"units": {"min": 21, "max": 50}}, "value_per_unit": 1.0},
                {"when": {"units": {"min": 51}}, "value": 50.0},
            ]
        },
        checks=[{"name": "area", "input": "area_m2", "op": "gte", "select": "required_area"}],
    )
    assert evaluate(rule, {"units": 30, "area_m2": 30.0}).status is RuleStatus.PASS
    assert evaluate(rule, {"units": 30, "area_m2": 29.0}).status is RuleStatus.FAIL


def test_modifier_multiply_and_at_least() -> None:
    rule = make_rule(
        inputs=["value", "flag"],
        thresholds={"base": 40.0},
        input_defaults={"flag": False},
        checks=[
            {
                "name": "modified",
                "input": "value",
                "op": "gte",
                "threshold": "base",
                "modifiers": [{"when": {"flag": True}, "multiply": 0.5, "at_least": 25.0}],
            }
        ],
    )
    # flag off (default): threshold 40.
    assert evaluate(rule, {"value": 30.0}).status is RuleStatus.FAIL
    # flag on: 40*0.5=20 clamped to at_least 25 -> 30 passes.
    check = evaluate(rule, {"value": 30.0, "flag": True})
    assert check.status is RuleStatus.PASS
    assert check.trace["checks"][0]["target_value"] == 25.0


def test_other_input_comparison() -> None:
    rule = make_rule(
        inputs=["provided", "required"],
        checks=[{"name": "balance", "input": "provided", "op": "gte", "other_input": "required"}],
    )
    assert evaluate(rule, {"provided": 5, "required": 4}).status is RuleStatus.PASS
    assert evaluate(rule, {"provided": 3, "required": 4}).status is RuleStatus.FAIL
    assert (
        evaluate(rule, {"provided": 3}, mode="optimistic").status is RuleStatus.UNKNOWN
    )


def test_input_defaults_are_recorded_as_assumptions_never_silent() -> None:
    rule = make_rule(input_defaults={"flag": False}, **SIMPLE)
    check = evaluate(rule, {"distance_m": 9.0})
    assert check.status is RuleStatus.PASS
    assert check.trace["assumed_inputs"] == {"flag": False}


# --------------------------------------------------------------------------- #
# Trace content (NFR-AUD-005)
# --------------------------------------------------------------------------- #
def test_trace_contains_inputs_threshold_and_source() -> None:
    check = evaluate(make_rule(**SIMPLE), {"distance_m": 5.0})
    assert check.trace["inputs"] == {"distance_m": 5.0}
    entry = check.trace["checks"][0]
    assert entry["threshold"] == "min_distance_m"
    assert entry["target_value"] == 4.0
    assert entry["source_reference"] == "par. 1 ust. 1"
    assert check.trace["source_reference"] == "Dz.U. TEST poz. 1 par. 1"
    assert check.source_reference == "Dz.U. TEST poz. 1 par. 1"


def test_determinism_same_inputs_identical_result() -> None:
    rule = make_rule(**BRACKETS)
    inputs = {"spaces": 25, "distance_m": 9.0}
    a, b = evaluate(rule, dict(inputs)), evaluate(rule, dict(inputs))
    assert a == b


def test_confidence_ranges() -> None:
    decided = evaluate(make_rule(**SIMPLE), {"distance_m": 5.0})
    unknown = evaluate(make_rule(**SIMPLE), {}, mode="optimistic")
    conservative = evaluate(make_rule(**SIMPLE), {}, mode="conservative")
    assert decided.confidence > conservative.confidence > unknown.confidence


def test_confidence_policy_out_of_range_is_clamped_not_raised() -> None:
    """M1 regression: an out-of-range ``confidence_policy`` value reached through
    ``rule.raw`` (the schema rejects it at load time, but raw access paths and
    in-memory rules bypass the loader) is CLAMPED into [0.05, 1.0] like the
    legacy ``_confidence`` — ``evaluate`` never raises (engine guarantee)."""
    rule = make_rule(
        confidence_policy={"decided": 1.2, "unknown": -0.3}, **SIMPLE
    )
    decided = evaluate(rule, {"distance_m": 5.0})  # must not raise
    assert decided.confidence == 1.0  # legacy scalar clamp
    cc = decided.trace["confidence_components"]
    assert cc["components"]["ruleset_certainty"] == 1.0  # 1.2 clamped down

    unknown = evaluate(rule, {}, mode="optimistic")  # must not raise
    assert unknown.confidence == 0.05  # legacy scalar clamp (floor)
    cc_unknown = unknown.trace["confidence_components"]
    assert cc_unknown["components"]["ruleset_certainty"] == 0.05  # -0.3 clamped up


# --------------------------------------------------------------------------- #
# Expert override hook (F-0137, NFR-AUD-003)
# --------------------------------------------------------------------------- #
def test_override_applies_with_audit_note() -> None:
    rule = make_rule(**SIMPLE)
    override = Override(
        id="ovr-1",
        user_id="ekspert@example.pl",
        target_type="rule_check",
        target_id="TEST-RULE-001",
        before_json={"status": "fail"},
        after_json={"status": "pass", "confidence": 0.8},
        reason="Pomiar geodezyjny potwierdza odleglosc 4.05 m",
    )
    check = evaluate(rule, {"distance_m": 2.0}, override=override)
    assert check.status is RuleStatus.PASS
    audit = check.trace["override"]
    assert audit["user_id"] == "ekspert@example.pl"
    assert audit["reason"].startswith("Pomiar geodezyjny")
    assert audit["original_status"] == "fail"
    assert check.confidence == 0.8
    assert "overridden" in check.message


def test_override_marks_confidence_components_superseded() -> None:
    """m4 regression: after an expert override the flat confidence comes from the
    override (e.g. 0.95) while the trace's confidence_components still describe
    the PRE-override evaluation — the components block must carry an explicit
    ``superseded_by_override`` marker so the disagreement is self-explaining."""
    rule = make_rule(**SIMPLE)
    override = Override(
        id="ovr-3",
        user_id="ekspert@example.pl",
        target_type="rule_check",
        target_id="TEST-RULE-001",
        before_json={"status": "fail"},
        after_json={"status": "pass", "confidence": 0.95},
        reason="Pomiar geodezyjny potwierdza zgodnosc",
    )
    overridden = evaluate(rule, {}, mode="optimistic", override=override)
    assert overridden.status is RuleStatus.PASS
    assert overridden.confidence == 0.95
    cc = overridden.trace["confidence_components"]
    assert cc["superseded_by_override"] is True
    # The components still document the pre-override evaluation (audit trail).
    assert cc["value"] < overridden.confidence

    plain = evaluate(rule, {}, mode="optimistic")
    assert "superseded_by_override" not in plain.trace["confidence_components"]


def test_override_for_other_rule_is_ignored() -> None:
    override = Override(
        id="ovr-2",
        user_id="ekspert@example.pl",
        target_type="rule_check",
        target_id="OTHER-RULE",
        after_json={"status": "pass"},
        reason="n/a",
    )
    check = evaluate(make_rule(**SIMPLE), {"distance_m": 2.0}, override=override)
    assert check.status is RuleStatus.FAIL
    assert "override" not in check.trace


# --------------------------------------------------------------------------- #
# JSON Schema validation at load time
# --------------------------------------------------------------------------- #
def _write_ruleset(tmp_path: Path, doc: dict[str, Any]) -> Path:
    target = tmp_path / "building-technical"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "rule.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


VALID_DOC: dict[str, Any] = {
    "id": "TEST-LOAD-001",
    "title": "t",
    "jurisdiction": "PL",
    "valid_from": "2026-01-01",
    "source_reference": "Dz.U. TEST",
    "inputs": ["x"],
    "outputs": ["y"],
    "severity": "hard",
    "checks": [{"name": "c", "input": "x", "op": "gte", "value": 1}],
}


def test_valid_document_loads(tmp_path: Path) -> None:
    _write_ruleset(tmp_path, VALID_DOC)
    reg = load_rulesets(tmp_path)
    assert reg.errors == ()
    assert reg.get("TEST-LOAD-001") is not None


@pytest.mark.parametrize(
    "mutation",
    [
        {"valid_from": None},  # §12.1: no undated rules
        {"severity": "fatal"},  # not in hard|soft
        {"checks": [{"name": "c", "op": "between"}]},  # unsupported op
        {"select": {"t": [{"value": 1.0}]}},  # bracket without `when`
        {"source_reference": None},  # §12.1: no unsourced rules
        {"thresholds": {"a": "four"}},  # non-numeric threshold
        # M1: confidence_policy levels are probabilities — bounded to [0, 1] at
        # load time (a YAML typo like decided: 1.2 lands in registry.errors
        # instead of corrupting evaluations; the engine clamp is defense-in-depth
        # for raw access paths).
        {"confidence_policy": {"decided": 1.2}},
        {"confidence_policy": {"unknown": -0.1}},
    ],
)
def test_invalid_document_is_skipped_and_reported(
    tmp_path: Path, mutation: dict[str, Any]
) -> None:
    _write_ruleset(tmp_path, {**VALID_DOC, **mutation})
    reg = load_rulesets(tmp_path)
    assert reg.get("TEST-LOAD-001") is None  # never half-loaded
    assert reg.errors, f"expected schema errors for mutation {mutation}"


def test_validate_rule_document_reports_paths() -> None:
    errors = validate_rule_document({**VALID_DOC, "severity": "fatal"})
    assert any("severity" in e for e in errors)


def test_ruleset_errors_surfaced_in_explain_and_diagnostics(tmp_path: Path) -> None:
    """M5 regression: schema-validation failures recorded on the registry are
    visible in the ruleset_explain and diagnostics_run payloads."""
    from plot_mcp_server import usecases

    _write_ruleset(tmp_path, {**VALID_DOC, "severity": "fatal"})  # malformed rule
    reg = load_rulesets(tmp_path)
    assert reg.errors  # the loader skipped + recorded the broken rule

    explained = usecases.ruleset_explain(reg)
    assert explained["ruleset_errors"] == list(reg.errors)
    assert any("severity" in e for e in explained["ruleset_errors"])

    diag = usecases.diagnostics_run(
        ruleset_version=reg.ruleset_version,
        rule_count=len(reg.rules),
        dev_hot_reload=False,
        last_reload_at=None,
        server_version="test",
        ruleset_errors=list(reg.errors),
    )
    assert diag["ruleset_errors"] == list(reg.errors)


def test_rulecheck_model_shape() -> None:
    check = evaluate(make_rule(**SIMPLE), {"distance_m": 5.0})
    assert isinstance(check, RuleCheck)
    payload = check.model_dump()
    assert set(payload) == {
        "rule_id",
        "status",
        "severity",
        "message",
        "trace",
        "source_reference",
        "confidence",
        # Phase 10: optional GeoJSON evidence attached by the wt_validators —
        # backwards-compatible (default None; the engine itself never fills it).
        "geometry_evidence",
    }
    assert payload["geometry_evidence"] is None
