"""JSON Schema for declarative ruleset YAML files (Phase 8, base_assumptions §12.1-§12.2).

Every rule document (any YAML under ``rulesets/`` that carries an ``id`` key) must
validate against :data:`RULESET_SCHEMA` before it enters the registry. The schema
covers the §12.2 frontmatter (id/title/jurisdiction/valid_from/valid_to/
source_reference/inputs/outputs/logic) plus the Phase 8 engine extensions
(``severity``, ``applies_when``, ``thresholds``, ``select``, ``checks``,
``input_defaults``) so a typo in a legal threshold table is caught at load time
instead of silently skewing an evaluation.

YAML parses bare dates into ``datetime.date`` objects, which JSON Schema cannot
type-check, so callers must normalize documents with :func:`normalize_document`
(dates -> ISO strings) before validating.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import jsonschema

# A numeric range condition usable inside `applies_when`/modifier `when` maps:
# min/max are inclusive, gt/lt exclusive; all present bounds must hold. Generic
# mechanism so rules can gate checks/modifiers on value ranges (e.g. WT §13
# ust. 3 width brackets) without putting legal values into the engine.
_CONDITION_BRACKET = {
    "type": "object",
    "properties": {
        "min": {"type": "number"},
        "max": {"type": "number"},
        "gt": {"type": "number"},
        "lt": {"type": "number"},
    },
    "additionalProperties": False,
    "minProperties": 1,
}

# A condition map used by `applies_when` (rule- and check-level) and modifier
# `when`: input name -> expected scalar (equality), list of scalars
# (membership), or a numeric range bracket. §12 "simple equality/flag
# conditions" + the Phase 8 range form.
_CONDITIONS = {
    "type": "object",
    "additionalProperties": {
        "anyOf": [
            {"type": ["string", "number", "boolean", "null"]},
            {"type": "array", "items": {"type": ["string", "number", "boolean"]}},
            _CONDITION_BRACKET,
        ]
    },
}

# One bracket of a `select` table: {when: {param: {min/max} | scalar}, value: ...}.
# Exactly one of value / value_per_unit / value_from_input provides the result.
_BRACKET = {
    "type": "object",
    "required": ["when"],
    "properties": {
        "when": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                        },
                        "additionalProperties": False,
                        "minProperties": 1,
                    },
                    {"type": ["string", "number", "boolean"]},
                ]
            },
        },
        "value": {"type": "number"},
        # value = value_per_unit * bracket-param value (e.g. 1 m2 per mieszkanie).
        "value_per_unit": {"type": "number"},
        # value is read from another input at evaluation time (e.g. wysokosc
        # przeslaniania as the required distance, WT §13 ust. 1 pkt 1 lit. a).
        "value_from_input": {"type": "string"},
    },
    "oneOf": [
        {"required": ["value"]},
        {"required": ["value_per_unit"]},
        {"required": ["value_from_input"]},
    ],
}

# A threshold modifier: multiply the resolved threshold when a flag matches
# (e.g. zabudowa srodmiejska halves WT §13/§60/§40 values; ppoz §271 ust. 2-7
# percentage adjustments). `at_least` clamps the modified value from below.
_MODIFIER = {
    "type": "object",
    "required": ["when", "multiply"],
    "properties": {
        "when": _CONDITIONS,
        "multiply": {"type": "number", "exclusiveMinimum": 0},
        "at_least": {"type": "number"},
        "note": {"type": "string"},
    },
    "additionalProperties": False,
}

_CHECK = {
    "type": "object",
    "required": ["name", "op"],
    "properties": {
        "name": {"type": "string"},
        "op": {
            "enum": ["gte", "lte", "gt", "lt", "eq", "ne", "present", "resolve", "exempt"]
        },
        "input": {"type": "string"},
        "applies_when": _CONDITIONS,
        # Comparison target: exactly one of threshold (named or literal), select
        # (named bracket table), other_input (another input), value (literal).
        "threshold": {"type": ["string", "number"]},
        "select": {"type": "string"},
        "other_input": {"type": "string"},
        "value": {"type": ["string", "number", "boolean"]},
        "modifiers": {"type": "array", "items": _MODIFIER},
        "message": {"type": "string"},
        "source_reference": {"type": "string"},
    },
    "additionalProperties": False,
}

RULESET_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Plot Analyzer declarative ruleset document (base_assumptions §12.2 + Phase 8)",
    "type": "object",
    # §12.1: every rule is versioned (valid_from) and sourced (source_reference).
    "required": [
        "id",
        "title",
        "jurisdiction",
        "valid_from",
        "source_reference",
        "inputs",
        "outputs",
    ],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "jurisdiction": {"type": "string", "minLength": 1},
        "valid_from": {"type": "string", "minLength": 4},
        "valid_to": {"type": ["string", "null"]},
        "source_reference": {"type": "string", "minLength": 1},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "outputs": {"type": "array", "items": {"type": "string"}},
        "logic": {},
        "severity": {"enum": ["hard", "soft"]},
        "applies_when": _CONDITIONS,
        "thresholds": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
        "select": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": _BRACKET, "minItems": 1},
        },
        "checks": {"type": "array", "items": _CHECK},
        # Documented conservative assumptions applied when an input is absent;
        # recorded in the evaluation trace as `assumed_inputs` (never silent).
        "input_defaults": {"type": "object"},
        "confidence_policy": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
        # NFR-AUD-005: literal fragment of the legal text + how it was verified.
        "source_quote": {"type": "string"},
        "verification": {"enum": ["isap_primary", "secondary_source"]},
        "notes": {"type": ["string", "array"]},
    },
    # Tolerate extra documentation keys (e.g. the legacy example rules carry
    # default_* scalars used by the hot-reload proof test).
    "additionalProperties": True,
}

_VALIDATOR = jsonschema.Draft202012Validator(RULESET_SCHEMA)


def normalize_document(doc: Any) -> Any:
    """Recursively convert YAML date/datetime scalars to ISO strings.

    ``yaml.safe_load`` parses bare ``2024-08-01`` into ``datetime.date``, which
    JSON Schema has no type for; normalizing keeps the schema purely JSON-typed.
    """
    if isinstance(doc, _dt.datetime | _dt.date):
        return doc.isoformat()
    if isinstance(doc, dict):
        return {k: normalize_document(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [normalize_document(v) for v in doc]
    return doc


def validate_rule_document(doc: dict[str, Any]) -> list[str]:
    """Validate a (normalized) rule document; return human-readable error strings.

    Empty list means the document is structurally valid. The loader records the
    errors on the registry and skips the rule — a malformed rule must never be
    half-loaded (it would evaluate against a broken threshold table).
    """
    errors = []
    for err in _VALIDATOR.iter_errors(doc):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return sorted(errors)
