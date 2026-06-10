"""Deterministic indicator extractors for MPZP/POG/WZ text (Phase 8 §8.1.3, F-0112–0120).

Mode (a) of ``planning_parse_document``: regex/keyword heuristics over the
document text produce :class:`IndicatorExtraction` candidates for the v2 §8.1.3
indicator set. Every extraction carries a **verbatim** ``source_fragment`` with
character offsets (``text[start:end] == fragment`` by construction — NFR-AUD-002)
and a deterministic confidence. A missing indicator is simply absent from the
result — the caller records an :class:`~plot_domain.UnknownItem`, NEVER a default
value (§21, Phase 8 anti-pattern guard).

The extractors only read indicator patterns; instruction-like content in the
document cannot match them, so prompt injection cannot steer the output
(NFR-SEC-003 — see :mod:`plot_planning.parser.security` for the screening layer).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from plot_planning.parser.security import neutralize_fragment

#: Decimal number with Polish comma: "1,2" / "16" / "0.25".
_NUM = r"(\d+(?:[.,]\d+)?)"
_INT = r"(\d+)"


class SourceFragment(BaseModel):
    """Verbatim citation: quote + char offsets into the document (NFR-AUD-002)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=3, description="Verbatim quote from the document.")
    start: int = Field(ge=0, description="Start char offset into the document text.")
    end: int = Field(ge=0, description="End char offset into the document text.")


