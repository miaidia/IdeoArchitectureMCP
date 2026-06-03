"""Red-flag classifier + screening decision (Phase 7 §7.1.3 / §7.19 / §14.2).

* :func:`red_flags` — turn the computed constraints + envelope + context into
  :class:`~plot_domain.RiskItem`s (severity / confidence / status), one per detected
  constraint plus envelope-derived flags (e.g. "almost no buildable area").
* :func:`decision` — collapse risks + envelope into the screening decision
  ``OK | OK_WITH_RISKS | NEEDS_MANUAL_REVIEW | LIKELY_BLOCKED`` with **HARD-BLOCKER
  DOMINANCE** (§14.2): a critical/hard blocker forces ``LIKELY_BLOCKED`` (or at least
  ``NEEDS_MANUAL_REVIEW``) regardless of how large the envelope is. Scores are NOT summed
  past a hard blocker (§7.4 anti-pattern).
* :func:`unknowns_for_unavailable` — build :class:`~plot_domain.UnknownItem`s for themes
  whose source was ``source_unavailable`` (NOT ``not_detected``) — the distinction is
  preserved (§21).
* :func:`next_actions` — recommendations addressed to the right authority (§7.19).

Confidence on a risk maps the source confidence to the qualitative §11.2 scale.
"""

from __future__ import annotations

import uuid

from plot_domain import (
    BuildableEnvelope,
    ConfidenceLevel,
    Constraint,
    Decision,
    Recommendation,
    RiskItem,
    RiskStatus,
    RiskType,
    Severity,
    UnknownItem,
)

from plot_envelope.layers import LAYER_POLICY, RiskKind
from plot_envelope.overlay import is_hard, severity_rank

# Themes whose presence is a HARD blocker for screening (§14.2 dominance).
_HARD_BLOCKER_KINDS: frozenset[str] = frozenset(
    {RiskKind.FLOOD.value, RiskKind.LANDSLIDE.value}
)

# Below this buildable fraction the parcel is effectively unbuildable → manual review.
_MIN_BUILDABLE_FRACTION = 0.05


def _confidence_level(conf: float) -> ConfidenceLevel:
    if conf >= 0.75:
        return ConfidenceLevel.HIGH
    if conf >= 0.5:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _risk_type_for(constraint: Constraint) -> RiskType:
    rt = constraint.machine_summary.get("risk_type")
    try:
        return RiskType(rt)
    except (ValueError, TypeError):
        return RiskType.DATA_QUALITY


def red_flags(
    constraints: list[Constraint],
    envelope: BuildableEnvelope | None,
    *,
    parcel_area_m2: float | None = None,
    no_road_access: bool = False,
    context: dict[str, object] | None = None,
) -> list[RiskItem]:
    """Build the risk register / red-flag list (§7.19 / §11.2).

    Every detected constraint becomes a :class:`~plot_domain.RiskItem` (``status=detected``).
    Envelope-derived flags (tiny buildable area) and an explicit ``no_road_access`` flag
    (a hard procedural blocker) are added. Each risk carries a source_id → evidence
    (NFR-AUD-001) where one exists.
    """
    risks: list[RiskItem] = []
    for con in constraints:
        hard = is_hard(con)
        risks.append(
            RiskItem(
                id=f"risk:{uuid.uuid4().hex[:8]}",
                risk_type=_risk_type_for(con),
                severity=con.severity,
                confidence=_confidence_level(con.confidence),
                status=RiskStatus.DETECTED,
                summary=con.human_summary,
                mitigation=(
                    "Wymaga potwierdzenia/decyzji przed projektowaniem (twarde ograniczenie)."
                    if hard
                    else "Uwzględnić w projekcie (miękkie ograniczenie / strefa techniczna)."
                ),
                source_id=con.source_id,
            )
        )

    # Envelope-derived flag: almost nothing left to build on.
    if envelope is not None and parcel_area_m2 and parcel_area_m2 > 0.0:
        frac = (envelope.area_m2 or 0.0) / parcel_area_m2
        if frac < _MIN_BUILDABLE_FRACTION:
            risks.append(
                RiskItem(
                    id=f"risk:{uuid.uuid4().hex[:8]}",
                    risk_type=RiskType.TECHNICAL_BUILDING_RULES,
                    severity=Severity.HIGH,
                    confidence=_confidence_level(envelope.confidence),
                    status=RiskStatus.DETECTED,
                    summary=(
                        f"Po odsunięciach i strefach wyłączonych pozostaje tylko "
                        f"{frac * 100:.1f}% działki jako obszar zabudowy."
                    ),
                    mitigation="Zweryfikować linie zabudowy i możliwość zmniejszych odsunięć.",
                    source_id=None,
                )
            )

    # Explicit no-road-access hard procedural blocker (§7.19 "co jest największym blockerem").
    if no_road_access:
        risks.append(
            RiskItem(
                id=f"risk:{uuid.uuid4().hex[:8]}",
                risk_type=RiskType.ROAD_ACCESS,
                severity=Severity.CRITICAL,
                confidence=ConfidenceLevel.MEDIUM,
                status=RiskStatus.DETECTED,
                summary="Brak dostępu do drogi publicznej — działka może być niezabudowywalna.",
                mitigation="Ustalić służebność dojazdu lub dostęp do drogi publicznej.",
                source_id=None,
            )
        )

    return risks


