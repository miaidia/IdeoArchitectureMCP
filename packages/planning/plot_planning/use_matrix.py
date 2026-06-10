"""Use matrix per zone symbol (Phase 8 Task 3; F-0121–0123).

Maps a planning-zone symbol (MPZP convention: MN, MW, U, P, ZP, …; compounds like
"MN/U") onto ``allowed | conditional | forbidden | unknown`` for the investment
categories. The base table encodes **symbol conventions** (planning practice —
``basis: symbol_convention``), NOT legal thresholds: MPZP zone symbols have no
nationally fixed legal semantics, so every result carries the basis + confidence
and an unknown symbol yields ``unknown`` across the board (never a guess, §21).
Parsed textual provisions (nakazy/zakazy, F-0118) refine the convention — a
provision-derived status records its citation in the trace.
"""

from __future__ import annotations

import re
from typing import Any

#: Investment categories of the matrix (Phase 8B Task 3).
USE_CATEGORIES: tuple[str, ...] = (
    "mieszkalnictwo_jednorodzinne",
    "mieszkalnictwo_wielorodzinne",
    "uslugi",
    "produkcja",
    "magazyny",
)

_ALLOWED = "allowed"
_CONDITIONAL = "conditional"
_FORBIDDEN = "forbidden"
_UNKNOWN = "unknown"

#: Permissiveness order for combining compound symbols (MN/U → component max).
_RANK = {_FORBIDDEN: 0, _CONDITIONAL: 1, _ALLOWED: 2}

#: Base convention table: zone symbol → category → status.
#: basis: symbol_convention (planning practice, NOT law) — refined by provisions.
_BASE_MATRIX: dict[str, dict[str, str]] = {
    # zabudowa mieszkaniowa jednorodzinna
    "MN": {
        "mieszkalnictwo_jednorodzinne": _ALLOWED,
        "mieszkalnictwo_wielorodzinne": _FORBIDDEN,
        "uslugi": _CONDITIONAL,
        "produkcja": _FORBIDDEN,
        "magazyny": _FORBIDDEN,
    },
    # zabudowa mieszkaniowa wielorodzinna
    "MW": {
        "mieszkalnictwo_jednorodzinne": _CONDITIONAL,
        "mieszkalnictwo_wielorodzinne": _ALLOWED,
        "uslugi": _CONDITIONAL,
        "produkcja": _FORBIDDEN,
        "magazyny": _FORBIDDEN,
    },
    # usługi
    "U": {
        "mieszkalnictwo_jednorodzinne": _CONDITIONAL,
        "mieszkalnictwo_wielorodzinne": _CONDITIONAL,
        "uslugi": _ALLOWED,
        "produkcja": _CONDITIONAL,
        "magazyny": _CONDITIONAL,
    },
    # produkcja / przemysł
    "P": {
        "mieszkalnictwo_jednorodzinne": _FORBIDDEN,
        "mieszkalnictwo_wielorodzinne": _FORBIDDEN,
        "uslugi": _CONDITIONAL,
        "produkcja": _ALLOWED,
        "magazyny": _ALLOWED,
    },
    # zieleń urządzona / lasy / rolnicze / wody / drogi — non-building zones
    "ZP": dict.fromkeys(USE_CATEGORIES, _FORBIDDEN),
    "ZL": dict.fromkeys(USE_CATEGORIES, _FORBIDDEN),
    "R": dict.fromkeys(USE_CATEGORIES, _FORBIDDEN),
    "WS": dict.fromkeys(USE_CATEGORIES, _FORBIDDEN),
    "KD": dict.fromkeys(USE_CATEGORIES, _FORBIDDEN),
    "KDW": dict.fromkeys(USE_CATEGORIES, _FORBIDDEN),
}

#: Keywords mapping provision text to a category (F-0118 zakazy/nakazy refinement).
_CATEGORY_KEYWORDS: dict[str, re.Pattern[str]] = {
    "mieszkalnictwo_jednorodzinne": re.compile(r"jednorodzinn\w+", re.IGNORECASE),
    "mieszkalnictwo_wielorodzinne": re.compile(r"wielorodzinn\w+", re.IGNORECASE),
    "uslugi": re.compile(r"us[łl]ug\w+", re.IGNORECASE),
    "produkcja": re.compile(r"produkcyjn\w+|produkcj\w+|przemys[łl]\w+", re.IGNORECASE),
    "magazyny": re.compile(r"magazyn\w+|sk[łl]adow\w+", re.IGNORECASE),
}

