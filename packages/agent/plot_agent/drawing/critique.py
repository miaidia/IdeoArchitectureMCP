"""Structured critique that feeds the next proposal (Phase 4 §4.1.B.4).

``critique`` turns a scored proposal into a machine-readable :class:`StructuredCritique`:
which hard constraint was violated, which score component is weakest, and a concrete
suggestion for the next proposal. This is the iterate signal of the drawing loop — it is
structured DATA (not free text steering the agent, NFR-SEC-003).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from plot_agent.drawing.proposal import LayoutProposal
from plot_agent.drawing.score import ProposalScore


@dataclass
class StructuredCritique:
    """Critique of one scored proposal (§4.1.B.4)."""

    valid: bool
    violated_constraints: list[str] = field(default_factory=list)
    weakest_component: str | None = None
    weakest_value: float | None = None
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "violated_constraints": self.violated_constraints,
            "weakest_component": self.weakest_component,
            "weakest_value": self.weakest_value,
            "suggestions": self.suggestions,
        }


# Component → concrete suggestion for the next proposal (§4.1.B.4).
_COMPONENT_SUGGESTIONS: dict[str, str] = {
    "coverage_component": "Reduce footprint to bring coverage under the ruleset maximum.",
    "envelope_utilization": "Enlarge or reposition the footprint to use more of the buildable envelope.",
    "pbc_component": "Add greenery_polygons to raise the biologically-active area.",
    "parking_component": "Increase parking_count to meet the estimated demand.",
}


def critique(proposal: LayoutProposal, score: ProposalScore) -> StructuredCritique:
    """Produce a structured critique guiding the next proposal (§4.1.B.4)."""
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
