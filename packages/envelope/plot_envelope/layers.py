"""Risk-layer typing + per-layer overlay policy (Phase 7 §7.1.1).

A :class:`RiskLayer` is the already-fetched geometry for one risk theme (flood,
protected, landslide, heritage, utilities, roads, watercourses, forest), normalised to
shapely geometries in the analytical CRS (EPSG:2180). The orchestrator (Phase 7 §C)
builds these from connector evidence; the overlay engine consumes them and never touches
the network (so tests stay zero-network).

:data:`LAYER_POLICY` maps each :class:`RiskKind` to:

* the per-layer **buffer** distance in metres (a default; the orchestrator may override
  it from ``rulesets/PL/**`` / ``profiles`` thresholds — see ``overlay_layers(..., buffers=...)``);
* whether the constraint is **hard** (a no-build / blocker) or **soft** (penalised);
* the §11.2 :class:`~plot_domain.RiskType` and a base :class:`~plot_domain.Severity`.

Thresholds live in rulesets, not hardcoded in logic (§12/§21): these defaults are the
documented v1 fallbacks for the MVP risk layers, clearly marked, used only when a ruleset
value is absent. They are conservative (e.g. a watercourse keeps a buffer; a flood hazard
zone is hard).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from plot_domain import LegalStatus, RiskType, Severity
from shapely.geometry.base import BaseGeometry


class RiskKind(str, Enum):
    """The MVP risk-layer themes checked by quick_screening (§4.1 / §18.2)."""

    FLOOD = "flood"
    PROTECTED = "protected"
    LANDSLIDE = "landslide"
    HERITAGE = "heritage"
    UTILITIES = "utilities"
    ROADS = "roads"
    WATERCOURSES = "watercourses"
    FOREST = "forest"


@dataclass(frozen=True)
class LayerPolicy:
    """Overlay policy for one risk theme (v1 default; ruleset-overridable)."""

    risk_kind: RiskKind
    risk_type: RiskType
    #: Default buffer applied around fetched features before intersecting the parcel (m).
    default_buffer_m: float
    #: A *hard* constraint removes buildable area (no-build); a *soft* one is penalised.
    hard: bool
    #: Base severity contributed when the theme overlaps the parcel (§11.2).
    base_severity: Severity
    #: Human label used in summaries / map captions.
    label: str


#: v1 default overlay policy per theme (§7.1.1). Buffers are conservative defaults; the
#: orchestrator passes ruleset/profile-derived values where available (clearly marked).
LAYER_POLICY: dict[RiskKind, LayerPolicy] = {
    RiskKind.FLOOD: LayerPolicy(
        RiskKind.FLOOD, RiskType.FLOOD, default_buffer_m=0.0, hard=True,
        base_severity=Severity.HIGH, label="Strefa zagrożenia powodziowego",
    ),
    RiskKind.PROTECTED: LayerPolicy(
        RiskKind.PROTECTED, RiskType.ENVIRONMENTAL, default_buffer_m=0.0, hard=False,
        base_severity=Severity.MEDIUM, label="Obszar chronionej przyrody",
    ),
    RiskKind.LANDSLIDE: LayerPolicy(
        RiskKind.LANDSLIDE, RiskType.GEOLOGY, default_buffer_m=0.0, hard=True,
        base_severity=Severity.HIGH, label="Teren osuwiskowy / zagrożony ruchami masowymi",
    ),
    RiskKind.HERITAGE: LayerPolicy(
        RiskKind.HERITAGE, RiskType.HERITAGE, default_buffer_m=0.0, hard=False,
        base_severity=Severity.MEDIUM, label="Obiekt / strefa ochrony zabytków",
    ),
    RiskKind.UTILITIES: LayerPolicy(
        # A utility line carries a technical protection corridor → soft setback buffer.
        RiskKind.UTILITIES, RiskType.UTILITIES, default_buffer_m=1.5, hard=False,
        base_severity=Severity.LOW, label="Sieć uzbrojenia terenu (strefa techniczna)",
    ),
    RiskKind.ROADS: LayerPolicy(
        # Road reserve / building-line setback (statutory; v1 conservative default).
        RiskKind.ROADS, RiskType.ROAD_ACCESS, default_buffer_m=6.0, hard=False,
        base_severity=Severity.LOW, label="Droga / pas drogowy (linia zabudowy)",
    ),
    RiskKind.WATERCOURSES: LayerPolicy(
        # Watercourse keep-clear strip (water-law; v1 conservative default).
        RiskKind.WATERCOURSES, RiskType.ENVIRONMENTAL, default_buffer_m=5.0, hard=False,
        base_severity=Severity.MEDIUM, label="Ciek wodny (pas przybrzeżny)",
    ),
    RiskKind.FOREST: LayerPolicy(
        # Forest edge / fire-setback (technical; v1 conservative default).
        RiskKind.FOREST, RiskType.ENVIRONMENTAL, default_buffer_m=12.0, hard=False,
        base_severity=Severity.MEDIUM, label="Las / grunt leśny (odległość od lasu)",
    ),
}


@dataclass
class RiskLayer:
    """One fetched risk theme's geometry + provenance (Phase 7 §7.1.1).

    ``geometries`` are shapely geometries already in EPSG:2180 (the orchestrator coerces
    connector GeoJSON evidence to shapely). ``status`` distinguishes a theme that was
    *checked and clear* (``not_detected``) from one whose source failed
    (``source_unavailable``) — the two are never conflated (§21 / NFR-REL-001). The
    ``source_id`` / ``source_legal_status`` flow onto the emitted Constraints (§11.3).
    """

    kind: RiskKind
    geometries: list[BaseGeometry] = field(default_factory=list)
    #: 'ok' (features present) | 'not_detected' | 'source_unavailable' (mirrors ResultStatus values).
    status: str = "not_detected"
    source_id: str = "no_source"
    source_legal_status: LegalStatus = LegalStatus.INFORMATIVE
    source_confidence: float = 0.7
    #: Buffer override (m). When None, :data:`LAYER_POLICY` default is used.
    buffer_m_override: float | None = None

    def policy(self) -> LayerPolicy:
        return LAYER_POLICY[self.kind]

    def buffer_m(self) -> float:
        """Effective buffer: override if set, else the policy default."""
        if self.buffer_m_override is not None:
            return self.buffer_m_override
        return self.policy().default_buffer_m

    def has_features(self) -> bool:
        return any(g is not None and not g.is_empty for g in self.geometries)
