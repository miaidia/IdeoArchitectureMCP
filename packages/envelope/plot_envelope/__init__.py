"""Overlay/constraints engine + buildable envelope v1 + risk/decision (Phase 7 §7.1).

This is the analytical core behind ``quick_screening`` (IMPLEMENTATION_PLAN.md Phase 7):

* :mod:`plot_envelope.layers` — :class:`RiskLayer` (already-fetched theme geometry) +
  per-theme :data:`LAYER_POLICY` (buffer / hard-vs-soft / severity / risk_type).
* :mod:`plot_envelope.overlay` — :func:`overlay_layers`: parcel × risk layers →
  :class:`~plot_domain.Constraint`s with area attribution (§11.3 / §30).
* :mod:`plot_envelope.nobuild` — :func:`no_build_zones`: hard constraints + statutory
  boundary setback → :class:`~plot_domain.NoBuildZone`s.
* :mod:`plot_envelope.envelope` — :func:`buildable_envelope_v1`: parcel − setbacks −
  no-build, largest inscribed rectangle, source-ranked confidence (§7.5), area trace.
* :mod:`plot_envelope.risk` — :func:`red_flags`, :func:`decision` (hard-blocker dominance,
  §14.2), :func:`unknowns_for_unavailable`, :func:`next_actions` (§7.19).

Decoupling (§9.4): depends on plot_domain / plot_shared / plot_geo / plot_rules and must
never reach the connectors layer (it consumes already-fetched geometry).
"""

from __future__ import annotations

from plot_envelope.envelope import buildable_envelope_v1, envelope_confidence
from plot_envelope.layers import LAYER_POLICY, LayerPolicy, RiskKind, RiskLayer
from plot_envelope.nobuild import no_build_zones, setback_no_build_zone
from plot_envelope.overlay import (
    hard_constraints,
    is_hard,
    overlay_layer,
    overlay_layers,
    severity_rank,
    soft_constraints,
)
from plot_envelope.risk import (
    decision,
    next_actions,
    red_flags,
    unknowns_for_unavailable,
)

__version__ = "0.1.0"

__all__ = [
    # layers
    "RiskKind",
    "RiskLayer",
    "LayerPolicy",
    "LAYER_POLICY",
    # overlay
    "overlay_layers",
    "overlay_layer",
    "hard_constraints",
    "soft_constraints",
    "is_hard",
    "severity_rank",
    # nobuild
    "no_build_zones",
    "setback_no_build_zone",
    # envelope
    "buildable_envelope_v1",
    "envelope_confidence",
    # risk / decision
    "red_flags",
    "decision",
    "unknowns_for_unavailable",
    "next_actions",
]
