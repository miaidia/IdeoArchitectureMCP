"""Capacity / chłonność engine (Phase 9 §9.1.2; base_assumptions §8.5 F-0180–0215).

Pure functions, no I/O:

* :func:`building_metrics` — per-building powierzchnia zabudowy (footprint union), PC
  nadziemna = Σ(owned_segment_area × floors) where overlapping segment footprints are
  counted ONCE (overlap attributed to the taller segment — no PC/PUM double-counting),
  GFA, PUM/PUU estimates, mieszkania estimate, honouring ``ground_floor_use`` (usługi
  w parterze: parter → PUU, piętra → PUM);
* :func:`masterplan_metrics` — totals + per-building + per-stage table rows (ROBYG
  exemplar columns: Liczba mieszkań / PUM / PUU / PU + SUMA row), intensywność,
  coverage, PBC balance (greenery ∪ playgrounds·credit per WT §40 — the credit share is
  READ from the wt-40 ruleset, never a literal), parking demand (MPZP indicators ONLY —
  a missing indicator yields ``unknown`` + an :class:`~plot_domain.UnknownItem`, never a
  default, §0v2.4) vs supply, plac zabaw demand bracket (WT §40 select table resolved
  via the :mod:`plot_rules` engine, not literals);
* :func:`generate_capacity_scenarios` — the no-drawing mode behind
  ``capacity_generate_scenarios``: conservative/base/optimistic/max scenarios from the
  buildable envelope + Phase 8 indicators (F-0202–0205) + sensitivity (F-0206).

Estimation factors live in :class:`CapacityConfig` (NOT rulesets — they are industry
heuristics, not law); every metric derived from them carries ``basis:
"industry_heuristic"`` in its metadata, with PN-ISO 9836:2022-07 cited as the
measurement standard the estimate approximates (§0v2.2). Legal threshold VALUES (WT §39
PBC, §40 plac zabaw brackets + 30% PBC share) are read from the ruleset registry.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from plot_domain import CapacityScenario, Severity, UnknownItem
from plot_rules import RuleCheck, RulesetRegistry, evaluate
from shapely.geometry.base import BaseGeometry

# --------------------------------------------------------------------------- #
# Basis tags (anti-pattern guard §0v2.4: PUM must never appear without a basis).
# --------------------------------------------------------------------------- #
BASIS_GEOMETRY = "geometry_measured"
BASIS_HEURISTIC = "industry_heuristic"
BASIS_INDICATOR = "planning_indicator"
BASIS_RULESET = "ruleset"

#: The measurement standard the PUM/PUU estimates approximate (plan §0v2.2 wording):
#: the heuristic PUM ≈ 0.70 × powierzchnia całkowita nadziemna (±5 p.p. for optimized
#: plans) is an industry estimation factor, NOT law and NOT part of any ruleset.
MEASUREMENT_STANDARD_NOTE = (
    "PN-ISO 9836:2022-07 cited as the measurement standard the estimate approximates "
    "(norma powołana bez daty w zał. 2 rozp. o projekcie budowlanym, Dz.U. 2022 poz. "
    "1679); industry heuristic PUM ≈ pum_efficiency × powierzchnia całkowita nadziemna "
    "(±5 p.p. for optimized plans) — basis: industry_heuristic, NOT law."
)

# Ruleset ids the engine reads legal values from (ids are structural references; the
# VALUES live only in the YAML files — Phase 8/9 anti-pattern guard).
WT40_RULE_ID = "PL-WT-40-PLAC-ZABAW-001"
WT40_PBC_SHARE_THRESHOLD = "min_pbc_share"
WT40_AREA_CHECK_NAME = "powierzchnia-placu-zabaw"
WT39_RULE_ID = "PL-WT-39-PBC-001"
WT39_STATUTORY_THRESHOLD = "statutory_min_pbc_ratio"

# Canonical Phase 8 indicator keys (plot_planning.parser.schema.INDICATOR_NAMES) the
# capacity engine reads — EXACT names, never synonyms.
IND_MAX_INTENSITY = "max_intensity"
IND_MAX_HEIGHT_M = "max_height_m"
IND_MAX_KONDYGNACJE = "max_kondygnacje"
IND_MAX_COVERAGE = "max_coverage_ratio"
IND_MIN_PBC = "min_pbc_ratio"
IND_PARKING_PER_MIESZKANIE = "parking_per_mieszkanie"
IND_PARKING_PER_100M2_USLUG = "parking_per_100m2_uslug"


# --------------------------------------------------------------------------- #
# Config — estimation factors (heuristics, NOT legal thresholds; §0v2.2/§0v2.4)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CapacityConfig:
    """Estimation factors for the capacity engine (config, NOT a ruleset).

    These are industry heuristics (basis ``industry_heuristic``) — every metric derived
    from them carries that basis in output metadata. NO legal threshold values here.
    """

    pum_efficiency: float = 0.70  # PUM ≈ 0.70 × PC nadziemna (±5 p.p., §0v2.2)
    puu_efficiency: float = 0.80  # PUU analogue for usługi
    avg_mieszkanie_m2: float = 52.0  # average dwelling size for mieszkania estimate
    floor_height_m: float = 3.3  # storey height for height/floor derivations

    def basis_block(self) -> dict[str, Any]:
        """Metadata block stamped onto every config-derived metric set."""
        return {
            "basis": BASIS_HEURISTIC,
            "pum_efficiency": self.pum_efficiency,
            "puu_efficiency": self.puu_efficiency,
            "avg_mieszkanie_m2": self.avg_mieszkanie_m2,
            "floor_height_m": self.floor_height_m,
            "measurement_standard": MEASUREMENT_STANDARD_NOTE,
        }


# --------------------------------------------------------------------------- #
# Structural protocols — the DSL v2 models (plot_agent.drawing.proposal) satisfy
# these; plot_planning must not import plot_agent (dependency direction §9.4).
# --------------------------------------------------------------------------- #
class SegmentLike(Protocol):
    """One building wing: floors, use, optional parter use, composed geometry."""

    @property
    def floors(self) -> int: ...

    @property
    def use(self) -> str: ...

    @property
    def ground_floor_use(self) -> str | None: ...

    def geometry(self) -> BaseGeometry: ...


class BuildingLike(Protocol):
    """One building: named, staged, statused segments with a footprint union."""

    @property
    def name(self) -> str: ...

    @property
    def segments(self) -> Sequence[SegmentLike]: ...

    @property
    def stage(self) -> int | None: ...

    @property
    def status(self) -> str: ...

    @property
    def underground_floors(self) -> int: ...

    def footprint_geometry(self) -> BaseGeometry: ...


class ParkingLike(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def spaces(self) -> int: ...


class MasterplanLike(Protocol):
    """The duck-typed surface of :class:`plot_agent.drawing.proposal.MasterplanProposal`."""

    @property
    def buildings(self) -> Sequence[BuildingLike]: ...

    @property
    def parking(self) -> Sequence[ParkingLike]: ...

    @property
    def zabudowa_srodmiejska(self) -> bool: ...

    def buildings_footprint_geometry(self) -> BaseGeometry: ...

    def greenery_geometry(self) -> BaseGeometry: ...

    def playgrounds_geometry(self) -> BaseGeometry: ...


# --------------------------------------------------------------------------- #
# Per-building metrics (F-0180–0183, F-0198–0199)
# --------------------------------------------------------------------------- #
# Use → PU category. Classification semantics (not legal values): mieszkalny feeds PUM,
# usługi/hotel feed PUU, garaż/techniczne feed neither (PC only).
_UPPER_FLOOR_CATEGORY: dict[str, str | None] = {
    "mieszkalny": "pum",
    "mieszkalno-uslugowy": "pum",  # upper floors residential; parter handled below
    "uslugowy": "puu",
    "hotelowy": "puu",
    "garazowy": None,
    "techniczny": None,
}
_GROUND_FLOOR_CATEGORY: dict[str, str | None] = {
    "mieszkalny": "pum",
    "uslugowy": "puu",
    "garaz": None,
    "techniczny": None,
}


@dataclass
class BuildingMetrics:
    """Per-building capacity metrics with explicit basis metadata (Phase 9 §9.1.2)."""

    name: str
    stage: int | None
    status: str
    underground_floors: int
    floors_by_segment: list[int]
    uses: list[str]
    powierzchnia_zabudowy_m2: float
    pc_nadziemna_m2: float
    pc_podziemna_m2: float
    gfa_m2: float
    pc_mieszkalna_m2: float
    pc_uslugowa_m2: float
    pum_m2: float
    puu_m2: float
    pu_m2: float
    mieszkania_estimate: int
    height_estimate_m: float
    basis: dict[str, str] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "status": self.status,
            "underground_floors": self.underground_floors,
            "floors_by_segment": self.floors_by_segment,
            "uses": self.uses,
            "powierzchnia_zabudowy_m2": round(self.powierzchnia_zabudowy_m2, 2),
            "pc_nadziemna_m2": round(self.pc_nadziemna_m2, 2),
            "pc_podziemna_m2": round(self.pc_podziemna_m2, 2),
            "gfa_m2": round(self.gfa_m2, 2),
            "pc_mieszkalna_m2": round(self.pc_mieszkalna_m2, 2),
            "pc_uslugowa_m2": round(self.pc_uslugowa_m2, 2),
            "pum_m2": round(self.pum_m2, 2),
            "puu_m2": round(self.puu_m2, 2),
            "pu_m2": round(self.pu_m2, 2),
            "mieszkania_estimate": self.mieszkania_estimate,
            "height_estimate_m": round(self.height_estimate_m, 2),
            "basis": self.basis,
            "assumptions": self.assumptions,
        }


#: Area tolerance (m²) below which segment/building footprint overlap is treated as
#: floating-point noise rather than a real overlap.
_OVERLAP_EPS_M2 = 1e-6


def _owned_segment_areas(segments: Sequence[SegmentLike]) -> tuple[list[float], float]:
    """Decompose segment footprints into non-overlapping OWNED areas → ``(areas, overlap)``.

    Overlap must not double-count PC/PUM: each overlapping piece is assigned ONCE, to
    the segment with MORE floors (the architecturally correct reading — a shared core
    rises with the taller wing). Shapely difference cascade ordered by floors
    descending: the tallest segment keeps its full footprint, each next segment owns
    only its area minus the already-claimed footprint. ``areas`` is indexed in the
    ORIGINAL segment order; ``overlap`` is the total raw-minus-owned area (m²).
    """
    geoms = [segment.geometry() for segment in segments]
    owned = [0.0] * len(geoms)
    overlap_total = 0.0
    claimed: BaseGeometry | None = None
    # Floors descending; original index breaks ties deterministically.
    for i in sorted(range(len(geoms)), key=lambda k: (-int(segments[k].floors), k)):
        geom = geoms[i]
        piece = geom if claimed is None else geom.difference(claimed)
        owned[i] = float(piece.area)
        overlap_total += float(geom.area) - owned[i]
        claimed = geom if claimed is None else claimed.union(geom)
    return owned, overlap_total


def _segment_split(segment: SegmentLike, area: float) -> tuple[float, float, float, list[str]]:
    """One segment → ``(pc_total, pc_mieszkalna, pc_uslugowa, assumptions)``.

    ``area`` is the segment's OWNED (non-double-counted) footprint area from
    :func:`_owned_segment_areas`. ``ground_floor_use`` overrides the parter category
    (usługi w parterze: parter → PUU pool, piętra → PUM pool). ``mieszkalno-uslugowy``
    without an explicit parter use is conservatively split parter→usługi /
    piętra→mieszkania and the assumption recorded.
    """
    assumptions: list[str] = []
    floors = int(segment.floors)
    pc_total = area * floors

    ground_use = segment.ground_floor_use
    if ground_use is None and segment.use == "mieszkalno-uslugowy":
        ground_use = "uslugowy"
        assumptions.append(
            "mieszkalno-uslugowy without explicit ground_floor_use: assumed usługi w "
            "parterze (parter → PUU, piętra → PUM)"
        )

    upper_cat = _UPPER_FLOOR_CATEGORY.get(segment.use)
    if ground_use is None:
        ground_cat = "pum" if segment.use == "mieszkalny" else upper_cat
    else:
        ground_cat = _GROUND_FLOOR_CATEGORY.get(ground_use)

    pc_m = 0.0
    pc_u = 0.0
    # parter (1 floor) by ground category, upper (floors-1) by the segment use.
    if ground_cat == "pum":
        pc_m += area
    elif ground_cat == "puu":
        pc_u += area
    upper = area * max(0, floors - 1)
    if upper_cat == "pum":
        pc_m += upper
    elif upper_cat == "puu":
        pc_u += upper
    return pc_total, pc_m, pc_u, assumptions


def building_metrics(building: BuildingLike, config: CapacityConfig | None = None) -> BuildingMetrics:
    """Compute per-building capacity metrics (Phase 9 §9.1.2; pure geometry + config)."""
    cfg = config or CapacityConfig()
    footprint = float(building.footprint_geometry().area)

    pc_nadziemna = 0.0
    pc_mieszkalna = 0.0
    pc_uslugowa = 0.0
    assumptions: list[str] = []
    floors_by_segment: list[int] = []
    uses: list[str] = []
    # Overlapping segments (the natural corner-rectangle drawing idiom) must not
    # double-count PC/PUM: each segment contributes only its OWNED area, with overlap
    # attributed once to the taller segment (see _owned_segment_areas).
    owned_areas, overlap_m2 = _owned_segment_areas(building.segments)
    for segment, owned_area in zip(building.segments, owned_areas, strict=True):
        pc, pc_m, pc_u, seg_assumptions = _segment_split(segment, owned_area)
        pc_nadziemna += pc
        pc_mieszkalna += pc_m
        pc_uslugowa += pc_u
        assumptions.extend(seg_assumptions)
        floors_by_segment.append(int(segment.floors))
        uses.append(str(segment.use))
    if overlap_m2 > _OVERLAP_EPS_M2:
        assumptions.append(
            f"segment footprints overlap by {overlap_m2:.2f} m²: the overlap is counted "
            "ONCE and attributed to the segment with more floors (the shared core rises "
            "with the taller wing) — PC/PUM are not double-counted"
        )

    pc_podziemna = footprint * int(building.underground_floors)
    pum = pc_mieszkalna * cfg.pum_efficiency
    puu = pc_uslugowa * cfg.puu_efficiency
    # Conservative integer estimate: round DOWN (a fraction of a dwelling is not a
    # dwelling) so per-building values sum exactly into stage/total rows.
    mieszkania = int(pum // cfg.avg_mieszkanie_m2) if cfg.avg_mieszkanie_m2 > 0 else 0
    max_floors = max(floors_by_segment) if floors_by_segment else 0

    return BuildingMetrics(
        name=building.name,
        stage=building.stage,
        status=building.status,
        underground_floors=int(building.underground_floors),
        floors_by_segment=floors_by_segment,
        uses=uses,
        powierzchnia_zabudowy_m2=footprint,
        pc_nadziemna_m2=pc_nadziemna,
        pc_podziemna_m2=pc_podziemna,
        gfa_m2=pc_nadziemna + pc_podziemna,
        pc_mieszkalna_m2=pc_mieszkalna,
        pc_uslugowa_m2=pc_uslugowa,
        pum_m2=pum,
        puu_m2=puu,
        pu_m2=pum + puu,
        mieszkania_estimate=mieszkania,
        height_estimate_m=max_floors * cfg.floor_height_m,
        basis={
            "powierzchnia_zabudowy_m2": BASIS_GEOMETRY,
            "pc_nadziemna_m2": BASIS_GEOMETRY,
            "pc_podziemna_m2": BASIS_GEOMETRY,
            "gfa_m2": BASIS_GEOMETRY,
            "pum_m2": BASIS_HEURISTIC,
            "puu_m2": BASIS_HEURISTIC,
            "pu_m2": BASIS_HEURISTIC,
            "mieszkania_estimate": BASIS_HEURISTIC,
            "height_estimate_m": BASIS_HEURISTIC,
        },
        assumptions=assumptions,
    )


# --------------------------------------------------------------------------- #
# Masterplan totals / stage table / PBC / parking / plac zabaw
# --------------------------------------------------------------------------- #
@dataclass
class MasterplanMetrics:
    """Masterplan capacity metrics (Phase 9 §9.1.2): totals, stages, balances.

    ``warnings`` carries drawing-quality flags (e.g. footprints of DISTINCT buildings
    overlapping — a drawing error, not a stacking idiom): informational, never a hard
    violation (design choice: metrics warning rather than a validate_hard violation, so
    §14.2 hard-blocker dominance stays reserved for legal/geometric blockers).
    """

    per_building: list[BuildingMetrics]
    totals: dict[str, Any]
    stage_table: list[dict[str, Any]]
    pbc: dict[str, Any]
    parking: dict[str, Any]
    playground: dict[str, Any]
    indicator_checks: list[dict[str, Any]]
    unknowns: list[UnknownItem]
    config_basis: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_building": [b.to_dict() for b in self.per_building],
            "totals": self.totals,
            "stage_table": self.stage_table,
            "pbc": self.pbc,
            "parking": self.parking,
            "playground": self.playground,
            "indicator_checks": self.indicator_checks,
            "unknowns": [u.model_dump(mode="json") for u in self.unknowns],
            "config_basis": self.config_basis,
            "warnings": self.warnings,
        }


def _unknown(topic: str, reason: str, action: str, analysis_id: str | None) -> UnknownItem:
    return UnknownItem(
        id=f"unk:capacity:{uuid.uuid4().hex[:8]}",
        analysis_id=analysis_id,
        topic=topic,
        severity=Severity.HIGH,
        reason=reason,
        suggested_action=action,
    )


def _threshold_from_registry(
    registry: RulesetRegistry | None, rule_id: str, threshold: str
) -> tuple[float | None, str | None]:
    """Read a named threshold VALUE from a loaded ruleset rule → ``(value, citation)``."""
    if registry is None:
        return None, None
    rule = registry.get(rule_id)
    if rule is None:
        return None, None
    thresholds = rule.raw.get("thresholds") or {}
    if threshold not in thresholds:
        return None, rule.source_reference
    return float(thresholds[threshold]), rule.source_reference


def _playground_requirement(
    registry: RulesetRegistry | None,
    *,
    total_mieszkania: int,
    playground_area_m2: float,
    zabudowa_srodmiejska: bool,
) -> tuple[float | None, RuleCheck | None]:
    """Resolve the WT §40 required playground area via the rules ENGINE (no literals).

    Returns ``(required_m2, rule_check)``; ``required_m2`` is the resolved (and
    śródmiejska-modified) bracket value from the rule's select table — ``None`` when the
    rule is absent or the bracket unresolvable.
    """
    if registry is None:
        return None, None
    rule = registry.get(WT40_RULE_ID)
    if rule is None:
        return None, None
    check = evaluate(
        rule,
        {
            "is_multifamily": total_mieszkania > 0,
            "total_mieszkania": float(total_mieszkania),
            "playground_area_m2": playground_area_m2,
            "zabudowa_srodmiejska": zabudowa_srodmiejska,
        },
        mode="optimistic",
    )
    required: float | None = None
    for entry in check.trace.get("checks", []):
        if entry.get("name") == WT40_AREA_CHECK_NAME and "target_value" in entry:
            required = float(entry["target_value"])
    if total_mieszkania == 0:
        required = 0.0  # rule not applicable below the trigger (engine: not_applicable)
    return required, check


def masterplan_metrics(
    proposal: MasterplanLike,
    parcel_geom: BaseGeometry,
    indicators: Mapping[str, Any],
    *,
    config: CapacityConfig | None = None,
    registry: RulesetRegistry | None = None,
    analysis_id: str | None = None,
) -> MasterplanMetrics:
    """Compute the full masterplan metrics set (Phase 9 §9.1.2; pure, no I/O).

    ``indicators`` maps the EXACT Phase 8 canonical indicator names
    (``plot_planning.INDICATOR_NAMES``) to extracted values. A missing parking
    indicator yields ``demand: "unknown"`` + an :class:`UnknownItem` — never a default
    (§0v2.4). Legal values (WT §39 statutory PBC, §40 plac zabaw brackets + PBC credit
    share) are read from ``registry``, never hardcoded.
    """
    cfg = config or CapacityConfig()
    parcel_area = float(parcel_geom.area)
    unknowns: list[UnknownItem] = []
    warnings: list[str] = []

    per_building = [building_metrics(b, cfg) for b in proposal.buildings]

    # Footprint overlap across DIFFERENT buildings is a drawing error (overlapping
    # wings are a within-building stacking idiom; whole buildings are not) → recorded
    # as a metrics WARNING, never a hard violation (documented on MasterplanMetrics).
    building_footprints = [(b.name, b.footprint_geometry()) for b in proposal.buildings]
    for i in range(len(building_footprints)):
        for j in range(i + 1, len(building_footprints)):
            inter = building_footprints[i][1].intersection(building_footprints[j][1])
            if not inter.is_empty and float(inter.area) > _OVERLAP_EPS_M2:
                warnings.append(
                    f"building footprints overlap: '{building_footprints[i][0]}' and "
                    f"'{building_footprints[j][0]}' share {float(inter.area):.2f} m² — "
                    "overlapping DISTINCT buildings is a drawing error (powierzchnia "
                    "zabudowy uses the union; PC/PUM may be misattributed)"
                )

    # ---- totals (footprint union avoids double counting overlapping buildings) ---- #
    footprint_union_m2 = float(proposal.buildings_footprint_geometry().area)
    pc_nadziemna = sum(b.pc_nadziemna_m2 for b in per_building)
    pum = sum(b.pum_m2 for b in per_building)
    puu = sum(b.puu_m2 for b in per_building)
    mieszkania = sum(b.mieszkania_estimate for b in per_building)
    coverage_ratio = footprint_union_m2 / parcel_area if parcel_area > 0 else 0.0
    intensity = pc_nadziemna / parcel_area if parcel_area > 0 else 0.0
    totals: dict[str, Any] = {
        "buildings": len(per_building),
        "parcel_area_m2": round(parcel_area, 2),
        "powierzchnia_zabudowy_m2": round(footprint_union_m2, 2),
        "pc_nadziemna_m2": round(pc_nadziemna, 2),
        "gfa_m2": round(sum(b.gfa_m2 for b in per_building), 2),
        "pum_m2": round(pum, 2),
        "puu_m2": round(puu, 2),
        "pu_m2": round(pum + puu, 2),
        "mieszkania_estimate": mieszkania,
        "coverage_ratio": round(coverage_ratio, 4),
        "intensywnosc": round(intensity, 4),
        "basis": {
            "powierzchnia_zabudowy_m2": BASIS_GEOMETRY,
            "coverage_ratio": BASIS_GEOMETRY,
            "intensywnosc": BASIS_GEOMETRY,
            "pum_m2": BASIS_HEURISTIC,
            "puu_m2": BASIS_HEURISTIC,
            "pu_m2": BASIS_HEURISTIC,
            "mieszkania_estimate": BASIS_HEURISTIC,
        },
    }

    # ---- per-stage table (ROBYG exemplar: Liczba mieszkań / PUM / PUU / PU + SUMA) -- #
    stages = sorted({b.stage for b in per_building if b.stage is not None})
    stage_table: list[dict[str, Any]] = []

    def _stage_row(label: Any, rows: list[BuildingMetrics]) -> dict[str, Any]:
        return {
            "etap": label,
            "liczba_mieszkan": sum(r.mieszkania_estimate for r in rows),
            "pum_m2": round(sum(r.pum_m2 for r in rows), 2),
            "puu_m2": round(sum(r.puu_m2 for r in rows), 2),
            "pu_m2": round(sum(r.pu_m2 for r in rows), 2),
        }

    for stage in stages:
        stage_table.append(_stage_row(stage, [b for b in per_building if b.stage == stage]))
    unstaged = [b for b in per_building if b.stage is None]
    if unstaged:
        stage_table.append(_stage_row("poza etapami", unstaged))
    stage_table.append(_stage_row("SUMA", per_building))

    # ---- PBC balance: greenery ∪ playground·credit (credit READ from wt-40) -------- #
    greenery_geom = proposal.greenery_geometry()
    playground_geom = proposal.playgrounds_geometry()
    greenery_m2 = float(greenery_geom.area)
    playground_m2 = float(playground_geom.area)
    credit_share, wt40_ref = _threshold_from_registry(
        registry, WT40_RULE_ID, WT40_PBC_SHARE_THRESHOLD
    )
    if credit_share is None:
        playground_credit_m2: float | str = "unknown"
        pbc_m2: float | str = "unknown"
        pbc_ratio: float | str = "unknown"
        unknowns.append(
            _unknown(
                topic="pbc_playground_credit",
                reason="ruleset_value_unavailable",
                action=(
                    f"Załadować ruleset {WT40_RULE_ID} (threshold {WT40_PBC_SHARE_THRESHOLD}) "
                    "— udział placu zabaw w PBC nie może być zgadywany."
                ),
                analysis_id=analysis_id,
            )
        )
    else:
        # Avoid double counting where the playground overlaps greenery.
        extra_playground = float(playground_geom.difference(greenery_geom).area)
        playground_credit_m2 = round(extra_playground * credit_share, 2)
        pbc_m2 = round(greenery_m2 + extra_playground * credit_share, 2)
        pbc_ratio = round(pbc_m2 / parcel_area, 4) if parcel_area > 0 else 0.0

    required_pbc: float | str
    pbc_basis: str
    pbc_source: str | None
    if indicators.get(IND_MIN_PBC) is not None:
        required_pbc = float(indicators[IND_MIN_PBC])
        pbc_basis = BASIS_INDICATOR
        pbc_source = IND_MIN_PBC
    else:
        statutory, wt39_ref = _threshold_from_registry(
            registry, WT39_RULE_ID, WT39_STATUTORY_THRESHOLD
        )
        has_residential = any(b.pc_mieszkalna_m2 > 0 for b in per_building)
        if statutory is not None and has_residential:
            required_pbc = statutory
            pbc_basis = BASIS_RULESET
            pbc_source = wt39_ref
        else:
            required_pbc = "unknown"
            pbc_basis = "unknown"
            pbc_source = None
            unknowns.append(
                _unknown(
                    topic="min_pbc_ratio",
                    reason="indicator_not_found",
                    action=(
                        "Ustalić minimalny udział PBC z MPZP/WZ (wskaźnik min_pbc_ratio) "
                        "albo załadować ruleset WT §39."
                    ),
                    analysis_id=analysis_id,
                )
            )
    pbc = {
        "greenery_m2": round(greenery_m2, 2),
        "playground_m2": round(playground_m2, 2),
        "playground_credit_share": credit_share if credit_share is not None else "unknown",
        "playground_credit_share_source": wt40_ref,
        "playground_pbc_credit_m2": playground_credit_m2,
        "pbc_m2": pbc_m2,
        "pbc_ratio": pbc_ratio,
        "required_pbc_ratio": required_pbc,
        "required_pbc_basis": pbc_basis,
        "required_pbc_source": pbc_source,
        "within": (
            bool(pbc_ratio >= required_pbc)
            if isinstance(pbc_ratio, float) and isinstance(required_pbc, float)
            else "unknown"
        ),
        "basis": {"greenery_m2": BASIS_GEOMETRY, "playground_m2": BASIS_GEOMETRY},
    }

    # ---- parking demand vs supply (MPZP indicators ONLY — never a default) --------- #
    supply = sum(int(p.spaces) for p in proposal.parking)
    rate_dwelling = indicators.get(IND_PARKING_PER_MIESZKANIE)
    rate_services = indicators.get(IND_PARKING_PER_100M2_USLUG)
    demand: float | str
    missing: list[str] = []
    if mieszkania > 0 and rate_dwelling is None:
        missing.append(IND_PARKING_PER_MIESZKANIE)
    if puu > 0 and rate_services is None:
        missing.append(IND_PARKING_PER_100M2_USLUG)
    if missing:
        demand = "unknown"
        unknowns.append(
            _unknown(
                topic="parking_demand",
                reason="indicator_not_found",
                action=(
                    "Zapytać gminę / odczytać z MPZP-WZ wskaźniki parkingowe "
                    f"({', '.join(missing)}) — brak normy krajowej, wartość nigdy nie "
                    "jest domyślna (§0v2.4)."
                ),
                analysis_id=analysis_id,
            )
        )
    else:
        demand = round(
            (float(rate_dwelling) * mieszkania if rate_dwelling is not None else 0.0)
            + (float(rate_services) * puu / 100.0 if rate_services is not None else 0.0),
            2,
        )
    parking = {
        "demand_spaces": demand,
        "demand_basis": BASIS_INDICATOR if not missing else "unknown",
        "missing_indicators": missing,
        "supply_spaces": supply,
        "supply_basis": BASIS_GEOMETRY,
        "balance": round(supply - demand, 2) if isinstance(demand, float) else "unknown",
        "within": bool(supply >= demand) if isinstance(demand, float) else "unknown",
    }

    # ---- plac zabaw demand bracket (WT §40 select via the rules engine) ------------ #
    required_play, play_check = _playground_requirement(
        registry,
        total_mieszkania=mieszkania,
        playground_area_m2=playground_m2,
        zabudowa_srodmiejska=bool(proposal.zabudowa_srodmiejska),
    )
    if required_play is None:
        unknowns.append(
            _unknown(
                topic="plac_zabaw_required_area",
                reason="ruleset_value_unavailable",
                action=f"Załadować ruleset {WT40_RULE_ID} — widełki §40 czyta silnik reguł.",
                analysis_id=analysis_id,
            )
        )
    playground = {
        "provided_m2": round(playground_m2, 2),
        "required_m2": required_play if required_play is not None else "unknown",
        "required_basis": BASIS_RULESET if required_play is not None else "unknown",
        "required_source": play_check.source_reference if play_check else None,
        "within": (
            bool(playground_m2 >= required_play) if required_play is not None else "unknown"
        ),
        "total_mieszkania": mieszkania,
        "rule_check": (
            {"rule_id": play_check.rule_id, "status": play_check.status.value,
             "message": play_check.message}
            if play_check
            else None
        ),
    }

    # ---- indicator limit checks vs totals (max_intensity / max_coverage_ratio) ----- #
    indicator_checks: list[dict[str, Any]] = []
    for name, value in (
        (IND_MAX_COVERAGE, coverage_ratio),
        (IND_MAX_INTENSITY, intensity),
    ):
        limit = indicators.get(name)
        indicator_checks.append(
            {
                "indicator": name,
                "limit": float(limit) if limit is not None else "unknown",
                "value": round(value, 4),
                "within": bool(value <= float(limit)) if limit is not None else "unknown",
                "basis": BASIS_INDICATOR if limit is not None else "unknown",
            }
        )

    return MasterplanMetrics(
        per_building=per_building,
        totals=totals,
        stage_table=stage_table,
        pbc=pbc,
        parking=parking,
        playground=playground,
        indicator_checks=indicator_checks,
        unknowns=unknowns,
        config_basis=cfg.basis_block(),
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# No-drawing capacity scenarios (F-0202–0206) — behind capacity_generate_scenarios
# --------------------------------------------------------------------------- #
# Scenario shaping factors: HEURISTICS (utilization of the buildable envelope, floor
# fraction, PUM-efficiency delta), not legal values. Ordered so PUM strictly grows
# conservative < base < optimistic < max.
SCENARIO_FACTORS: dict[str, dict[str, float]] = {
    "conservative": {"envelope_utilization": 0.40, "floors_fraction": 0.75, "efficiency_delta": -0.05},
    "base": {"envelope_utilization": 0.55, "floors_fraction": 1.0, "efficiency_delta": 0.0},
    "optimistic": {"envelope_utilization": 0.70, "floors_fraction": 1.0, "efficiency_delta": 0.03},
    "max": {"envelope_utilization": 0.85, "floors_fraction": 1.0, "efficiency_delta": 0.05},
}
#: Sensitivity span for the PUM efficiency factor (the documented ±5 p.p., §0v2.2).
SENSITIVITY_EFFICIENCY_PP = 0.05


@dataclass
class CapacityScenarioSet:
    """Result of :func:`generate_capacity_scenarios` (scenarios + sensitivity + unknowns)."""

    scenarios: list[CapacityScenario]
    sensitivity: dict[str, Any]
    unknowns: list[UnknownItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": [s.model_dump(mode="json") for s in self.scenarios],
            "sensitivity": self.sensitivity,
            "unknowns": [u.model_dump(mode="json") for u in self.unknowns],
        }


def _allowed_floors(
    indicators: Mapping[str, Any], cfg: CapacityConfig
) -> tuple[int | None, list[str]]:
    """Max floors from indicators: min(max_kondygnacje, max_height_m/floor_height)."""
    citations: list[str] = []
    candidates: list[int] = []
    if indicators.get(IND_MAX_KONDYGNACJE) is not None:
        candidates.append(int(indicators[IND_MAX_KONDYGNACJE]))
        citations.append(f"{IND_MAX_KONDYGNACJE}={indicators[IND_MAX_KONDYGNACJE]}")
    if indicators.get(IND_MAX_HEIGHT_M) is not None:
        derived = int(float(indicators[IND_MAX_HEIGHT_M]) // cfg.floor_height_m)
        candidates.append(max(1, derived))
        citations.append(
            f"{IND_MAX_HEIGHT_M}={indicators[IND_MAX_HEIGHT_M]} / floor_height_m="
            f"{cfg.floor_height_m} -> {max(1, derived)} kondygnacje"
        )
    if not candidates:
        return None, citations
    return min(candidates), citations


def generate_capacity_scenarios(
    *,
    envelope_area_m2: float,
    parcel_area_m2: float,
    indicators: Mapping[str, Any],
    config: CapacityConfig | None = None,
    analysis_id: str | None = None,
) -> CapacityScenarioSet:
    """Conservative/base/optimistic/max scenarios from envelope + indicators (F-0202–0205).

    No drawing required: each scenario varies the envelope-coverage utilization, the
    floor count (up to ``max_kondygnacje`` / ``max_height_m``) and the efficiency
    factors; each carries citations of WHICH indicator bound it. Missing indicators →
    the scenario is marked ``partial`` and the gap is an :class:`UnknownItem` (never a
    default, §0v2.4). Sensitivity (F-0206): PUM range for ±5 p.p. on ``pum_efficiency``.
    """
    cfg = config or CapacityConfig()
    unknowns: list[UnknownItem] = []
    floors_allowed, floor_citations = _allowed_floors(indicators, cfg)
    if floors_allowed is None:
        unknowns.append(
            _unknown(
                topic="max_kondygnacje/max_height_m",
                reason="indicator_not_found",
                action=(
                    "Ustalić maksymalną liczbę kondygnacji lub wysokość z MPZP/WZ — bez "
                    "nich chłonność kubaturowa pozostaje częściowa."
                ),
                analysis_id=analysis_id,
            )
        )
    rate_dwelling = indicators.get(IND_PARKING_PER_MIESZKANIE)
    if rate_dwelling is None:
        unknowns.append(
            _unknown(
                topic="parking_demand",
                reason="indicator_not_found",
                action=(
                    f"Zapytać gminę o wskaźnik {IND_PARKING_PER_MIESZKANIE} (MPZP/WZ) — "
                    "brak normy krajowej, wartość nigdy nie jest domyślna (§0v2.4)."
                ),
                analysis_id=analysis_id,
            )
        )

    max_coverage = indicators.get(IND_MAX_COVERAGE)
    max_intensity = indicators.get(IND_MAX_INTENSITY)

    scenarios: list[CapacityScenario] = []
    base_pum: float | None = None
    for scenario_type, factors in SCENARIO_FACTORS.items():
        bounded_by = list(floor_citations)
        footprint = envelope_area_m2 * factors["envelope_utilization"]
        if max_coverage is not None:
            cap = parcel_area_m2 * float(max_coverage)
            if footprint > cap:
                footprint = cap
                bounded_by.append(f"{IND_MAX_COVERAGE}={max_coverage} caps footprint at {cap:.1f} m2")
        efficiency = cfg.pum_efficiency + factors["efficiency_delta"]

        metrics: dict[str, Any] = {
            "envelope_area_m2": round(envelope_area_m2, 2),
            "envelope_utilization": factors["envelope_utilization"],
            "footprint_m2": round(footprint, 2),
            "pum_efficiency_used": round(efficiency, 4),
            "bounded_by": bounded_by,
            "status": "computed",
            **cfg.basis_block(),
            "assumption": "program mieszkalny wielorodzinny (PC nadziemna -> PUM)",
        }

        if floors_allowed is not None:
            floors = max(1, math.floor(floors_allowed * factors["floors_fraction"]))
            pc = footprint * floors
            if max_intensity is not None:
                cap_pc = parcel_area_m2 * float(max_intensity)
                if pc > cap_pc:
                    pc = cap_pc
                    bounded_by.append(
                        f"{IND_MAX_INTENSITY}={max_intensity} caps PC at {cap_pc:.1f} m2"
                    )
            pum = pc * efficiency
            mieszkania = int(pum // cfg.avg_mieszkanie_m2)
            metrics.update(
                {
                    "floors": floors,
                    "pc_nadziemna_m2": round(pc, 2),
                    "gfa_m2": round(pc, 2),
                    "pum_m2": round(pum, 2),
                    "mieszkania_estimate": mieszkania,
                }
            )
            if rate_dwelling is not None:
                metrics["parking_demand_spaces"] = round(float(rate_dwelling) * mieszkania, 2)
                metrics["parking_demand_basis"] = BASIS_INDICATOR
            else:
                metrics["parking_demand_spaces"] = "unknown"
                metrics["parking_demand_basis"] = "unknown"
            if scenario_type == "base":
                base_pum = pum
        else:
            metrics["status"] = "partial"
            metrics["floors"] = "unknown"
            metrics["pum_m2"] = "unknown"

        scenarios.append(
            CapacityScenario(
                id=f"cap:{uuid.uuid4().hex[:8]}",
                analysis_id=analysis_id,
                scenario_type=scenario_type,
                metrics=metrics,
            )
        )

    # Sensitivity (F-0206): ±5 p.p. PUM-efficiency band around the base scenario.
    sensitivity: dict[str, Any] = {
        "parameter": "pum_efficiency",
        "delta_pp": SENSITIVITY_EFFICIENCY_PP,
        "basis": BASIS_HEURISTIC,
    }
    if base_pum is not None and cfg.pum_efficiency > 0:
        low = base_pum * (cfg.pum_efficiency - SENSITIVITY_EFFICIENCY_PP) / cfg.pum_efficiency
        high = base_pum * (cfg.pum_efficiency + SENSITIVITY_EFFICIENCY_PP) / cfg.pum_efficiency
        sensitivity["pum_range_m2"] = [round(low, 2), round(high, 2)]
    else:
        sensitivity["pum_range_m2"] = "unknown"

    return CapacityScenarioSet(scenarios=scenarios, sensitivity=sensitivity, unknowns=unknowns)
