"""WT §19/§21 — parking distances + stall plausibility (plan §10.1.4).

Geometric approach: every SURFACE parking element (``kind == "naziemny"``) is
evaluated through ``PL-WT-19-PARKING-DISTANCES-001`` with:

* ``parking_spaces``          — the element's declared stall count (drives the
  rule's 7/10/20 m and 3/6/16 m brackets — values live ONLY in the YAML);
* ``distance_to_windows_m``   — min distance from the parking polygon to any
  windowed wall of pomieszczenia na stały pobyt ludzi AND any plac zabaw
  (§19 ust. 1 lists both); absent targets → input omitted → the engine reports
  ``unknown`` (never a silent pass);
* ``distance_to_boundary_m``  — parking polygon → parcel boundary;
* ``vehicle_type``            — not in the DSL → left to the rule's documented
  ``input_defaults`` (osobowy), recorded as an assumption in the trace.

``hala_podziemna`` / ``wbudowany`` elements are EXEMPT from the §19 distances:
§19 ust. 1 governs "stanowiska postojowe, w tym w garażu otwartym" — i.e.
OPEN-AIR stalls and open garages; enclosed underground halls / built-in garages
are regulated by §102–105 instead (documented scope decision; no checks
emitted for them here).

Stall-count plausibility (§21 context): ``spaces × stall_area_m2`` (config
~25 m²/stall = 2.5×5 m stall per WT §21 + maneuvering, ``basis:
industry_heuristic``) compared to the polygon area → a SOFT WARNING RuleCheck
when the polygon cannot plausibly hold the declared stalls. This is a
heuristic screen, not a legal §21 evaluation — the WT §21 citation is attached
as context only.
"""

from __future__ import annotations

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry, RuleStatus
from plot_rules import evaluate as evaluate_rule
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from plot_planning.wt_validators.config import (
    BASIS_HEURISTIC,
    RULE_WT19,
    RULE_WT21,
    decided_entry,
    find_override,
    missing_rule_check,
    with_evidence,
)
from plot_planning.wt_validators.context import ValidationContext


def check_parking_distances(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    *,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
) -> list[RuleCheck]:
    """Evaluate WT §19 distances + the §21 stall-plausibility warning."""
    surface = [p for p in ctx.parking if p.kind == "naziemny"]
    if not surface:
        # hala_podziemna / wbudowany only → §19 distance regime does not apply
        # (see module docstring); nothing to evaluate.
        return []
    rule = registry.get(RULE_WT19)
    if rule is None:
        return [missing_rule_check(RULE_WT19, mode)]
    cfg = ctx.config

    windowed_walls = ctx.windowed_staly_pobyt_walls()
    playground_union: BaseGeometry | None = (
        unary_union(list(ctx.playgrounds)) if ctx.playgrounds else None
    )

    out: list[RuleCheck] = []
    for parking in surface:
        geom = parking.geometry
        window_targets: list[float] = [
            float(geom.distance(w.line)) for w in windowed_walls
        ]
        if playground_union is not None and not playground_union.is_empty:
            window_targets.append(float(geom.distance(playground_union)))
        distance_windows = min(window_targets) if window_targets else None
        distance_boundary = float(geom.distance(ctx.parcel.boundary))

        inputs: dict[str, object] = {
            "parking_spaces": float(parking.spaces),
            "distance_to_boundary_m": distance_boundary,
            # vehicle_type intentionally absent → the rule's documented default
            # (osobowy) applies and is recorded as an assumption in the trace.
        }
        if distance_windows is not None:
            inputs["distance_to_windows_m"] = distance_windows
        # else: no windowed walls / playgrounds in the plan — the engine reports
        # the window-distance check as unknown (NEVER a silent pass).

        override = find_override(
            overrides, analysis_id, RULE_WT19, subject=f"parking:{parking.index}"
        )
        check = evaluate_rule(rule, inputs, mode=mode, override=override)
        entry = decided_entry(check)
        required = entry["target_value"] if entry else "?"
        label = f"parking naziemny #{parking.index} ({parking.spaces} stanowisk)"
        if check.status is RuleStatus.FAIL:
            message = (
                f"{label}: odleglosc od okien/placu zabaw "
                f"{_fmt(distance_windows)} m, od granicy {distance_boundary:.2f} m — "
                f"naruszony prog {required} m (WT par. 19)"
            )
        else:
            message = (
                f"{label}: odleglosci od okien/placu zabaw {_fmt(distance_windows)} m "
                f"i od granicy {distance_boundary:.2f} m — WT par. 19"
            )
        out.append(
            with_evidence(
                check,
                message=message,
                evidence={
                    "geometry": dict(mapping(geom)),
                    "parking_index": parking.index,
                    "spaces": parking.spaces,
                    "distance_to_windows_m": (
                        round(distance_windows, 3) if distance_windows is not None else None
                    ),
                    "distance_to_boundary_m": round(distance_boundary, 3),
                },
            )
        )

        # ---- §21-context plausibility screen (heuristic, soft) ---------------- #
        required_area = parking.spaces * cfg.stall_area_m2
        area = float(geom.area)
        if area < required_area:
            wt21 = registry.get(RULE_WT21)
            out.append(
                RuleCheck(
                    rule_id=RULE_WT21,
                    status=RuleStatus.WARNING,
                    severity="soft",
                    message=(
                        f"{RULE_WT21}: {label}: powierzchnia {area:.0f} m2 jest "
                        f"niewiarygodna dla {parking.spaces} stanowisk — heurystyka "
                        f"~{cfg.stall_area_m2:g} m2/stanowisko (2,5x5 m + manewry) "
                        f"wymaga ok. {required_area:.0f} m2 [basis: industry_heuristic]"
                    ),
                    trace={
                        "mode": EvaluationMode(mode).value,
                        "heuristic": {
                            "stall_area_m2": cfg.stall_area_m2,
                            "basis": BASIS_HEURISTIC,
                            "note": (
                                "screen poprawnosci rysunku, NIE ewaluacja prawna "
                                "WT par. 21 (wymiary stanowisk)"
                            ),
                        },
                        "polygon_area_m2": round(area, 2),
                        "required_area_m2": round(required_area, 2),
                    },
                    source_reference=wt21.source_reference if wt21 else None,
                    confidence=0.6,
                    geometry_evidence={
                        "geometry": dict(mapping(geom)),
                        "parking_index": parking.index,
                        "spaces": parking.spaces,
                    },
                )
            )
    return out


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "?"
