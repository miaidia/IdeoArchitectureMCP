"""Source-conflict detection: GML zone values vs parsed-document values (§25.2, F-0125).

When the GML carrier and the parsed text disagree about the same indicator, the
conflict is REPORTED — both values, both sources, severity — and the result is
``manual_review_required``. A conflict is never silently resolved by picking one
value (§25.2 rules 1–4); the record proposes who can resolve it (rule 5).
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Result status when any decision-relevant conflict exists (§25.2 rule 4).
MANUAL_REVIEW_REQUIRED = "manual_review_required"

#: Indicators whose disagreement is decision-critical (severity high).
_HIGH_SEVERITY = {"max_height_m", "max_intensity", "max_coverage_ratio", "min_pbc_ratio"}

#: Numeric tolerance — values closer than this are the same indicator value.
_NUMERIC_TOLERANCE = 1e-9


class IndicatorValue(BaseModel):
    """One indicator value with its provenance (for cross-source comparison)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Canonical indicator name (§8.1.3 set).")
    value: Any = Field(description="The value from this source.")
    source_id: str = Field(description="Source record / document id.")
    source_kind: str = Field(description="gml | document | drawing | user (§25.3).")
    detail: str | None = Field(default=None, description="e.g. zone symbol / fragment ref.")


class Conflict(BaseModel):
    """A reported source conflict (§25.2: both values, both sources, severity)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Conflict id.")
    indicator: str = Field(description="Indicator name in conflict.")
    value_a: Any = Field(description="Value from source A.")
    source_a: str = Field(description="Source A id (kind: see detail).")
    value_b: Any = Field(description="Value from source B.")
    source_b: str = Field(description="Source B id.")
    severity: str = Field(description="high | medium (decision relevance).")
    status: str = Field(
        default=MANUAL_REVIEW_REQUIRED,
        description="Always manual_review_required — conflicts are never auto-resolved (§25.2).",
    )
    resolution_hint: str = Field(
        description="Document/organ that can resolve the conflict (§25.2 rule 5)."
    )
    detail: dict[str, Any] = Field(
        default_factory=dict, description="Source kinds + extra context for the report."
    )


def _values_differ(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) > _NUMERIC_TOLERANCE
    return a != b


def detect_conflicts(
    values_a: list[IndicatorValue], values_b: list[IndicatorValue]
) -> list[Conflict]:
    """Compare same-name indicators across two sources → :class:`Conflict` records.

    Indicators present in only one source are NOT conflicts (the other source is
    simply silent); only a same-name disagreement is reported. The caller marks
    the surrounding result ``manual_review_required`` when any conflict exists
    (use :data:`MANUAL_REVIEW_REQUIRED`).
    """
    by_name_b = {v.name: v for v in values_b}
    conflicts: list[Conflict] = []
    for va in values_a:
        vb = by_name_b.get(va.name)
        if vb is None or not _values_differ(va.value, vb.value):
            continue
        conflicts.append(
            Conflict(
                id=f"conflict:{uuid.uuid4().hex[:8]}",
                indicator=va.name,
                value_a=va.value,
                source_a=va.source_id,
                value_b=vb.value,
                source_b=vb.source_id,
                severity="high" if va.name in _HIGH_SEVERITY else "medium",
                resolution_hint=(
                    "Porównać z oryginałem uchwały w dzienniku urzędowym/BIP; "
                    "rozstrzyga gmina (organ sporządzający plan) — tekst uchwały "
                    "jest aktem prawa miejscowego, GML jest jego nośnikiem danych."
                ),
                detail={
                    "source_kind_a": va.source_kind,
                    "source_kind_b": vb.source_kind,
                    "detail_a": va.detail,
                    "detail_b": vb.detail,
                },
            )
        )
    return conflicts
