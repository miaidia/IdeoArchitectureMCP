"""Ruleset regression suite runner (Phase 16; F-0559, §18.3 "rulesets tested").

Three layers over the WHOLE ``rulesets/PL`` registry:

1. **coverage** — every loaded rule id has ≥1 golden vector in
   ``tests/ruleset_vectors.py``; a NEW rule without a vector fails the suite;
2. **golden vectors** — every vector evaluates to its expected status through
   the real engine (no loader/engine drift);
3. **evaluability** — every rule is evaluable with (a) its declared inputs all
   missing and (b) the engine never raises, never silently passes a hard rule
   on missing inputs (the §21 guarantee swept across the corpus, not just the
   hand-picked rules of the per-rule tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from plot_rules import RuleStatus, evaluate, load_rulesets
from tests.ruleset_vectors import VECTORS

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def registry():
    return load_rulesets(REPO_ROOT / "rulesets" / "PL")


def test_every_rule_has_at_least_one_vector(registry) -> None:
    """Fail on untested NEW rules (and on vectors for rules that disappeared)."""
    loaded = {r.id for r in registry.rules}
    covered = set(VECTORS)
    missing = sorted(loaded - covered)
    stale = sorted(covered - loaded)
    assert not missing, (
        f"rules WITHOUT a regression vector: {missing} — add golden vectors to "
        "tests/ruleset_vectors.py before shipping a new rule (F-0559/§18.3)"
    )
    assert not stale, f"vectors for unknown rules: {stale}"


def _vector_cases() -> list[tuple[str, int, dict]]:
    return [
        (rule_id, i, vector)
        for rule_id, vectors in sorted(VECTORS.items())
        for i, vector in enumerate(vectors)
    ]


@pytest.mark.parametrize(
    ("rule_id", "index", "vector"),
    _vector_cases(),
    ids=[f"{rid}-{i}" for rid, i, _ in _vector_cases()],
)
def test_golden_vector_evaluates_to_expected_status(
    registry, rule_id: str, index: int, vector: dict
) -> None:
    rule = registry.get(rule_id)
    assert rule is not None, f"{rule_id} not loaded"
    check = evaluate(rule, vector["inputs"], mode=vector.get("mode", "conservative"))
    assert check.status is RuleStatus(vector["expected"]), (
        f"{rule_id}[{index}] {vector.get('note', '')}: expected "
        f"{vector['expected']}, got {check.status.value} — {check.message}"
    )
    # Every evaluation carries the §25.1 decomposition (Phase 16 wiring).
    assert "confidence_components" in check.trace


def test_every_rule_evaluable_with_declared_inputs_missing(registry) -> None:
    """Loader-drift sweep: evaluating ANY rule with all declared inputs absent
    must return a RuleCheck (never raise) and must NEVER be a silent pass."""
    for rule in registry.rules:
        check = evaluate(rule, dict.fromkeys(rule.inputs), mode="conservative")
        assert check.rule_id == rule.id
        assert check.status in RuleStatus
        assert check.status is not RuleStatus.PASS, (
            f"{rule.id}: PASS with every declared input missing — silent-pass "
            "guard violated (§21)"
        )


def test_every_rule_evaluable_with_vector_inputs_in_every_mode(registry) -> None:
    """Mode sweep (F-0134-0138): each golden vector evaluates without raising in
    strict/conservative/optimistic; decided outcomes are mode-independent."""
    for rule_id, vectors in VECTORS.items():
        rule = registry.get(rule_id)
        assert rule is not None
        for vector in vectors:
            statuses = {
                mode: evaluate(rule, vector["inputs"], mode=mode).status
                for mode in ("strict", "conservative", "optimistic")
            }
            if vector["expected"] in ("pass", "fail", "not_applicable") and not vector.get(
                "mode"
            ):
                assert len(set(statuses.values())) == 1, (
                    f"{rule_id}: decided vector drifted across modes: {statuses}"
                )
