"""Hard-constraint validation — runs BEFORE scoring (Phase 4 §4.1.B.2 / §4.4 / §14.2).

``validate_hard`` enforces the dominant hard blockers (§14.2 "hard blocker must
dominate"): a footprint MUST be inside the buildable envelope and MUST NOT intersect any
no-build / hard-constraint zone. If ANY hard violation is present the proposal is INVALID
regardless of its score — the loop can never "draw away" a hard blocker to raise a score
(§4.4 anti-pattern, §21 "działka budowlana bo wygląda na budowlaną").

This is a pure geometry guard over typed proposal data (NFR-SEC-003): the model's drawing
is validated, never trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from plot_agent.context import AnalysisContext
from plot_agent.drawing.proposal import LayoutProposal, MasterplanProposal

# Tolerance (m²) for floating-point spill outside the envelope — below this we treat the
# footprint as contained (avoids spurious violations from tiny boundary numeric noise).
_AREA_EPS_M2 = 1e-6


@dataclass(frozen=True)
class Violation:
    """A hard-constraint violation (§14.2). ``kind`` is machine-readable."""

    # "outside_envelope" | "intersects_hard_constraint" | "empty_footprint"
    # | "outside_parcel" (Phase 9 masterplan: building not within the parcel)
    kind: str
    detail: str
    overlap_area_m2: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "detail": self.detail, "overlap_area_m2": self.overlap_area_m2}


def _outside_envelope_area(footprint: BaseGeometry, envelope: BaseGeometry) -> float:
    """Area of the footprint that lies OUTSIDE the buildable envelope (m²)."""
    outside = footprint.difference(envelope)
    return float(outside.area) if not outside.is_empty else 0.0


def validate_hard(proposal: LayoutProposal, context: AnalysisContext) -> list[Violation]:
    """Return all hard violations; an empty list means the proposal passes the guard.

    Order: this MUST be called before scoring (§4.4). The drawing loop refuses to accept
    any proposal that returns a non-empty list here.
    """
    violations: list[Violation] = []
    try:
        footprint = proposal.footprint_geometry()
    except ValueError as exc:
        return [Violation(kind="empty_footprint", detail=str(exc))]

    if footprint.is_empty or footprint.area <= 0:
        return [Violation(kind="empty_footprint", detail="footprint geometry is empty")]

    envelope = context.envelope_geom()
    outside = _outside_envelope_area(footprint, envelope)
    if outside > _AREA_EPS_M2:
        violations.append(
            Violation(
                kind="outside_envelope",
                detail=f"{outside:,.2f} m² of footprint lies outside the buildable envelope",
                overlap_area_m2=round(outside, 4),
            )
        )

    for i, hard in enumerate(context.hard_geoms()):
        inter = footprint.intersection(hard)
        if not inter.is_empty and inter.area > _AREA_EPS_M2:
            violations.append(
                Violation(
                    kind="intersects_hard_constraint",
                    detail=f"footprint intersects hard/no-build zone #{i + 1} by {inter.area:,.2f} m²",
                    overlap_area_m2=round(float(inter.area), 4),
                )
            )

    return violations


def validate_hard_masterplan(
    proposal: MasterplanProposal, context: AnalysisContext
) -> list[Violation]:
    """Hard guard for a masterplan proposal (Phase 9 §9.1.5 + §14.2).

    * EVERY building must lie within the parcel — buildings-within-parcel is checked
      HERE at scoring time (recorded as ``outside_parcel`` violations), never
      hard-rejected at parse (consistent with the v1 ``validate_hard`` behaviour);
    * NEW buildings (status ``projektowany`` / ``w_budowie``) must additionally stay
      inside the buildable envelope and clear of hard/no-build zones — existing /
      heritage buildings already stand, so the envelope guard does not apply to them.
    """
    violations: list[Violation] = []
    parcel = context.parcel_geom()
    envelope = context.envelope_geom()

    for building in proposal.buildings:
        footprint = building.footprint_geometry()
        if footprint.is_empty or footprint.area <= 0:
            violations.append(
                Violation(kind="empty_footprint", detail=f"{building.name}: empty footprint")
            )
            continue
        outside_parcel = float(footprint.difference(parcel).area)
        if outside_parcel > _AREA_EPS_M2:
            violations.append(
                Violation(
                    kind="outside_parcel",
                    detail=(
                        f"{building.name}: {outside_parcel:,.2f} m² of footprint lies "
                        "outside the parcel"
                    ),
                    overlap_area_m2=round(outside_parcel, 4),
                )
            )
        if building.status not in ("projektowany", "w_budowie"):
            continue  # existing/heritage: parcel containment only
        outside_env = _outside_envelope_area(footprint, envelope)
        if outside_env > _AREA_EPS_M2:
            violations.append(
                Violation(
                    kind="outside_envelope",
                    detail=(
                        f"{building.name}: {outside_env:,.2f} m² of footprint lies "
                        "outside the buildable envelope"
                    ),
                    overlap_area_m2=round(outside_env, 4),
                )
            )
        for i, hard in enumerate(context.hard_geoms()):
            inter = footprint.intersection(hard)
            if not inter.is_empty and inter.area > _AREA_EPS_M2:
                violations.append(
                    Violation(
                        kind="intersects_hard_constraint",
                        detail=(
                            f"{building.name}: footprint intersects hard/no-build zone "
                            f"#{i + 1} by {inter.area:,.2f} m²"
                        ),
                        overlap_area_m2=round(float(inter.area), 4),
                    )
                )

    if not proposal.buildings:
        violations.append(
            Violation(kind="empty_footprint", detail="masterplan has no buildings")
        )
    return violations
