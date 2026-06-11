"""Typology knowledge — design-practice SUGGESTIONS for the design brief (Phase 11 §11.1.2).

Reads the ``rulesets/PL/typologies/*.yaml`` documents (category ``typologies``,
``basis: design_practice`` — NO legal force) and ranks them for a concrete parcel:
:func:`recommend_typologies` returns scored :class:`TypologyRecommendation`\\ s, each
with a human ``why`` trail naming the matched conditions (e.g. "kwartał obrzeżny:
szerokość działki >60 m, śródmiejska pierzeja od południa").

ANTI-PATTERN GUARD (plan §11.4): typology heuristics are *suggestions in the brief*,
never validators — this module never imports :func:`plot_rules.evaluate`, never
produces a ``RuleCheck``, and nothing here may feed ``validate_hard`` /
``score_masterplan`` hard violations. All numeric knowledge (widths, traits, bonuses)
lives in the YAML documents — editing a YAML flips a recommendation without touching
code (data-driven test, plan Phase 11 part A).

Condition semantics mirror the rules-engine ``applies_when`` grammar (scalar equality,
list membership, ``{min/max/gt/lt}`` numeric brackets) so YAML authors use ONE
vocabulary across rulesets — re-implemented locally because the engine's evaluator is
private and typologies must stay decoupled from rule evaluation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plot_rules import RulesetRegistry
from pydantic import BaseModel, ConfigDict, Field

#: Loader category derived from the ``rulesets/PL/typologies/`` path segment.
TYPOLOGY_CATEGORY = "typologies"

#: Basis marker every recommendation carries (§0v2.4: heuristics are labelled, not law).
TYPOLOGY_BASIS = "design_practice"


class TypologyRecommendation(BaseModel):
    """One ranked typology suggestion for the design brief (NEVER a validator)."""

    model_config = ConfigDict(extra="forbid")

    typology_id: str = Field(description="Id of the typology YAML document.")
    title: str = Field(description="Typology title from the YAML.")
    score: float = Field(description="Preference score (base + matched bonuses).")
    why: list[str] = Field(
        default_factory=list,
        description="Human reasons: summary + matched bonus explanations.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Geometric design-practice parameters (trakt, sekcja, floors…).",
    )
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    basis: str = Field(
        default=TYPOLOGY_BASIS,
        description="Always design_practice — a suggestion, not a legal value.",
    )
    source: str = Field(default="", description="Path of the YAML document.")


# --------------------------------------------------------------------------- #
# Condition evaluation (same grammar as the rules-engine `applies_when`)
# --------------------------------------------------------------------------- #
def _condition_met(expected: Any, value: Any) -> bool | None:
    """One condition: equality / membership / numeric bracket. ``None`` = unknown."""
    if value is None:
        return None
    if isinstance(expected, dict):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if "min" in expected and v < float(expected["min"]):
            return False
        if "max" in expected and v > float(expected["max"]):
            return False
        if "gt" in expected and v <= float(expected["gt"]):
            return False
        if "lt" in expected and v >= float(expected["lt"]):
            return False
        return True
    if isinstance(expected, list):
        return value in expected
    return bool(value == expected)


def _conditions(cond: Mapping[str, Any], features: Mapping[str, Any]) -> bool | None:
    """All conditions of a ``when`` map. False dominates; else None if any unknown."""
    saw_unknown = False
    for key, expected in cond.items():
        met = _condition_met(expected, features.get(key))
        if met is False:
            return False
        if met is None:
            saw_unknown = True
    return None if saw_unknown else True


# --------------------------------------------------------------------------- #
# Feature derivation
# --------------------------------------------------------------------------- #
def _features(
    shape_class: str,
    indicators: Mapping[str, Any],
    srodmiejska: bool,
    parcel_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Flat feature map the YAML conditions evaluate against.

    Documented feature keys (YAML vocabulary): ``shape_class``, ``srodmiejska``,
    ``width_m`` (median parcel width), ``area_m2``, ``frontage_m``, ``zone_symbol``
    (optional, from ``indicators['zone_symbol']``), ``uslugi_expected`` (derived: the
    MPZP carries a usługi parking indicator OR the zone symbol contains "U"),
    ``parking_indicator_present`` plus the raw numeric indicators (``max_kondygnacje``,
    ``max_height_m``, ``max_intensity``).
    """
    zone_symbol = indicators.get("zone_symbol")
    uslugi_expected = indicators.get("parking_per_100m2_uslug") is not None or (
        isinstance(zone_symbol, str) and "U" in zone_symbol.upper()
    )
    return {
        "shape_class": shape_class,
        "srodmiejska": bool(srodmiejska),
        "width_m": parcel_metrics.get("width_m"),
        "area_m2": parcel_metrics.get("area_m2"),
        "frontage_m": parcel_metrics.get("frontage_m"),
        "zone_symbol": zone_symbol,
        "uslugi_expected": uslugi_expected,
        "parking_indicator_present": indicators.get("parking_per_mieszkanie") is not None
        or indicators.get("parking_per_100m2_uslug") is not None,
        "max_kondygnacje": indicators.get("max_kondygnacje"),
        "max_height_m": indicators.get("max_height_m"),
        "max_intensity": indicators.get("max_intensity"),
    }


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def recommend_typologies(
    shape_class: str,
    indicators: Mapping[str, Any],
    srodmiejska: bool,
    parcel_metrics: Mapping[str, Any],
    *,
    registry: RulesetRegistry,
) -> list[TypologyRecommendation]:
    """Rank the loaded typology documents for a parcel (Phase 11 §11.1.2).

    ``registry`` is the freshly-loaded ruleset registry (hot-reload semantics: the
    typology YAMLs come from ``registry.by_category("typologies")`` so an edited YAML
    is reflected on the next load — the knowledge is DATA, not code).

    Ranking: every typology whose ``applicability.when`` conditions are not violated
    gets ``preference.base`` plus each matched ``preference.bonuses[]`` entry; a failed
    applicability condition EXCLUDES the typology (e.g. kwartał obrzeżny below its
    minimum plot width). Unknown features never exclude (suggestions are lenient,
    unlike legal rules) — they simply earn no bonus.
    """
    features = _features(shape_class, indicators, srodmiejska, parcel_metrics)
    out: list[TypologyRecommendation] = []
    for rule in registry.by_category(TYPOLOGY_CATEGORY):
        raw = rule.raw or {}
        applicability = (raw.get("applicability") or {}).get("when") or {}
        if _conditions(applicability, features) is False:
            continue  # excluded — a hard applicability bound failed (e.g. width)
        preference = raw.get("preference") or {}
        score = float(preference.get("base") or 0.0)
        why: list[str] = []
        summary = str(raw.get("summary") or "").strip()
        if summary:
            why.append(summary)
        for bonus in preference.get("bonuses") or []:
            when = bonus.get("when") or {}
            if _conditions(when, features) is True:
                score += float(bonus.get("add") or 0.0)
                if bonus.get("why"):
                    why.append(str(bonus["why"]))
        out.append(
            TypologyRecommendation(
                typology_id=rule.id,
                title=rule.title,
                score=round(score, 4),
                why=why,
                parameters=dict(raw.get("parameters") or {}),
                pros=[str(p) for p in raw.get("pros") or []],
                cons=[str(c) for c in raw.get("cons") or []],
                basis=str(raw.get("basis") or TYPOLOGY_BASIS),
                source=rule.path,
            )
        )
    out.sort(key=lambda r: (-r.score, r.typology_id))
    return out
