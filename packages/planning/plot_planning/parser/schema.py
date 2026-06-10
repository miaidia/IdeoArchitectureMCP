"""JSON Schema for planning-indicator candidates (Phase 8 §29 parser DoD).

This is the validation gate between *anything* that extracts indicators (the
deterministic regex extractors AND the calling model's LLM candidates) and the
domain: every candidate must carry a canonical indicator ``name``, a typed
``value``, a ``confidence`` and a verbatim ``source_fragment`` citation —
LLM output is never binding without confidence and evidence (§20.11, §29).

The committed copy lives at ``schemas/planning-indicators.schema.json`` (same
convention as the other contract schemas); a test asserts file == this dict so
they cannot drift.
"""

from __future__ import annotations

from typing import Any

#: Canonical indicator names — the v2 §8.1.3 extraction target set.
INDICATOR_NAMES: tuple[str, ...] = (
    "max_intensity",
    "min_intensity",
    "max_height_m",
    "max_kondygnacje",
    "max_coverage_ratio",
    "min_pbc_ratio",
    "parking_per_mieszkanie",
    "parking_per_100m2_uslug",
    "linia_zabudowy",
    "dach_constraints",
    "zabudowa_srodmiejska",
)

#: Indicators whose value must be numeric.
NUMERIC_INDICATORS: frozenset[str] = frozenset(
    {
        "max_intensity",
        "min_intensity",
        "max_height_m",
        "max_kondygnacje",
        "max_coverage_ratio",
        "min_pbc_ratio",
        "parking_per_mieszkanie",
        "parking_per_100m2_uslug",
    }
)

PLANNING_INDICATORS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://plot-analyzer/schemas/planning-indicators.schema.json",
    "title": "PlanningIndicatorCandidates",
    "description": (
        "Planning-indicator candidates extracted from an MPZP/POG/WZ document "
        "(base_assumptions §29 parser DoD; IMPLEMENTATION_PLAN_V2 §8.1.3). Every "
        "candidate requires a verbatim source_fragment citation + confidence — a "
        "candidate without verifiable evidence is rejected (F-0550)."
    ),
    "type": "array",
    "items": {"$ref": "#/$defs/IndicatorCandidate"},
    "$defs": {
        "SourceFragment": {
            "title": "SourceFragment",
            "description": (
                "Verbatim quote from the document backing the indicator "
                "(NFR-AUD-002/005). 'start'/'end' are character offsets into the "
                "document text; the quote must occur verbatim or the candidate is "
                "rejected (hallucination rejection, F-0550)."
            ),
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 3},
                "start": {"type": "integer", "minimum": 0},
                "end": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "IndicatorCandidate": {
            "title": "IndicatorCandidate",
            "type": "object",
            "required": ["name", "value", "source_fragment", "confidence"],
            "properties": {
                "name": {
                    "description": "Canonical indicator name (v2 §8.1.3 set).",
                    "enum": list(INDICATOR_NAMES),
                },
                "value": {
                    "description": (
                        "Indicator value: number for numeric indicators (ratios as "
                        "fractions, e.g. PBC 25% -> 0.25), string for "
                        "linia_zabudowy/dach_constraints, boolean for "
                        "zabudowa_srodmiejska."
                    ),
                    "type": ["number", "string", "boolean"],
                },
                "unit": {"type": ["string", "null"]},
                "zone_symbol": {
                    "description": "Zone symbol the provision applies to, if scoped.",
                    "type": ["string", "null"],
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "source_fragment": {"$ref": "#/$defs/SourceFragment"},
                "method": {
                    "description": "How the candidate was produced.",
                    "enum": ["deterministic_regex", "llm_candidate"],
                },
            },
            "additionalProperties": False,
        },
    },
}
