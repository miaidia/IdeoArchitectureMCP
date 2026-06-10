"""Planning-document parser (Phase 8 §29 DoD): extract → validate → evidence.

Two modes behind ``planning_parse_document`` (the server does NOT call an LLM):

* **(a) deterministic** — :func:`extract_indicators`: regex/keyword extractors
  for the §8.1.3 indicator set, each with a verbatim ``source_fragment``;
* **(b) candidate validation** — :func:`validate_candidates`: the calling model
  supplies its own LLM extraction as structured JSON; the server validates it
  against ``schemas/planning-indicators.schema.json`` AND verifies every cited
  fragment occurs verbatim in the document (hallucination rejection, F-0550).

Both run in untrusted-content mode (:mod:`plot_planning.parser.security`,
NFR-SEC-002/003/009).
"""

from __future__ import annotations

from plot_planning.parser.extract import (
    IndicatorExtraction,
    SourceFragment,
    extract_indicators,
    missing_indicators,
)
from plot_planning.parser.schema import (
    INDICATOR_NAMES,
    NUMERIC_INDICATORS,
    PLANNING_INDICATORS_SCHEMA,
)
from plot_planning.parser.security import (
    MAX_DOCUMENT_CHARS,
    ScreenResult,
    injection_flags,
    neutralize_fragment,
    screen_document,
)
from plot_planning.parser.validate import (
    CandidateValidation,
    RejectedCandidate,
    validate_candidates,
)

__all__ = [
    "INDICATOR_NAMES",
    "MAX_DOCUMENT_CHARS",
    "NUMERIC_INDICATORS",
    "PLANNING_INDICATORS_SCHEMA",
    "CandidateValidation",
    "IndicatorExtraction",
    "RejectedCandidate",
    "ScreenResult",
    "SourceFragment",
    "extract_indicators",
    "injection_flags",
    "missing_indicators",
    "neutralize_fragment",
    "screen_document",
    "validate_candidates",
]
