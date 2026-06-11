"""Declarative versioned ruleset loader + evaluation engine (Phase 2 + Phase 8).

Phase 2 adds :func:`load_rulesets` and an in-memory :class:`RulesetRegistry` so
rules can be loaded *fresh* and reflected live via hot-reload (F-0133 / F-0440).
Phase 8 adds the rule-evaluation engine (:func:`evaluate` ->
``pass/fail/warning/unknown/not_applicable`` with trace, evaluation modes and the
audited expert-override hook; base_assumptions §12, F-0128-0138) and JSON Schema
validation of ruleset documents (:mod:`plot_rules.schema`).
"""

from plot_rules.engine import (
    EvaluationMode,
    RuleCheck,
    RuleStatus,
    evaluate,
    override_subject,
    override_targets_rule,
)
from plot_rules.loader import Rule, RulesetRegistry, clear_registry_cache, load_rulesets
from plot_rules.overrides import DEFAULT_OVERRIDE_STORE, OverrideStore
from plot_rules.schema import RULESET_SCHEMA, validate_rule_document

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_OVERRIDE_STORE",
    "RULESET_SCHEMA",
    "EvaluationMode",
    "OverrideStore",
    "Rule",
    "RuleCheck",
    "RuleStatus",
    "RulesetRegistry",
    "evaluate",
    "load_rulesets",
    "clear_registry_cache",
    "override_subject",
    "override_targets_rule",
    "validate_rule_document",
]
