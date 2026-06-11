"""WT §39 (PBC) + §40 (plac zabaw) validators (plan §10.1.5).

REUSE rule (plan): the PBC balance and the dwelling count come from the Phase 9
capacity engine (:func:`plot_planning.masterplan_metrics`) — they are NOT
recomputed here; this module turns the balances into proper RuleChecks via the
rules engine.

§39 (``PL-WT-39-PBC-001``): inputs are the metrics' ``pbc_ratio`` (greenery ∪
playground·credit, credit share itself read from wt-40 by the capacity engine)
and the MPZP ``min_pbc_ratio`` indicator when extracted (``mpzp_pbc_known`` /
``mpzp_min_pbc_ratio``) — otherwise the rule applies its statutory 25% check.

§40 (``PL-WT-40-PLAC-ZABAW-001``): ONE engine evaluation covering all the
rule's checks, with geometric inputs:

* ``total_mieszkania``        — Phase 9 estimate (``mieszkania_estimate``);
* ``playground_area_m2``      — union of the playground polygons;
* ``playground_split`` / ``smallest_part_area_m2`` — ≥2 polygons → split parts;
* ``playground_pbc_share``    — area(playground ∩ greenery) / area(playground)
  (the "≥30% na terenie biologicznie czynnym" input; the 30% lives in YAML);
* ``playground_insolation_hours_equinox`` — the §60 shadow engine sampled on a
  grid over the playground in the rule's 10:00–16:00 window; the value is the
  hours achieved by ≥ ``insolation_area_share`` (50%, from YAML) of the area;
* ``playground_min_distance_m`` — min distance to linia rozgraniczająca
  approximated by the internal road corridors (kdw / pożarowa / pieszojezdnia —
  dojście walkways excluded) and to windowed walls of pomieszczenia na pobyt
  ludzi. **Miejsca gromadzenia odpadów are NOT modeled yet** (no waste DSL
  element): the input covers roads+windows only and the gap is RECORDED in the
  trace/evidence (``waste_distance_component: "not_modeled"``) — a documented
  partial input, never silently complete.

When the metrics' required area is 0 (≤ trigger mieszkań) and no playground is
drawn, an explicit NOT_APPLICABLE check is emitted (the §40 obligation never
arose) instead of evaluating unverifiable sun/PBC inputs on a void playground.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry, RuleStatus
from plot_rules import evaluate as evaluate_rule
from shapely.geometry import mapping as geo_mapping
from shapely.ops import unary_union

from plot_planning.capacity import IND_MIN_PBC, MasterplanMetrics, masterplan_metrics
from plot_planning.wt_validators.config import (
    DECIDED_CONFIDENCE,
    RULE_WT39,
    RULE_WT40,
    decided_entry,
    find_override,
    missing_rule_check,
    rule_threshold,
    with_evidence,
)
from plot_planning.wt_validators.context import ValidationContext
from plot_planning.wt_validators.sun import playground_insolation_hours

#: Road functions approximating "linia rozgraniczajaca ulicy/drogi/ciagu
#: pieszo-jezdnego" (§40 ust. 4); pure pedestrian dojście paths are excluded.
_ROAD_FUNCTIONS_40 = ("kdw", "pozarowa", "pieszojezdnia")


def check_pbc_playground(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    *,
    metrics: MasterplanMetrics | None = None,
    indicators: Mapping[str, Any] | None = None,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
) -> list[RuleCheck]:
    """Evaluate WT §39 (PBC ratio) and §40 (plac zabaw) for the masterplan."""
    indicators = indicators or {}
    if metrics is None:
        # Phase 9 engine REUSED, not re-implemented (plan §10.1.5).
        metrics = masterplan_metrics(
            ctx.proposal, ctx.parcel, indicators, registry=registry
        )
    out: list[RuleCheck] = []
    out.extend(
        _check_wt39(ctx, registry, metrics, indicators, mode, overrides, analysis_id)
    )
    out.extend(_check_wt40(ctx, registry, metrics, mode, overrides, analysis_id))
    return out


# --------------------------------------------------------------------------- #
# §39
# --------------------------------------------------------------------------- #
def _check_wt39(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    metrics: MasterplanMetrics,
    indicators: Mapping[str, Any],
    mode: EvaluationMode | str,
    overrides: OverrideStore | None,
    analysis_id: str | None,
) -> list[RuleCheck]:
    rule = registry.get(RULE_WT39)
    if rule is None:
        return [missing_rule_check(RULE_WT39, mode)]
    override = find_override(overrides, analysis_id, RULE_WT39)

    multifamily = any(b.is_multifamily for b in ctx.buildings)
    pbc_ratio = metrics.pbc.get("pbc_ratio")
    mpzp_value = indicators.get(IND_MIN_PBC)
    inputs: dict[str, Any] = {
        # §39 ust. 1 covers wielorodzinne/opieki zdrowotnej/oświaty; the DSL has
        # no zdrowie/oświata buildings — multifamily presence decides (else the
        # rule is NOT_APPLICABLE through its applies_when lists).
        "building_type": "wielorodzinny" if multifamily else "inne",
        "pbc_ratio": float(pbc_ratio) if isinstance(pbc_ratio, int | float) else None,
        "mpzp_pbc_known": mpzp_value is not None,
        "mpzp_min_pbc_ratio": float(mpzp_value) if mpzp_value is not None else None,
        "is_public_square": False,  # publicly accessible plac not in the DSL yet
    }
    check = evaluate_rule(rule, inputs, mode=mode, override=override)
    entry = decided_entry(check)
    required = entry["target_value"] if entry else "?"
    ratio_txt = (
        f"{float(pbc_ratio):.1%}" if isinstance(pbc_ratio, int | float) else "nieznany"
    )
    return [
        with_evidence(
            check,
            message=(
                f"udzial PBC {ratio_txt} vs wymagane >= {required} "
                f"(zrodlo wymogu: {metrics.pbc.get('required_pbc_basis')}) — WT par. 39"
            ),
            evidence={
                "geometry": (
                    dict(geo_mapping(ctx.greenery)) if not ctx.greenery.is_empty else None
                ),
                "pbc": {
                    k: metrics.pbc.get(k)
                    for k in ("pbc_m2", "pbc_ratio", "required_pbc_ratio", "required_pbc_basis")
                },
            },
        )
    ]


# --------------------------------------------------------------------------- #
# §40
# --------------------------------------------------------------------------- #
def _check_wt40(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    metrics: MasterplanMetrics,
    mode: EvaluationMode | str,
    overrides: OverrideStore | None,
    analysis_id: str | None,
) -> list[RuleCheck]:
    rule = registry.get(RULE_WT40)
    if rule is None:
        return [missing_rule_check(RULE_WT40, mode)]
    override = find_override(overrides, analysis_id, RULE_WT40)
    cfg = ctx.config

    multifamily = any(b.is_multifamily for b in ctx.buildings)
    total_mieszkania = int(metrics.totals.get("mieszkania_estimate", 0))
    playground_union = (
        unary_union(list(ctx.playgrounds)) if ctx.playgrounds else None
    )
    area = float(playground_union.area) if playground_union is not None else 0.0
    required = metrics.playground.get("required_m2")  # Phase 9 already rule-resolved

    if (
        multifamily
        and isinstance(required, int | float)
        and float(required) <= 0.0
        and area <= 0.0
    ):
        # Obligation never arose (≤ trigger mieszkań, ust. 1/8) and nothing was
        # drawn — explicit NOT_APPLICABLE instead of unverifiable void inputs.
        trigger = rule_threshold(rule, "trigger_mieszkania")
        return [
            RuleCheck(
                rule_id=rule.id,
                status=RuleStatus.NOT_APPLICABLE,
                severity=rule.severity,
                message=(
                    f"{rule.id}: {total_mieszkania} mieszkan <= prog "
                    f"{trigger:g} (ruleset) — plac zabaw niewymagany (WT par. 40)"
                ),
                trace={
                    "mode": EvaluationMode(mode).value,
                    "total_mieszkania": total_mieszkania,
                    "required_m2": 0.0,
                    "basis": "ruleset (Phase 9 capacity resolution)",
                },
                source_reference=rule.source_reference,
                confidence=DECIDED_CONFIDENCE,
                geometry_evidence={"geometry": None, "total_mieszkania": total_mieszkania},
            )
        ]

    # ---- geometric inputs ----------------------------------------------------- #
    inputs: dict[str, Any] = {
        "is_multifamily": multifamily,
        "total_mieszkania": float(total_mieszkania),
        "playground_area_m2": area,
        "playground_split": len(ctx.playgrounds) > 1,
        "zabudowa_srodmiejska": ctx.srodmiejska,
    }
    if len(ctx.playgrounds) > 1:
        inputs["smallest_part_area_m2"] = min(float(p.area) for p in ctx.playgrounds)

    waste_note = (
        "miejsca gromadzenia odpadow NIE sa modelowane (brak elementu DSL) — "
        "playground_min_distance_m obejmuje tylko drogi + okna; luka odnotowana"
    )
    if playground_union is not None and area > 0:
        inputs["playground_pbc_share"] = (
            float(playground_union.intersection(ctx.greenery).area) / area
        )
        inputs["playground_insolation_hours_equinox"] = playground_insolation_hours(
            playground_union,
            ctx.all_parts(),
            start_h=rule_threshold(rule, "insolation_window_start_h"),
            end_h=rule_threshold(rule, "insolation_window_end_h"),
            area_share=rule_threshold(rule, "insolation_area_share"),
            config=cfg,
        )
        distance_targets: list[float] = [
            float(playground_union.distance(r.polygon))
            for r in ctx.roads
            if r.function in _ROAD_FUNCTIONS_40
        ]
        distance_targets.extend(
            float(playground_union.distance(w.line))
            for w in ctx.windowed_staly_pobyt_walls()
        )
        if distance_targets:
            inputs["playground_min_distance_m"] = min(distance_targets)
        # No roads AND no windowed walls → input stays absent → engine: unknown
        # (cannot prove the 10 m clearance against nothing — honest gap).

    check = evaluate_rule(rule, inputs, mode=mode, override=override)
    # The required AREA comes from the area check's own trace entry (the binding
    # smallest-margin entry may be a DIFFERENT check, e.g. the PBC share) —
    # falling back to the Phase 9 metrics resolution.
    area_entry = next(
        (
            e
            for e in check.trace.get("checks", [])
            if e.get("name") == "powierzchnia-placu-zabaw" and "target_value" in e
        ),
        None,
    )
    required_msg = area_entry["target_value"] if area_entry else required
    failed_names = [
        str(e.get("name"))
        for e in check.trace.get("checks", [])
        if e.get("status") == "fail"
    ]
    if check.status is RuleStatus.FAIL:
        message = (
            f"zespol {total_mieszkania} mieszkan: plac zabaw {area:.0f} m2 — "
            f"wymagane >= {required_msg} m2; naruszone warunki par. 40: "
            f"{', '.join(failed_names)} (WT par. 40)"
        )
    else:
        message = (
            f"zespol {total_mieszkania} mieszkan: plac zabaw {area:.0f} m2 "
            f"(wymagane {required_msg} m2) — WT par. 40"
        )
    return [
        with_evidence(
            check,
            message=message,
            evidence={
                "geometry": (
                    dict(geo_mapping(playground_union))
                    if playground_union is not None and area > 0
                    else None
                ),
                "total_mieszkania": total_mieszkania,
                "playground_area_m2": round(area, 2),
                "waste_distance_component": "not_modeled",
                "note": waste_note,
            },
            extra_trace={"waste_distance_component": "not_modeled"},
        )
    ]
