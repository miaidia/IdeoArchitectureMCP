"""Declarative versioned ruleset loader (Phase 2 backbone; engine logic in Phase 8).

Phase 2 adds :func:`load_rulesets` and an in-memory :class:`RulesetRegistry` so
rules can be loaded *fresh* and reflected live via hot-reload (F-0133 / F-0440).
The rule-evaluation engine (pass/fail/warning/unknown/not_applicable, traces) lands
in Phase 8.
"""

from plot_rules.loader import Rule, RulesetRegistry, load_rulesets

__version__ = "0.1.0"

__all__ = ["Rule", "RulesetRegistry", "load_rulesets"]
