"""Candidate-validation mode for ``planning_parse_document`` (Phase 8 §29, F-0550).

Mode (b) of the parser contract: the **calling model** (Claude) performs its own
LLM extraction and supplies the result as structured ``candidates``. The server
NEVER treats that output as binding (§20.11) — each candidate must survive:

1. **JSON Schema validation** against
   ``schemas/planning-indicators.schema.json`` (canonical name, typed value,
   confidence, source_fragment present);
2. **value-type check** (numeric indicators must be numeric — schema allows the
   union, this narrows per name);
3. **hallucination rejection** (F-0550): the cited ``source_fragment.text`` must
   occur **verbatim** in the document text. A fragment that does not occur is
   rejected with a reason; offsets that disagree with the text are corrected to
   the real first occurrence (recorded, never silent).

Accepted candidates become :class:`~plot_planning.parser.extract.IndicatorExtraction`
records with ``method="llm_candidate"``; rejected ones are returned with reasons
so the calling model can fix its extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jsonschema

from plot_planning.parser.extract import IndicatorExtraction, SourceFragment
from plot_planning.parser.schema import (
    NUMERIC_INDICATORS,
    PLANNING_INDICATORS_SCHEMA,
)
from plot_planning.parser.security import neutralize_fragment

# Per-candidate validator over the IndicatorCandidate $def (same document as the
# committed schemas/planning-indicators.schema.json — a test pins them equal).
_CANDIDATE_VALIDATOR = jsonschema.Draft202012Validator(
    {
        "$ref": "#/$defs/IndicatorCandidate",
        "$defs": PLANNING_INDICATORS_SCHEMA["$defs"],
    }
)


@dataclass(frozen=True)
class RejectedCandidate:
    """A candidate that failed validation, with auditable reasons."""

    candidate: dict[str, Any]
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateValidation:
    """Outcome of validating one candidate batch against one document."""

    accepted: list[IndicatorExtraction]
    rejected: list[RejectedCandidate]


def _schema_errors(candidate: Any) -> list[str]:
    errors = []
    for err in _CANDIDATE_VALIDATOR.iter_errors(candidate):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {path}: {err.message}")
    return sorted(errors)


def validate_candidates(text: str, candidates: list[Any]) -> CandidateValidation:
    """Validate LLM-extracted ``candidates`` against the document ``text`` (§29).

    Every acceptance requires schema validity AND a verbatim source fragment; a
    candidate citing text that does not occur in the document is rejected with
    ``hallucination`` in the reason (F-0550) — it is never "fixed up" into the
    result.
    """
    accepted: list[IndicatorExtraction] = []
    rejected: list[RejectedCandidate] = []

    for raw in candidates:
        if not isinstance(raw, dict):
            rejected.append(
                RejectedCandidate(
                    candidate={"_raw": repr(raw)[:200]},
                    reasons=["schema: candidate must be a JSON object"],
                )
            )
            continue
        reasons = _schema_errors(raw)
        if reasons:
            rejected.append(RejectedCandidate(candidate=raw, reasons=reasons))
            continue

        name = raw["name"]
        value = raw["value"]
        if name in NUMERIC_INDICATORS and not isinstance(value, int | float):
            rejected.append(
                RejectedCandidate(
                    candidate=raw,
                    reasons=[f"value: indicator '{name}' requires a numeric value"],
                )
            )
            continue
        if isinstance(value, bool) and name in NUMERIC_INDICATORS:
            rejected.append(
                RejectedCandidate(
                    candidate=raw,
                    reasons=[f"value: indicator '{name}' requires a numeric value, got boolean"],
                )
            )
            continue

        fragment = raw["source_fragment"]
        quote = fragment["text"]
        found_at = text.find(quote)
        if found_at < 0:
            rejected.append(
                RejectedCandidate(
                    candidate=raw,
                    reasons=[
                        "hallucination: source_fragment.text does not occur verbatim "
                        "in the document (F-0550) — candidate rejected, never defaulted"
                    ],
                )
            )
            continue

        start = fragment.get("start")
        end = fragment.get("end")
        offsets_corrected = False
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or text[start:end] != quote
        ):
            # The quote is real but the offsets are not — correct to the first
            # verbatim occurrence and record the correction (never silent).
            start, end = found_at, found_at + len(quote)
            offsets_corrected = True

        safe_text, redacted = neutralize_fragment(quote)
        confidence = float(raw["confidence"])
        if offsets_corrected:
            confidence = min(confidence, 0.7)
        if redacted:
            confidence = min(confidence, 0.3)

        accepted.append(
            IndicatorExtraction(
                name=name,
                value=float(value) if name in NUMERIC_INDICATORS else value,
                unit=raw.get("unit"),
                zone_symbol=raw.get("zone_symbol"),
                confidence=confidence,
                source_fragment=SourceFragment(text=safe_text, start=start, end=end),
                method="llm_candidate",
                redacted=redacted,
            )
        )
    return CandidateValidation(accepted=accepted, rejected=rejected)