def _is_hard_blocker(risk: RiskItem) -> bool:
    """A risk that DOMINATES the decision (§14.2): critical severity, or a hard theme."""
    if risk.status not in (RiskStatus.DETECTED, RiskStatus.SUSPECTED):
        return False
    if risk.severity is Severity.CRITICAL:
        return True
    # Flood / landslide detected with HIGH severity is a hard blocker theme.
    if risk.risk_type in (RiskType.FLOOD, RiskType.GEOLOGY) and risk.severity is Severity.HIGH:
        return True
    if risk.risk_type is RiskType.ROAD_ACCESS and risk.severity is Severity.CRITICAL:
        return True
    return False


def decision(
    risks: list[RiskItem],
    envelope: BuildableEnvelope | None,
    *,
    parcel_area_m2: float | None = None,
    has_manual_review: bool = False,
) -> Decision:
    """Collapse risks + envelope into the screening decision with hard-blocker dominance.

    Rules (§14.2 / §7.4 — never sum past a hard blocker):

    * a HARD blocker (critical risk, detected flood/landslide hazard) ⇒ ``LIKELY_BLOCKED``
      — regardless of an otherwise-large envelope;
    * else a manual-review item or near-zero buildable area ⇒ ``NEEDS_MANUAL_REVIEW``;
    * else any HIGH/MEDIUM risk ⇒ ``OK_WITH_RISKS``;
    * else ⇒ ``OK``.
    """
    detected = [r for r in risks if r.status in (RiskStatus.DETECTED, RiskStatus.SUSPECTED)]

    # 1) HARD BLOCKER DOMINANCE — short-circuits everything (§14.2).
    if any(_is_hard_blocker(r) for r in detected):
        return Decision.LIKELY_BLOCKED

    # 2) Manual-review item present, or near-zero buildable area.
    tiny_envelope = False
    if envelope is not None and parcel_area_m2 and parcel_area_m2 > 0.0:
        frac = (envelope.area_m2 or 0.0) / parcel_area_m2
        tiny_envelope = frac < _MIN_BUILDABLE_FRACTION
    if has_manual_review or any(r.status is RiskStatus.MANUAL_REVIEW_REQUIRED for r in risks) or tiny_envelope:
        return Decision.NEEDS_MANUAL_REVIEW

    # 3) Any non-trivial risk ⇒ OK_WITH_RISKS.
    if any(severity_rank(r.severity) >= severity_rank(Severity.MEDIUM) for r in detected):
        return Decision.OK_WITH_RISKS
    if detected:
        return Decision.OK_WITH_RISKS

    # 4) Clean.
    return Decision.OK


def unknowns_for_unavailable(unavailable_kinds: list[RiskKind]) -> list[UnknownItem]:
    """Build :class:`~plot_domain.UnknownItem`s for source-unavailable themes (§21).

    These are NOT ``not_detected`` — the source failed, so the theme's presence is
    genuinely unknown and must persist regardless of score (§20.10 / NFR-REL-001).
    """
    unknowns: list[UnknownItem] = []
    for kind in unavailable_kinds:
        policy = LAYER_POLICY[kind]
        # A hard theme being unknown is more severe than a soft one being unknown.
        sev = Severity.HIGH if policy.hard else Severity.MEDIUM
        unknowns.append(
            UnknownItem(
                id=f"unk:{kind.value}:{uuid.uuid4().hex[:8]}",
                topic=f"{policy.label} (warstwa: {kind.value})",
                severity=sev,
                reason="source_unavailable",
                suggested_action=(
                    f"Ponowić pobranie warstwy '{kind.value}' lub potwierdzić ją w urzędzie; "
                    "brak danych NIE oznacza braku ograniczenia."
                ),
            )
        )
    return unknowns


def next_actions(
    risks: list[RiskItem],
    unknowns: list[UnknownItem],
    decision_value: Decision,
) -> list[Recommendation]:
    """Recommend next best actions based on the screening outcome (§7.19)."""
    actions: list[Recommendation] = []

    if decision_value in (Decision.LIKELY_BLOCKED, Decision.NEEDS_MANUAL_REVIEW):
        actions.append(
            Recommendation(
                id=f"rec:{uuid.uuid4().hex[:8]}",
                title="Potwierdzić największy blocker przed zakupem",
                detail=(
                    "Zweryfikować dominujące ryzyko (np. strefa powodziowa / osuwisko / brak "
                    "dojazdu) u właściwego organu przed podjęciem decyzji inwestycyjnej."
                ),
                priority=Severity.HIGH,
                addressed_to="urząd gminy / starostwo / Wody Polskie / PIG",
            )
        )

    for unk in unknowns:
        actions.append(
            Recommendation(
                id=f"rec:{uuid.uuid4().hex[:8]}",
                title=f"Uzupełnić dane: {unk.topic}",
                detail=unk.suggested_action or "Pobrać brakującą warstwę / potwierdzić w urzędzie.",
                priority=unk.severity if severity_rank(unk.severity) >= severity_rank(Severity.MEDIUM) else Severity.MEDIUM,
                addressed_to="operator źródła danych / urząd",
            )
        )

    if not actions:
        actions.append(
            Recommendation(
                id=f"rec:{uuid.uuid4().hex[:8]}",
                title="Przejść do pełnej analizy due diligence",
                detail="Brak twardych blokerów na poziomie screeningu — zlecić pełną analizę.",
                priority=Severity.LOW,
                addressed_to="zespół projektowy",
            )
        )
    return actions
