"""Read scalar rule values from the loaded ruleset (Phase 4 §4.2 evaluator input).

The Phase 2 :class:`plot_rules.Rule` keeps the canonical fields (id/title/valid_from/
logic/...) but NOT arbitrary extra scalars. The example rule
``rulesets/PL/planning/mn-coverage.yaml`` carries ``default_max_coverage_ratio`` — the
value the self-improve harness edits to prove a regression. We re-read the rule's YAML
file fresh (same approach as ``plot_mcp_server.usecases._rule_raw``) so an edited value
is visible on the next load. This is READ-ONLY: the loop re-loads and re-scores; it
NEVER writes legal values (§12, NFR-AUD-003).
"""

from __future__ import annotations

from typing import Any

import yaml
from plot_rules import RulesetRegistry

# Rule id of the example coverage rule (rulesets/PL/planning/mn-coverage.yaml).
COVERAGE_RULE_ID = "PL-PLAN-MN-COVERAGE-001"
COVERAGE_RATIO_KEY = "default_max_coverage_ratio"
# Conservative fallback when the rule / value is absent (so scoring still runs).
FALLBACK_COVERAGE_RATIO = 0.30


def _rule_raw(path: str) -> dict[str, Any]:
    """Re-read a rule's YAML file fresh so edited scalars are visible (hot-reload)."""
    try:
        with open(path, "rb") as fh:
            doc = yaml.safe_load(fh) or {}
        return doc if isinstance(doc, dict) else {}
    except OSError:
        return {}


def max_coverage_ratio(registry: RulesetRegistry) -> tuple[float, str | None, bool]:
    """Return ``(ratio, source_rule_id, found)`` for the max building-coverage ratio.

    ``found`` is ``False`` when the rule/value is missing — callers should treat the
    coverage check as ``unknown`` rather than fake a pass (§20.10, no fake scores).
    """
    rule = registry.get(COVERAGE_RULE_ID)
    if rule is None:
        return FALLBACK_COVERAGE_RATIO, None, False
    raw = _rule_raw(rule.path)
    value = raw.get(COVERAGE_RATIO_KEY)
    if value is None:
        return FALLBACK_COVERAGE_RATIO, rule.id, False
    return float(value), rule.id, True
