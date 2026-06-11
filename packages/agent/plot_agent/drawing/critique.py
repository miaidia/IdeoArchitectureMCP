"""Structured critique that feeds the next proposal (Phase 4 §4.1.B.4; Phase 11 §11.1.3).

``critique`` turns a scored proposal into a machine-readable :class:`StructuredCritique`:
which hard constraint was violated, which score component is weakest, and a concrete
suggestion for the next proposal. This is the iterate signal of the drawing loop — it is
structured DATA (not free text steering the agent, NFR-SEC-003).

Phase 11 (masterplan loop v2) adds :func:`critique_masterplan`, the architect-grade
critique of a :class:`MasterplanProposal` iteration:

* **rule findings** — every failing/warning Phase 10 :class:`plot_rules.RuleCheck`
  cited by ``rule_id`` + evaluation SUBJECT (building name / sorted ``pair:A|B`` /
  ``parking:N`` from the geometry evidence) + the binding required-vs-actual values
  read from the engine trace;
* **capacity gap** — measured PUM vs the ``capacity_generate_scenarios`` BASE-scenario
  target ("PUM X z ~Y m² osiągalnych"); a shortfall is ALWAYS stated, never hidden
  (anti-pattern guard, plan §11.4), and an unknown target is reported as unknown;
* **staging warnings** — the soft Phase 11 :class:`plot_planning.StageCheck` outcomes
  that are not PASS;
* **positive reinforcement** — score components that IMPROVED vs the previous
  iteration (the loop passes ``previous_components``).

The model's design-reasoning note (the loop's per-iteration free-text record) is
deliberately NOT an input here: it is documentation that flows audit → report only
and may never steer validation or critique (NFR-SEC-003, plan §11.1.6) — a grep
for that field name over this module proves the isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from plot_agent.drawing.proposal import LayoutProposal, MasterplanProposal
from plot_agent.drawing.score import ProposalScore

#: Components below the previous value by more than this are not "improved" noise.
_IMPROVEMENT_EPS = 1e-4
#: Capacity acceptance band around the base-scenario target (plan §11.3: ±10%).
CAPACITY_TARGET_TOLERANCE = 0.10


@dataclass
class StructuredCritique:
    """Critique of one scored proposal (§4.1.B.4; masterplan fields Phase 11 §11.1.3)."""

    valid: bool
    violated_constraints: list[str] = field(default_factory=list)
    weakest_component: str | None = None
    weakest_value: float | None = None
    suggestions: list[str] = field(default_factory=list)
    # Phase 11 masterplan critique (empty/None on the v1 LayoutProposal path).
    rule_findings: list[dict[str, Any]] = field(default_factory=list)
    capacity: dict[str, Any] | None = None
    staging_warnings: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "violated_constraints": self.violated_constraints,
            "weakest_component": self.weakest_component,
            "weakest_value": self.weakest_value,
            "suggestions": self.suggestions,
            "rule_findings": self.rule_findings,
            "capacity": self.capacity,
            "staging_warnings": self.staging_warnings,
            "improvements": self.improvements,
        }


# Component → concrete suggestion for the next proposal (§4.1.B.4).
_COMPONENT_SUGGESTIONS: dict[str, str] = {
    "coverage_component": "Reduce footprint to bring coverage under the ruleset maximum.",
    "envelope_utilization": "Enlarge or reposition the footprint to use more of the buildable envelope.",
    "pbc_component": "Add greenery_polygons to raise the biologically-active area.",
    "parking_component": "Increase parking_count to meet the estimated demand.",
}

# Rule-family → next-step suggestion (machine-mapped from the rule id prefix; the
# legal VALUES stay in the cited RuleCheck trace, never restated here).
_RULE_FAMILY_SUGGESTIONS: list[tuple[str, str]] = [
    ("PL-WT-12", "Odsuń wskazane ściany od granicy działki na odległość wymaganą w cytowanej regule (WT §12)."),
    ("PL-WT-13", "Rozsuń wskazane budynki lub obniż obiekt przesłaniający — kąt 60° od osi okna (WT §13)."),
    ("PL-WT-19", "Przesuń parking naziemny dalej od okien/placu zabaw/granicy albo zmniejsz liczbę stanowisk (WT §19)."),
    ("PL-WT-21", "Powiększ wielobok parkingu albo zmniejsz deklarowaną liczbę stanowisk (heurystyka ~25 m²/stanowisko)."),
    ("PL-WT-39", "Zwiększ powierzchnię biologicznie czynną (zieleń, kredyt placu zabaw) do wymaganego udziału (WT §39)."),
    ("PL-WT-40", "Powiększ/przesuń plac zabaw zgodnie z warunkami §40 (powierzchnia, odległości, nasłonecznienie, udział PBC)."),
    ("PL-WT-60", "Przeprojektuj wskazany segment tak, by mieszkania miały okno spełniające wymóg nasłonecznienia (WT §60)."),
    ("PL-PPOZ-271", "Zwiększ odległość między wskazanymi budynkami do wymaganej separacji ppoż (§271–273)."),
    ("PL-PPOZ-DROGA", "Poprowadź drogę pożarową (lub kdw ≥4 m) wzdłuż dłuższego boku budynku, krawędzią 5–15 m od ściany."),
]


def critique(
    proposal: LayoutProposal | MasterplanProposal, score: ProposalScore
) -> StructuredCritique:
    """Produce a structured critique guiding the next proposal (§4.1.B.4; v2 Phase 9)."""
    if score.violations:
        violated = [v.kind for v in score.violations]
        suggestions: list[str] = []
        if "outside_envelope" in violated:
            suggestions.append(
                "Move/shrink the footprint so it lies entirely inside the buildable envelope."
            )
        if "intersects_hard_constraint" in violated:
            suggestions.append(
                "Pull the footprint away from the no-build / hard-constraint zone (it may not overlap)."
            )
        if "outside_parcel" in violated:
            suggestions.append("Keep every building footprint inside the parcel boundary.")
        if "inter_building_rule" in violated:
            suggestions.append(
                "Resolve the failed inter-building rule (WT/ppoż) cited in the violations."
            )
        if "empty_footprint" in violated:
            suggestions.append("Provide a non-empty footprint (GeoJSON polygon or draw-DSL rectangles).")
        return StructuredCritique(
            valid=False,
            violated_constraints=violated,
            suggestions=suggestions or ["Resolve the hard violation before optimising scores."],
        )

    # Valid: find the weakest scoring component to improve next.
    candidates = {
        k: v
        for k, v in score.components.items()
        if k in _COMPONENT_SUGGESTIONS
    }
    weakest_component: str | None = None
    weakest_value: float | None = None
    if candidates:
        weakest_component = min(candidates, key=lambda k: candidates[k])
        weakest_value = candidates[weakest_component]
    suggestions = []
    if weakest_component is not None:
        suggestions.append(_COMPONENT_SUGGESTIONS[weakest_component])
    return StructuredCritique(
        valid=True,
        violated_constraints=[],
        weakest_component=weakest_component,
        weakest_value=weakest_value,
        suggestions=suggestions,
    )


# --------------------------------------------------------------------------- #
# Phase 11 §11.1.3 — masterplan critique helpers
# --------------------------------------------------------------------------- #
def _check_attr(check: Any, name: str, default: Any = None) -> Any:
    """Read a field from a RuleCheck object OR its ``model_dump`` dict (duck-typed)."""
    if isinstance(check, dict):
        return check.get(name, default)
    return getattr(check, name, default)


def _check_status(check: Any) -> str:
    """Plain status string of a RuleCheck-like ('fail'/'warning'/...)."""
    status = _check_attr(check, "status", "")
    return str(getattr(status, "value", status))


def _check_subject(check: Any) -> str | None:
    """The evaluation subject cited by a check (plan §11.1.3: WHICH building/pair).

    Mirrors the Phase 10 override-subject vocabulary: ``pair:A|B`` (names sorted)
    for pairwise §271 evidence, the ``building`` name for per-building rules,
    ``parking:N`` for §19 — all read from the validator's ``geometry_evidence``.
    """
    evidence = _check_attr(check, "geometry_evidence") or {}
    if not isinstance(evidence, dict):
        return None
    pair = evidence.get("pair")
    if isinstance(pair, list | tuple) and pair:
        return "pair:" + "|".join(sorted(str(p) for p in pair))
    building = evidence.get("building")
    if building:
        return str(building)
    parking_index = evidence.get("parking_index")
    if parking_index is not None:
        return f"parking:{parking_index}"
    return None


def _binding_entry(check: Any) -> dict[str, Any] | None:
    """The binding pass/fail comparison entry of a check's engine trace.

    Same selection rule as ``plot_planning.wt_validators.config.decided_entry``
    (failing entry first, else smallest pass margin) — duplicated over the
    dict/object duck-typed surface so dumped JSON checks critique identically.
    """
    trace = _check_attr(check, "trace") or {}
    entries = [
        e
        for e in trace.get("checks", [])
        if isinstance(e, dict)
        and e.get("status") in ("pass", "fail")
        and "target_value" in e
        and "input_value" in e
    ]
    if not entries:
        return None
    fails = [e for e in entries if e["status"] == "fail"]
    pool = fails or entries
    return min(pool, key=lambda e: float(e["input_value"]) - float(e["target_value"]))


def _rule_findings(checks: list[Any]) -> list[dict[str, Any]]:
    """Failing/warning RuleChecks → sorted, citable findings (plan §11.1.3)."""
    findings: list[dict[str, Any]] = []
    for check in checks:
        status = _check_status(check)
        if status not in ("fail", "warning"):
            continue
        entry = _binding_entry(check)
        findings.append(
            {
                "rule_id": str(_check_attr(check, "rule_id", "")),
                "status": status,
                "severity": str(_check_attr(check, "severity", "")),
                "subject": _check_subject(check),
                "required_value": entry.get("target_value") if entry else None,
                "actual_value": entry.get("input_value") if entry else None,
                "message": str(_check_attr(check, "message", "")),
            }
        )
    findings.sort(key=lambda f: (f["rule_id"], str(f["subject"]), f["message"]))
    return findings


def _capacity_block(
    metrics: Any, pum_target_m2: float | None
) -> dict[str, Any]:
    """Measured PUM vs the base-scenario target — the gap is ALWAYS stated (§11.4)."""
    totals = metrics.totals if not isinstance(metrics, dict) else metrics.get("totals", {})
    pum = float(totals.get("pum_m2", 0.0))
    block: dict[str, Any] = {"pum_m2": round(pum, 2)}
    if pum_target_m2 is None or pum_target_m2 <= 0:
        block["target_pum_m2"] = "unknown"
        block["within_target_tolerance"] = "unknown"
        block["message"] = (
            f"PUM {pum:,.0f} m² — cel chłonności nieznany (brak scenariusza bazowego: "
            "uzupełnij wskaźniki MPZP/WZ albo policz capacity_generate_scenarios)."
        )
        return block
    gap = pum_target_m2 - pum
    gap_ratio = gap / pum_target_m2
    within = abs(gap_ratio) <= CAPACITY_TARGET_TOLERANCE
    block.update(
        {
            "target_pum_m2": round(pum_target_m2, 2),
            "target_basis": "capacity_generate_scenarios (scenariusz bazowy)",
            "gap_m2": round(gap, 2),
            "gap_ratio": round(gap_ratio, 4),
            "within_target_tolerance": within,
            "tolerance": CAPACITY_TARGET_TOLERANCE,
        }
    )
    if gap > 0 and not within:
        block["message"] = (
            f"PUM {pum:,.0f} m² z ~{pum_target_m2:,.0f} m² osiągalnych (scenariusz "
            f"bazowy) — luka {gap:,.0f} m² ({gap_ratio:.0%}); rozważ zwiększenie "
            "liczby kondygnacji lub dodanie skrzydła wzdłuż wolnej pierzei."
        )
    elif gap > 0:
        block["message"] = (
            f"PUM {pum:,.0f} m² z ~{pum_target_m2:,.0f} m² osiągalnych (scenariusz "
            f"bazowy) — w granicach tolerancji ±{CAPACITY_TARGET_TOLERANCE:.0%}."
        )
    else:
        block["message"] = (
            f"PUM {pum:,.0f} m² przekracza cel bazowy ~{pum_target_m2:,.0f} m² o "
            f"{-gap:,.0f} m² — zweryfikuj zgodność ze wskaźnikami MPZP."
        )
    return block


#: Non-"_component" keys that still count as quality signals for reinforcement.
_IMPROVEMENT_EXTRA_KEYS = ("envelope_utilization", "pum_m2")


def _improvements(
    components: dict[str, float], previous: dict[str, float] | None
) -> list[str]:
    """Positive reinforcement: components improved vs the previous iteration."""
    if not previous:
        return []
    improved: list[str] = []
    for key in sorted(components):
        if not (key.endswith("_component") or key in _IMPROVEMENT_EXTRA_KEYS):
            continue
        before = previous.get(key)
        after = components[key]
        if isinstance(before, int | float) and after > float(before) + _IMPROVEMENT_EPS:
            improved.append(f"poprawa: {key} {float(before):g} → {after:g}")
    return improved


def critique_masterplan(
    proposal: MasterplanProposal,
    score: ProposalScore,
    *,
    metrics: Any,
    checks: list[Any] | None = None,
    staging_checks: list[Any] | None = None,
    pum_target_m2: float | None = None,
    previous_components: dict[str, float] | None = None,
) -> StructuredCritique:
    """Architect-grade structured critique of a masterplan iteration (plan §11.1.3).

    ``metrics`` is the Phase 9 ``MasterplanMetrics``; ``checks`` the Phase 10
    RuleChecks (objects or dumped dicts); ``staging_checks`` the Phase 11
    StageChecks; ``pum_target_m2`` the base-scenario PUM target. The critique is
    pure DATA derived from validated geometry + rule traces — no free text from
    the model enters it (NFR-SEC-003).
    """
    base = critique(proposal, score)
    findings = _rule_findings(list(checks or []))

    staging_warnings: list[str] = []
    for stage_check in staging_checks or []:
        status = _check_status(stage_check)
        if status not in ("pass", "not_applicable"):
            staging_warnings.append(str(_check_attr(stage_check, "message", "")))
    staging_warnings.sort()

    capacity = _capacity_block(metrics, pum_target_m2)

    suggestions = list(base.suggestions)
    seen_families: set[str] = set()
    for finding in findings:
        if finding["status"] != "fail":
            continue
        for prefix, suggestion in _RULE_FAMILY_SUGGESTIONS:
            if finding["rule_id"].startswith(prefix) and prefix not in seen_families:
                seen_families.add(prefix)
                suggestions.append(suggestion)
    if isinstance(capacity.get("within_target_tolerance"), bool) and not capacity[
        "within_target_tolerance"
    ]:
        suggestions.append(capacity["message"])
    if staging_warnings:
        suggestions.append(
            "Domknij etapowanie: każdy etap musi być samodzielnie obsługiwalny "
            "(dojazd, bilans postojowy, plac zabaw) — patrz staging_warnings."
        )

    return StructuredCritique(
        valid=base.valid,
        violated_constraints=base.violated_constraints,
        weakest_component=base.weakest_component,
        weakest_value=base.weakest_value,
        suggestions=suggestions,
        rule_findings=findings,
        capacity=capacity,
        staging_warnings=staging_warnings,
        improvements=_improvements(score.components, previous_components),
    )