_FORBID_RE = re.compile(r"zakaz\w*|zakazuje\s+si[ęe]|wyklucza\s+si[ęe]", re.IGNORECASE)
_PERMIT_RE = re.compile(r"dopuszcza\s+si[ęe]|dopuszczeni\w+", re.IGNORECASE)


def _split_symbol(symbol: str) -> list[str]:
    """Split a compound symbol ('MN/U', '1MW-U', 'MNU') into known base symbols."""
    cleaned = re.sub(r"^\d+", "", symbol.strip().upper())
    parts = [p for p in re.split(r"[/,\-.\s]+", cleaned) if p]
    known = [p for p in parts if p in _BASE_MATRIX]
    if known:
        return known
    # Concatenated form (e.g. 'MNU') — greedy longest-symbol decomposition.
    # The WHOLE symbol must decompose into known base symbols: an unrecognized
    # remainder (e.g. 'MNE' → 'MN' + 'E'?) means the symbol is NOT a known
    # compound, so it is reported as unknown rather than silently truncated
    # to its recognized prefix (§21 — never a guess).
    out: list[str] = []
    rest = cleaned
    while rest:
        for sym in sorted(_BASE_MATRIX, key=len, reverse=True):
            if rest.startswith(sym):
                out.append(sym)
                rest = rest[len(sym):]
                break
        else:
            return []
    return out


def _combine(statuses: list[str]) -> str:
    """Most permissive component wins (an MN/U zone allows both MN and U uses)."""
    return max(statuses, key=lambda s: _RANK[s])


def use_matrix_for(symbol: str, provisions: list[str] | None = None) -> dict[str, Any]:
    """Build the use matrix for ``symbol`` (F-0121–0123).

    ``provisions`` are parsed textual sentences from the act (ustalenia, F-0118);
    a ``zakaz``-style provision forces ``forbidden``, a ``dopuszcza się`` lifts a
    category to at least ``conditional`` — each refinement records its citation
    in the trace. An unrecognized symbol yields ``unknown`` for every category
    (F-0111: unknown symbol is reported, never guessed).
    """
    trace: list[dict[str, Any]] = []
    parts = _split_symbol(symbol)
    if not parts:
        categories = dict.fromkeys(USE_CATEGORIES, _UNKNOWN)
        trace.append({"step": "symbol_lookup", "symbol": symbol, "result": "unknown symbol"})
        return {
            "symbol": symbol,
            "categories": categories,
            "basis": "symbol_convention",
            "confidence": 0.0,
            "trace": trace,
        }

    categories = {
        cat: _combine([_BASE_MATRIX[p][cat] for p in parts]) for cat in USE_CATEGORIES
    }
    trace.append({"step": "symbol_lookup", "symbol": symbol, "components": parts})

    basis = "symbol_convention"
    for provision in provisions or []:
        forbid = bool(_FORBID_RE.search(provision))
        permit = bool(_PERMIT_RE.search(provision))
        if not (forbid or permit):
            continue
        for cat, keyword in _CATEGORY_KEYWORDS.items():
            if not keyword.search(provision):
                continue
            before = categories[cat]
            if forbid:
                categories[cat] = _FORBIDDEN
            elif permit and _RANK[before] < _RANK[_CONDITIONAL]:
                categories[cat] = _CONDITIONAL
            if categories[cat] != before:
                basis = "symbol_convention+provisions"
                trace.append(
                    {
                        "step": "provision_refinement",
                        "category": cat,
                        "before": before,
                        "after": categories[cat],
                        "citation": provision[:200],
                    }
                )
    return {
        "symbol": symbol,
        "categories": categories,
        "basis": basis,
        "confidence": 0.7 if basis == "symbol_convention" else 0.8,
        "trace": trace,
    }