class IndicatorExtraction(BaseModel):
    """One extracted indicator candidate (schema-shaped, §29)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Canonical indicator name (v2 §8.1.3 set).")
    value: float | str | bool = Field(description="Extracted value (ratios as fractions).")
    unit: str | None = Field(default=None, description="Unit, if any.")
    zone_symbol: str | None = Field(default=None, description="Zone scope, if derivable.")
    confidence: float = Field(ge=0.0, le=1.0, description="Extraction confidence.")
    source_fragment: SourceFragment = Field(description="Verbatim citation.")
    method: str = Field(default="deterministic_regex", description="Producer of the candidate.")
    redacted: bool = Field(
        default=False,
        description="True when the citation was redacted (instruction-like content, NFR-SEC-003).",
    )

    def to_candidate(self) -> dict[str, Any]:
        """Schema-conforming candidate dict (planning-indicators.schema.json)."""
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "zone_symbol": self.zone_symbol,
            "confidence": self.confidence,
            "source_fragment": self.source_fragment.model_dump(),
            "method": self.method,
        }


def _num(value: str) -> float:
    return float(value.replace(",", "."))


# --------------------------------------------------------------------------- #
# Patterns. Each entry: (indicator name, regex, value builder, unit, confidence).
# The whole match is the citation fragment, so offsets are exact by construction.
# --------------------------------------------------------------------------- #
_F = re.IGNORECASE | re.UNICODE

_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str | None, float], ...] = (
    # intensywność zabudowy — max / min (F-0113/0114)
    (
        "max_intensity",
        re.compile(
            r"(?:maksymaln\w+\s+intensywno[śs][ćc]\s+zabudowy|intensywno[śs][ćc]\s+zabudowy[^;.\n]{0,60}?"
            r"(?:maksymaln\w+|nie\s+wi[ęe]ksz\w+\s+ni[żz]))[^0-9;.\n]{0,40}?" + _NUM,
            _F,
        ),
        "float",
        None,
        0.85,
    ),
    (
        "min_intensity",
        re.compile(
            r"(?:minimaln\w+\s+intensywno[śs][ćc]\s+zabudowy|intensywno[śs][ćc]\s+zabudowy[^;.\n]{0,60}?"
            r"(?:minimaln\w+|nie\s+mniejsz\w+\s+ni[żz]))[^0-9;.\n]{0,40}?" + _NUM,
            _F,
        ),
        "float",
        None,
        0.85,
    ),
    # maksymalna wysokość zabudowy ... 16 m (F-0114)
    (
        "max_height_m",
        re.compile(
            r"(?:maksymaln\w+\s+wysoko[śs][ćc]\s+zabudowy|wysoko[śs][ćc]\s+zabudowy[^;.\n]{0,40}?"
            r"(?:do|nie\s+wi[ęe]cej\s+ni[żz]|nie\s+wy[żz]sz\w+\s+ni[żz]|maksymaln\w+))"
            r"[^0-9;.\n]{0,30}?" + _NUM + r"\s*m\b",
            _F,
        ),
        "float",
        "m",
        0.85,
    ),
    # do 5 kondygnacji nadziemnych / nie więcej niż 5 kondygnacji
    (
        "max_kondygnacje",
        re.compile(
            r"\b(?:do|nie\s+wi[ęe]cej\s+ni[żz]|maksymalnie)\s+" + _INT + r"\s+kondygnacj\w+",
            _F,
        ),
        "int",
        "kondygnacje",
        0.8,
    ),
    # maksymalna powierzchnia zabudowy ... 40% (ratio)
    (
        "max_coverage_ratio",
        re.compile(
            r"(?:maksymaln\w+\s+)?(?:wielko[śs][ćc]\s+)?powierzchni\w*\s+zabudowy[^;.\n%]{0,80}?"
            + _NUM
            + r"\s*%",
            _F,
        ),
        "pct",
        "ratio",
        0.8,
    ),
    # powierzchnia biologicznie czynna ... minimum 25% (ratio)
    (
        "min_pbc_ratio",
        re.compile(
            r"(?:minimaln\w+\s+|minimum\s+)?(?:udzia[łl]\w*\s+)?powierzchni\w*\s+biologicznie\s+czynn\w+"
            r"[^;.\n%]{0,80}?" + _NUM + r"\s*%",
            _F,
        ),
        "pct",
        "ratio",
        0.8,
    ),
    # 1,2 miejsca postojowego na 1 mieszkanie (F-0116)
    (
        "parking_per_mieszkanie",
        re.compile(
            _NUM
            + r"\s+(?:miejsc\w*\s+postojow\w+|stanowisk\w*\s+postojow\w+)\s+na\s+"
            r"(?:1\s+|ka[żz]d\w+\s+)?(?:mieszkanie|lokal\s+mieszkaln\w+)",
            _F,
        ),
        "float",
        "mp/mieszkanie",
        0.85,
    ),
    # 3 miejsca postojowe na każde 100 m2 powierzchni usług (F-0116)
    (
        "parking_per_100m2_uslug",
        re.compile(
            _NUM
            + r"\s+(?:miejsc\w*\s+postojow\w+|stanowisk\w*)\s+na\s+(?:ka[żz]d\w+\s+)?100\s*m[²2]?\s+"
            r"(?:powierzchni\s+)?(?:u[żz]ytkowej\s+)?us[łl]ug\w*",
            _F,
        ),
        "float",
        "mp/100m2",
        0.85,
    ),
    # nieprzekraczalna / obowiązująca linia zabudowy (F-0117)
    (
        "linia_zabudowy",
        re.compile(r"(nieprzekraczaln\w+|obowi[ąa]zuj[ąa]c\w+)\s+lini\w+\s+zabudowy", _F),
        "linia",
        None,
        0.85,
    ),
    # dach: geometria/typ (F-0119) — descriptor string, never a guessed number
    (
        "dach_constraints",
        re.compile(
            r"dach\w*\s+(?:p[łl]ask\w+|dwuspadow\w+|wielospadow\w+|spadzist\w+|strom\w+)"
            r"(?:\s+(?:lub|i|albo)\s+(?:p[łl]ask\w+|dwuspadow\w+|wielospadow\w+|spadzist\w+|strom\w+))?"
            r"(?:\s+o\s+k[ąa]cie\s+nachylenia[^;.\n]{0,40}?\d+\s*(?:stopni|°)?)?",
            _F,
        ),
        "text",
        None,
        0.7,
    ),
    # zabudowa śródmiejska flag (drives §13/§60/§40 reductions)
    (
        "zabudowa_srodmiejska",
        re.compile(r"zabudow\w+\s+[śs]r[óo]dmiejsk\w+", _F),
        "flag",
        None,
        0.75,
    ),
)


def _build_value(kind: str, match: re.Match[str]) -> float | str | bool:
    if kind == "float":
        return _num(match.group(1))
    if kind == "int":
        return float(int(match.group(1)))
    if kind == "pct":
        return round(_num(match.group(1)) / 100.0, 4)
    if kind == "linia":
        word = match.group(1).lower()
        return "nieprzekraczalna" if word.startswith("nieprzekraczaln") else "obowiazujaca"
    if kind == "text":
        return match.group(0).strip()
    if kind == "flag":
        return True
    raise ValueError(f"unknown value kind: {kind}")


def extract_indicators(text: str) -> list[IndicatorExtraction]:
    """Run the deterministic extractors over ``text`` (mode (a) of the parser).

    Returns at most one extraction per indicator (the first match wins; further
    matches of the same indicator are ignored — zone-scoped multi-value parsing
    is a later-phase refinement). Fragments are verbatim with exact offsets.
    """
    out: list[IndicatorExtraction] = []
    seen: set[str] = set()
    for name, pattern, kind, unit, confidence in _PATTERNS:
        if name in seen:
            continue
        match = pattern.search(text)
        if match is None:
            continue
        fragment_text = match.group(0)
        start, end = match.span()
        assert text[start:end] == fragment_text  # verbatim by construction
        safe_text, redacted = neutralize_fragment(fragment_text)
        out.append(
            IndicatorExtraction(
                name=name,
                value=_build_value(kind, match),
                unit=unit,
                confidence=confidence if not redacted else min(confidence, 0.3),
                source_fragment=SourceFragment(text=safe_text, start=start, end=end),
                redacted=redacted,
            )
        )
        seen.add(name)
    return out


def missing_indicators(extractions: list[IndicatorExtraction]) -> list[str]:
    """Indicator names from the §8.1.3 set absent from ``extractions`` (→ unknowns)."""
    from plot_planning.parser.schema import INDICATOR_NAMES

    present = {e.name for e in extractions}
    return [n for n in INDICATOR_NAMES if n not in present]
