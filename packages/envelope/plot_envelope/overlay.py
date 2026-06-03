"""Overlay engine: parcel × risk layers → Constraints (§11.3) (Phase 7 §7.1.1).

For each fetched :class:`~plot_envelope.layers.RiskLayer`, this module:

1. unions the layer's features, applies the per-layer **buffer** (metres, EPSG:2180,
   via :func:`plot_geo.buffer_m` — never in degrees), then **intersects** with the parcel
   (``plot_geo.intersection``);
2. records **area attribution** on the resulting :class:`~plot_domain.Constraint`
   (``applies_to_area_m2`` / ``applies_to_percent``, §11.3 / §30 "which constraint
   removed which area");
3. **classifies** the constraint hard vs soft from the layer policy
   (:data:`~plot_envelope.layers.LAYER_POLICY`) and carries the source's
   ``legal_status`` / confidence through.

The buffered-then-intersected geometry is the *constraint geometry*. For a *hard* layer
that geometry is what later becomes a :class:`~plot_domain.NoBuildZone` (``nobuild.py``);
soft layers are penalised but do not subtract buildable area.

This module never touches the network and never imports ``plot_connectors`` — it consumes
already-fetched geometry, keeping connectors↛rules and rules↛connectors intact (§9.4).
"""

from __future__ import annotations

import uuid

import plot_geo
from plot_domain import Constraint, Severity
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from plot_envelope.layers import RiskKind, RiskLayer

# Severity ordering for "dominance" comparisons elsewhere.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def severity_rank(sev: Severity) -> int:
    """Numeric rank for a §11.2 severity (info=0 … critical=4)."""
    return _SEVERITY_RANK[sev]


def _layer_geometry(layer: RiskLayer) -> BaseGeometry | None:
    """Union a layer's features then buffer (metric EPSG:2180). ``None`` if no features."""
    feats = [g for g in layer.geometries if g is not None and not g.is_empty]
    if not feats:
        return None
    merged = plot_geo.dissolve(feats)
    if merged.is_empty:
        return None
    buf = layer.buffer_m()
    if buf and buf > 0.0:
        # buffer_m with no src_crs assumes already-metric analytical geometry (its
        # documented contract) — we are in EPSG:2180, so this is metres, not degrees.
        merged = plot_geo.buffer_m(merged, buf)
    return merged


def overlay_layer(parcel_geom: BaseGeometry, layer: RiskLayer) -> Constraint | None:
    """Build a :class:`~plot_domain.Constraint` for one risk layer, or ``None``.

    Returns ``None`` when the layer has no features (nothing to constrain) — the caller
    records *that* theme as ``not_detected`` / ``source_unavailable`` separately so the
    distinction is never lost (§21). A returned constraint always carries area attribution.
    """
    geom = _layer_geometry(layer)
    if geom is None:
        return None
    overlap = plot_geo.intersection(geom, parcel_geom)
    if overlap.is_empty:
        return None

    parcel_area = float(parcel_geom.area)
    overlap_area = float(overlap.area)
    pct = (overlap_area / parcel_area * 100.0) if parcel_area > 0.0 else 0.0

    policy = layer.policy()
    # A hard theme that genuinely overlaps the parcel is at least HIGH; a hard theme that
    # only touches via buffer keeps its base severity. Soft themes keep the base severity.
    severity = policy.base_severity

    summary = (
        f"{policy.label}: nakłada się na {overlap_area:,.0f} m² "
        f"({pct:.1f}%) działki "
        f"[{'twarde ograniczenie' if policy.hard else 'miękkie ograniczenie'}]."
    )

    return Constraint(
        constraint_id=f"con:{layer.kind.value}:{uuid.uuid4().hex[:8]}",
        constraint_type=layer.kind.value,
        source_id=layer.source_id,
        source_legal_status=layer.source_legal_status,
        geometry=mapping(overlap),
        applies_to_area_m2=round(overlap_area, 3),
        applies_to_percent=round(pct, 3),
        rule_id=None,
        rule_version=None,
        severity=severity,
        confidence=round(float(layer.source_confidence), 4),
        human_summary=summary,
        machine_summary={
            "risk_kind": layer.kind.value,
            "risk_type": policy.risk_type.value,
            "hard": policy.hard,
            "buffer_m": layer.buffer_m(),
            "overlap_m2": round(overlap_area, 3),
            "overlap_percent": round(pct, 3),
            "source_status": layer.status,
        },
        mitigation=None,
    )


def overlay_layers(
    parcel_geom: BaseGeometry, risk_layers: list[RiskLayer]
) -> list[Constraint]:
    """Intersect the parcel with every fetched risk layer → list of Constraints (§7.1.1).

    Only layers that actually overlap the parcel produce a constraint. Hard vs soft and
    area attribution are recorded per constraint (§11.3). Ordering is stable: hard
    constraints first, then by descending overlap area, so the report leads with blockers.
    """
    constraints: list[Constraint] = []
    for layer in risk_layers:
        con = overlay_layer(parcel_geom, layer)
        if con is not None:
            constraints.append(con)
    constraints.sort(
        key=lambda c: (
            0 if bool(c.machine_summary.get("hard")) else 1,
            -(c.applies_to_area_m2 or 0.0),
        )
    )
    return constraints


def is_hard(constraint: Constraint) -> bool:
    """Whether a constraint is a hard (no-build / blocker) one (§14.2)."""
    return bool(constraint.machine_summary.get("hard"))


def hard_constraints(constraints: list[Constraint]) -> list[Constraint]:
    return [c for c in constraints if is_hard(c)]


def soft_constraints(constraints: list[Constraint]) -> list[Constraint]:
    return [c for c in constraints if not is_hard(c)]


# Themes that, when their source is unavailable, are the most consequential to flag as an
# unknown (so the model knows it is missing a potential blocker, not that it is absent).
HARD_THEMES: frozenset[RiskKind] = frozenset(
    {RiskKind.FLOOD, RiskKind.LANDSLIDE}
)
