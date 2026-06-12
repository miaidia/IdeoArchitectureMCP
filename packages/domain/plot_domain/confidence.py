"""Composite confidence model (base_assumptions §25.1; Phase 16 / v1 Phase 13.1.2).

``confidence_components(...)`` turns the §25.1 component vector into a single,
calibrated :class:`CompositeConfidence` with the spec's policy bands:

* ``>= 0.85``    — *high*: wysokie zaufanie, ale nadal z evidence;
* ``0.60-0.84``  — *moderate*: umiarkowane zaufanie, pokazać zastrzeżenia;
* ``0.35-0.59``  — *low*: niskie zaufanie, wymaga potwierdzenia (manual review);
* ``< 0.35``     — *hint*: traktować jako hint, nie jako podstawę decyzji.

Aggregation (documented choice — §25.1 prescribes the components and the policy
thresholds but not the formula):

* **weighted geometric mean** over the PROVIDED components — a weak component
  drags the product down sharply (chain-strength intuition, consistent with the
  Phase 7 envelope formula's min-blend), while equally strong components keep
  their level;
* **weak-link cap**: the composite may never exceed the weakest provided
  component by more than :data:`WEAK_LINK_HEADROOM` — one very weak link keeps
  the composite out of the high band no matter how many strong components
  surround it (anti-pattern §20.10: calibration must not inflate confidence);
* components that are ``None`` are *not measured / not applicable* and simply
  do not participate (a parser score is meaningless where no parser ran), BUT
  an EMPTY component vector is itself an unknown: the composite collapses to
  :data:`NO_COMPONENT_CONFIDENCE` (hint band) — unknown stays unknown, it is
  never defaulted to a comfortable mid value.

Weights (documented heuristic, not law): the decision-critical components —
authority of the source, semantic unambiguity and ruleset certainty — weigh
double; the supporting components (freshness, agreement, manual confirmation)
weigh single; geometry/parser quality sit in between. ``manual_verification``
can only CONFIRM (raise) a result when provided; its absence costs nothing.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# §25.1 policy thresholds (the spec's example policy, adopted verbatim).
# --------------------------------------------------------------------------- #
THRESHOLD_HIGH = 0.85
THRESHOLD_MODERATE = 0.60
THRESHOLD_LOW = 0.35

#: Composite when NO component is measurable (unknown stays unknown — hint band).
NO_COMPONENT_CONFIDENCE = 0.2
#: The composite may exceed the weakest provided component by at most this much
#: (calibrated against the manual-review set: a source conflict / dead link must
#: drag the composite out of the comfortable bands, §20.10).
WEAK_LINK_HEADROOM = 0.25
#: Floor for the geometric mean's logarithm (a 0.0 component is a dead link).
_EPS = 0.01

ConfidenceBand = Literal["high", "moderate", "low", "hint"]

#: §25.1 component names, in spec order.
COMPONENT_NAMES: tuple[str, ...] = (
    "source_authority",
    "source_freshness",
    "geometry_precision",
    "semantic_precision",
    "parser_confidence",
    "cross_source_agreement",
    "ruleset_certainty",
    "manual_verification",
)

#: Aggregation weights (documented heuristic — see module docstring).
COMPONENT_WEIGHTS: dict[str, float] = {
    "source_authority": 2.0,
    "source_freshness": 1.0,
    "geometry_precision": 1.5,
    "semantic_precision": 2.0,
    "parser_confidence": 1.5,
    "cross_source_agreement": 1.0,
    "ruleset_certainty": 2.0,
    "manual_verification": 1.0,
}


def band_for(value: float) -> ConfidenceBand:
    """Map a confidence value to its §25.1 policy band."""
    if value >= THRESHOLD_HIGH:
        return "high"
    if value >= THRESHOLD_MODERATE:
        return "moderate"
    if value >= THRESHOLD_LOW:
        return "low"
    return "hint"


class CompositeConfidence(BaseModel):
    """The §25.1 composite: value + band + the audited component breakdown."""

    model_config = ConfigDict(extra="forbid")

    value: float = Field(ge=0.0, le=1.0, description="Composite confidence, 0.0-1.0.")
    band: ConfidenceBand = Field(description="§25.1 policy band for the value.")
    components: dict[str, float] = Field(
        default_factory=dict,
        description="The PROVIDED §25.1 components that produced the value.",
    )
    missing_components: list[str] = Field(
        default_factory=list,
        description="§25.1 components that were not measured / not applicable.",
    )
    weak_link: str | None = Field(
        default=None, description="Name of the weakest provided component."
    )
    requires_manual_review: bool = Field(
        description="§25.1: value < 0.60 → niskie zaufanie, wymaga potwierdzenia."
    )
    decision_grade: bool = Field(
        description="§25.1: value >= 0.35 — below it the result is a hint, "
        "never a basis for decisions."
    )
    basis: str = Field(
        default="weighted_geometric_mean+weak_link_cap (§25.1 policy thresholds)",
        description="Documented aggregation method (auditability).",
    )

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def confidence_components(
    *,
    source_authority: float | None = None,
    source_freshness: float | None = None,
    geometry_precision: float | None = None,
    semantic_precision: float | None = None,
    parser_confidence: float | None = None,
    cross_source_agreement: float | None = None,
    ruleset_certainty: float | None = None,
    manual_verification: float | None = None,
) -> CompositeConfidence:
    """Compose the §25.1 components into a :class:`CompositeConfidence`.

    Every argument is a 0.0-1.0 score or ``None`` (= not measured / not
    applicable — it does not participate; see module docstring for why this is
    NOT treated as 1.0 and why an all-``None`` vector collapses to the hint
    band instead of a comfortable default).
    """
    raw = {
        "source_authority": source_authority,
        "source_freshness": source_freshness,
        "geometry_precision": geometry_precision,
        "semantic_precision": semantic_precision,
        "parser_confidence": parser_confidence,
        "cross_source_agreement": cross_source_agreement,
        "ruleset_certainty": ruleset_certainty,
        "manual_verification": manual_verification,
    }
    provided: dict[str, float] = {}
    for name, value in raw.items():
        if value is None:
            continue
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"component {name!r} must be in [0, 1], got {value!r}")
        provided[name] = float(value)
    missing = [name for name in COMPONENT_NAMES if name not in provided]

    if not provided:
        value = NO_COMPONENT_CONFIDENCE
        return CompositeConfidence(
            value=value,
            band=band_for(value),
            components={},
            missing_components=missing,
            weak_link=None,
            requires_manual_review=True,
            decision_grade=value >= THRESHOLD_LOW,
        )

    # Weighted geometric mean over the provided components.
    total_weight = sum(COMPONENT_WEIGHTS[name] for name in provided)
    log_sum = sum(
        COMPONENT_WEIGHTS[name] * math.log(max(score, _EPS))
        for name, score in provided.items()
    )
    value = math.exp(log_sum / total_weight)

    # Weak-link cap: one very weak component keeps the composite honest.
    weak_name, weak_score = min(provided.items(), key=lambda kv: kv[1])
    value = min(value, weak_score + WEAK_LINK_HEADROOM)
    value = round(max(0.0, min(1.0, value)), 4)

    return CompositeConfidence(
        value=value,
        band=band_for(value),
        components=dict(provided),
        missing_components=missing,
        weak_link=weak_name,
        requires_manual_review=value < THRESHOLD_MODERATE,
        decision_grade=value >= THRESHOLD_LOW,
    )
