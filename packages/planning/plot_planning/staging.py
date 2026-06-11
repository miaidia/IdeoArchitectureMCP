"""Etapowanie consistency checks (Phase 11 §11.1.5 / part A Task 4).

:func:`check_staging` verifies that every construction stage of a masterplan is
independently serviceable, CUMULATIVELY through stage N:

(a) **road access** — some road element reaches every stage-N building (simple
    touch/overlap test against the buffered road corridors available by stage N);
(b) **parking balance** — cumulative supply ≥ cumulative demand IF the MPZP parking
    indicators are present, else ``unknown`` (a missing indicator is NEVER a pass —
    §0v2.4 no national defaults);
(c) **plac zabaw** — available by the stage whose cumulative mieszkania first trigger
    the WT §40 obligation. The trigger and area brackets are resolved through the
    rules ENGINE against the ``PL-WT-40-PLAC-ZABAW-001`` ruleset (never a literal
    "20" in code — anti-pattern guard, plan §11.4);
(d) **stage dependency** — no stage-N building strictly dependent on a stage-N+1
    parking hall (heuristic: a hala whose ``serves_buildings`` includes a building
    of an EARLIER stage than the hala itself → warning).

All outcomes are SOFT (:class:`StageCheck` with ``severity="soft"``): they feed the
masterplan loop critique (part B) and travel as ``staging_checks`` in the
``propose_layout`` structuredContent — they never become hard violations (§14.2
hard-blocker dominance stays reserved for legal/geometric blockers).

Elements without a declared stage are handled with RECORDED assumptions (never
silent): an unstaged road/playground is assumed available from the first stage; an
unstaged parking element is assumed realised with the earliest building it serves
(else the first stage). An unstaged building is assumed required from the first
stage (conservative: it must be serviceable from the start).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from plot_rules import RulesetRegistry, RuleStatus
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry.base import BaseGeometry

from plot_planning.capacity import (
    IND_PARKING_PER_100M2_USLUG,
    IND_PARKING_PER_MIESZKANIE,
    WT40_RULE_ID,
    MasterplanMetrics,
    _playground_requirement,
)

#: Touch/overlap tolerance for the stage road-access test (metres; design-practice
#: value — a building within this distance of a road corridor counts as reached).
DEFAULT_ROAD_TOUCH_TOLERANCE_M = 5.0


# --------------------------------------------------------------------------- #
# Structural protocols (duck-typed against the plot_agent masterplan DSL —
# plot_planning must not import plot_agent, §9.4 dependency direction).
# --------------------------------------------------------------------------- #
class _BuildingLike(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def stage(self) -> int | None: ...
    @property
    def status(self) -> str: ...
    def footprint_geometry(self) -> BaseGeometry: ...


class _RoadLike(Protocol):
    @property
    def stage(self) -> int | None: ...
    def to_polygon(self) -> BaseGeometry: ...


class _ParkingLike(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def spaces(self) -> int: ...
    @property
    def serves_buildings(self) -> list[str]: ...
    @property
    def stage(self) -> int | None: ...


class StagedMasterplanLike(Protocol):
    """The subset of the masterplan DSL v2 the staging checks read."""

    @property
    def buildings(self) -> Sequence[_BuildingLike]: ...
    @property
    def roads(self) -> Sequence[_RoadLike]: ...
    @property
    def parking(self) -> Sequence[_ParkingLike]: ...
    @property
    def playgrounds(self) -> list[dict[str, Any]]: ...
    @property
    def zabudowa_srodmiejska(self) -> bool: ...


class StageCheck(BaseModel):
    """One staging-consistency outcome (RuleCheck-like, always SOFT)."""

    model_config = ConfigDict(extra="forbid")

    stage: int = Field(description="Stage N the cumulative check covers.")
    check: str = Field(
        description="road_access | parking_balance | plac_zabaw | stage_dependency."
    )
    status: RuleStatus = Field(description="pass/warning/unknown/not_applicable.")
    severity: str = Field(default="soft", description="Always soft (plan §11.1.5).")
    message: str = Field(default="")
    rule_id: str | None = Field(
        default=None, description="Ruleset id when a legal rule backs the check (§40)."
    )
    source_reference: str | None = Field(default=None)
    evidence: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Stage attribution helpers (assumptions RECORDED in evidence, never silent)
# --------------------------------------------------------------------------- #
def _building_stage(b: _BuildingLike, first_stage: int) -> tuple[int, bool]:
    if b.stage is not None:
        return int(b.stage), False
    return first_stage, True


def _parking_stage(
    p: _ParkingLike, stage_by_building: Mapping[str, int], first_stage: int
) -> tuple[int, bool]:
    if p.stage is not None:
        return int(p.stage), False
    served = [stage_by_building[n] for n in p.serves_buildings if n in stage_by_building]
    if served:
        return min(served), True
    return first_stage, True


def _playground_stage(entry: Mapping[str, Any], first_stage: int) -> tuple[int, bool]:
    """Stage of a playground polygon: GeoJSON Feature ``properties.stage`` or assumed."""
    if entry.get("type") == "Feature":
        stage = (entry.get("properties") or {}).get("stage")
        if stage is not None:
            return int(stage), False
    return first_stage, True


def _playground_area(entry: Mapping[str, Any]) -> float:
    from shapely.geometry import shape

    geom = entry.get("geometry") if entry.get("type") == "Feature" else entry
    return float(shape(geom).area)


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #
def check_staging(
    proposal: StagedMasterplanLike,
    metrics: MasterplanMetrics,
    registry: RulesetRegistry,
    *,
    indicators: Mapping[str, Any] | None = None,
    road_touch_tolerance_m: float = DEFAULT_ROAD_TOUCH_TOLERANCE_M,
) -> list[StageCheck]:
    """Run all stage-consistency checks over a staged masterplan (pure, no I/O).

    ``metrics`` are the already-computed Phase 9 capacity metrics (REUSED, not
    recomputed — per-building mieszkania/PUU drive the cumulative balances);
    ``indicators`` is the Phase 8 canonical indicator map (parking rates). A proposal
    with NO staged building returns ``[]`` — there is no etapowanie to check.
    """
    indicators = indicators or {}
    declared = sorted({int(b.stage) for b in proposal.buildings if b.stage is not None})
    if not declared:
        return []
    first_stage = declared[0]

    stage_by_building: dict[str, int] = {}
    assumed_buildings: list[str] = []
    for b in proposal.buildings:
        stage, assumed = _building_stage(b, first_stage)
        stage_by_building[b.name] = stage
        if assumed:
            assumed_buildings.append(b.name)

    metrics_by_name = {m.name: m for m in metrics.per_building}
    checks: list[StageCheck] = []

    for stage_n in declared:
        cum_names = [n for n, s in stage_by_building.items() if s <= stage_n]
        evidence_base: dict[str, Any] = {"cumulative_buildings": sorted(cum_names)}
        if assumed_buildings:
            evidence_base["assumed_stage_buildings"] = sorted(assumed_buildings)

        checks.append(
            _road_access_check(
                proposal, stage_n, cum_names,
                tolerance_m=road_touch_tolerance_m, evidence_base=evidence_base,
            )
        )
        checks.append(
            _parking_balance_check(
                proposal, stage_n, cum_names, stage_by_building, metrics_by_name,
                indicators, first_stage=first_stage, evidence_base=evidence_base,
            )
        )
        checks.append(
            _playground_check(
                proposal, registry, stage_n, cum_names, metrics_by_name, first_stage,
                evidence_base=evidence_base,
            )
        )

    checks.extend(_stage_dependency_checks(proposal, stage_by_building, first_stage))
    checks.sort(key=lambda c: (c.stage, c.check, c.message))
    return checks


# --------------------------------------------------------------------------- #
# (a) road access
# --------------------------------------------------------------------------- #
def _road_access_check(
    proposal: StagedMasterplanLike,
    stage_n: int,
    cum_names: list[str],
    *,
    tolerance_m: float,
    evidence_base: dict[str, Any],
) -> StageCheck:
    corridors: list[BaseGeometry] = []
    assumed_roads = 0
    for r in proposal.roads:
        road_stage = int(r.stage) if r.stage is not None else None
        if road_stage is None:
            assumed_roads += 1
        if road_stage is None or road_stage <= stage_n:
            corridors.append(r.to_polygon())
    evidence = {
        **evidence_base,
        "roads_available": len(corridors),
        "tolerance_m": tolerance_m,
    }
    if assumed_roads:
        evidence["assumed_stage_roads"] = assumed_roads
    if not corridors:
        return StageCheck(
            stage=stage_n,
            check="road_access",
            status=RuleStatus.WARNING,
            message=(
                f"etap {stage_n}: brak drogi wewnętrznej dostępnej do etapu {stage_n} "
                "— budynki etapu nie mają obsługi komunikacyjnej"
            ),
            evidence=evidence,
        )
    unreached: list[str] = []
    for b in proposal.buildings:
        if b.name not in cum_names:
            continue
        footprint = b.footprint_geometry()
        if not any(footprint.distance(c) <= tolerance_m for c in corridors):
            unreached.append(b.name)
    if unreached:
        return StageCheck(
            stage=stage_n,
            check="road_access",
            status=RuleStatus.WARNING,
            message=(
                f"etap {stage_n}: budynki bez dojazdu (żaden element drogowy nie "
                f"dochodzi bliżej niż {tolerance_m:g} m): {sorted(unreached)}"
            ),
            evidence={**evidence, "unreached_buildings": sorted(unreached)},
        )
    return StageCheck(
        stage=stage_n,
        check="road_access",
        status=RuleStatus.PASS,
        message=f"etap {stage_n}: każdy budynek etapu styka się z układem drogowym",
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# (b) parking balance
# --------------------------------------------------------------------------- #
def _parking_balance_check(
    proposal: StagedMasterplanLike,
    stage_n: int,
    cum_names: list[str],
    stage_by_building: Mapping[str, int],
    metrics_by_name: Mapping[str, Any],
    indicators: Mapping[str, Any],
    *,
    first_stage: int,
    evidence_base: dict[str, Any],
) -> StageCheck:
    cum_mieszkania = sum(
        int(metrics_by_name[n].mieszkania_estimate) for n in cum_names if n in metrics_by_name
    )
    cum_puu = sum(
        float(metrics_by_name[n].puu_m2) for n in cum_names if n in metrics_by_name
    )
    rate_dwelling = indicators.get(IND_PARKING_PER_MIESZKANIE)
    rate_services = indicators.get(IND_PARKING_PER_100M2_USLUG)
    missing: list[str] = []
    if cum_mieszkania > 0 and rate_dwelling is None:
        missing.append(IND_PARKING_PER_MIESZKANIE)
    if cum_puu > 0 and rate_services is None:
        missing.append(IND_PARKING_PER_100M2_USLUG)
    supply = 0
    assumed_parking: list[int] = []
    for i, p in enumerate(proposal.parking):
        p_stage, assumed = _parking_stage(p, stage_by_building, first_stage)
        if assumed:
            assumed_parking.append(i)
        if p_stage <= stage_n:
            supply += int(p.spaces)
    evidence = {
        **evidence_base,
        "cumulative_mieszkania": cum_mieszkania,
        "cumulative_puu_m2": round(cum_puu, 2),
        "supply_spaces": supply,
    }
    if assumed_parking:
        evidence["assumed_stage_parking"] = assumed_parking
    if missing:
        return StageCheck(
            stage=stage_n,
            check="parking_balance",
            status=RuleStatus.UNKNOWN,
            message=(
                f"etap {stage_n}: bilans postojowy nieweryfikowalny — brak wskaźników "
                f"MPZP/WZ: {missing} (wartość nigdy nie jest domyślna, §0v2.4)"
            ),
            evidence={**evidence, "missing_indicators": missing},
        )
    demand = (float(rate_dwelling) * cum_mieszkania if rate_dwelling is not None else 0.0) + (
        float(rate_services) * cum_puu / 100.0 if rate_services is not None else 0.0
    )
    demand = round(demand, 2)
    evidence["demand_spaces"] = demand
    if supply + 1e-9 < demand:
        return StageCheck(
            stage=stage_n,
            check="parking_balance",
            status=RuleStatus.WARNING,
            message=(
                f"etap {stage_n}: niedobór miejsc postojowych — podaż {supply} < "
                f"popyt {demand:g} (kumulatywnie, wg wskaźników MPZP)"
            ),
            evidence=evidence,
        )
    return StageCheck(
        stage=stage_n,
        check="parking_balance",
        status=RuleStatus.PASS,
        message=(
            f"etap {stage_n}: bilans postojowy domknięty — podaż {supply} ≥ popyt {demand:g}"
        ),
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# (c) plac zabaw (WT §40 via the rules engine — no literal trigger in code)
# --------------------------------------------------------------------------- #
def _playground_check(
    proposal: StagedMasterplanLike,
    registry: RulesetRegistry,
    stage_n: int,
    cum_names: list[str],
    metrics_by_name: Mapping[str, Any],
    first_stage: int,
    *,
    evidence_base: dict[str, Any],
) -> StageCheck:
    cum_mieszkania = sum(
        int(metrics_by_name[n].mieszkania_estimate) for n in cum_names if n in metrics_by_name
    )
    cum_play_m2 = 0.0
    assumed_playgrounds = 0
    for entry in proposal.playgrounds:
        p_stage, assumed = _playground_stage(entry, first_stage)
        if assumed:
            assumed_playgrounds += 1
        if p_stage <= stage_n:
            cum_play_m2 += _playground_area(entry)
    required, rule_check = _playground_requirement(
        registry,
        total_mieszkania=cum_mieszkania,
        playground_area_m2=cum_play_m2,
        zabudowa_srodmiejska=bool(proposal.zabudowa_srodmiejska),
    )
    evidence = {
        **evidence_base,
        "cumulative_mieszkania": cum_mieszkania,
        "cumulative_playground_m2": round(cum_play_m2, 2),
    }
    if assumed_playgrounds:
        evidence["assumed_stage_playgrounds"] = assumed_playgrounds
    source_ref = rule_check.source_reference if rule_check else None
    if required is None:
        return StageCheck(
            stage=stage_n,
            check="plac_zabaw",
            status=RuleStatus.UNKNOWN,
            message=(
                f"etap {stage_n}: nie można rozstrzygnąć obowiązku placu zabaw — "
                f"ruleset {WT40_RULE_ID} niedostępny"
            ),
            rule_id=WT40_RULE_ID,
            source_reference=source_ref,
            evidence=evidence,
        )
    evidence["required_playground_m2"] = required
    if required > 0 and cum_play_m2 + 1e-9 < required:
        return StageCheck(
            stage=stage_n,
            check="plac_zabaw",
            status=RuleStatus.WARNING,
            message=(
                f"etap {stage_n}: skumulowane mieszkania ({cum_mieszkania}) uruchamiają "
                f"obowiązek placu zabaw wg {WT40_RULE_ID} — wymagane {required:g} m², "
                f"dostępne {cum_play_m2:g} m² do tego etapu"
            ),
            rule_id=WT40_RULE_ID,
            source_reference=source_ref,
            evidence=evidence,
        )
    if required > 0:
        message = (
            f"etap {stage_n}: plac zabaw dostępny ({cum_play_m2:g} m² ≥ {required:g} m² "
            f"wg {WT40_RULE_ID})"
        )
    else:
        message = (
            f"etap {stage_n}: obowiązek placu zabaw jeszcze nie powstał "
            f"(silnik reguł, {WT40_RULE_ID})"
        )
    return StageCheck(
        stage=stage_n,
        check="plac_zabaw",
        status=RuleStatus.PASS,
        message=message,
        rule_id=WT40_RULE_ID,
        source_reference=source_ref,
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# (d) stage dependency (parking hall later than a building it serves)
# --------------------------------------------------------------------------- #
def _stage_dependency_checks(
    proposal: StagedMasterplanLike,
    stage_by_building: Mapping[str, int],
    first_stage: int,
) -> list[StageCheck]:
    out: list[StageCheck] = []
    for i, p in enumerate(proposal.parking):
        if not p.serves_buildings:
            continue
        p_stage, assumed = _parking_stage(p, dict(stage_by_building), first_stage)
        late_served = sorted(
            n
            for n in p.serves_buildings
            if n in stage_by_building and stage_by_building[n] < p_stage
        )
        evidence: dict[str, Any] = {
            "parking_index": i,
            "parking_kind": str(p.kind),
            "parking_stage": p_stage,
            "serves_buildings": list(p.serves_buildings),
        }
        if assumed:
            evidence["assumed_stage"] = True
        if late_served:
            pairs = ", ".join(
                f"{n} (etap {stage_by_building[n]})" for n in late_served
            )
            out.append(
                StageCheck(
                    stage=p_stage,
                    check="stage_dependency",
                    status=RuleStatus.WARNING,
                    message=(
                        f"parking[{i}] ({p.kind}, etap {p_stage}) obsługuje budynki "
                        f"wcześniejszych etapów: {pairs} — budynek nie może zależeć od "
                        "parkingu z późniejszego etapu"
                    ),
                    evidence={**evidence, "late_served_buildings": late_served},
                )
            )
        else:
            out.append(
                StageCheck(
                    stage=p_stage,
                    check="stage_dependency",
                    status=RuleStatus.PASS,
                    message=(
                        f"parking[{i}] ({p.kind}, etap {p_stage}) dostępny dla wszystkich "
                        "obsługiwanych budynków w ich etapach"
                    ),
                    evidence=evidence,
                )
            )
    return out
