"""WT §271–273 — fire separation between buildings (plan §10.1.6).

Classification (``basis: simplified_classification``, WT §209 reference —
documented in :data:`plot_planning.wt_validators.context.ZL_BY_USE`):
mieszkalny / mieszkalno-usługowy → ZL IV, hotelowy → ZL V, usługowy → ZL III,
garażowy / techniczny → PM with the gęstość obciążenia ogniowego Q from config
(default ≤1000 MJ/m², i.e. the base §271 row). For the §271 table only Q
matters: ZL/IN rows collapse to Q = 0 (the YAML documents this contract).

Per building PAIR (proposal×proposal and NEW-proposal×neighbor; pairs where
both buildings already exist are skipped — nothing is being situated), ONE
engine evaluation of ``PL-PPOZ-271-273-FIRE-SEPARATION-001`` covers:

* §271 — ``separation_distance_m`` = footprint↔footprint distance,
  ``max_q_mj_m2`` = max of the pair's Q values; the ust. 2–7 modifier flags
  (fire-spreading walls, sprinklers, …) are NOT modeled in the DSL → left to
  the rule's documented conservative ``input_defaults`` (recorded assumptions);
* §272 — ``distance_to_unbuilt_boundary_m`` = the smaller footprint→parcel-
  boundary distance over the pair's NEW on-plot members only (§272 binds the
  wznoszony budynek; a neighbor footprint sits outside the parcel, so its
  distance to OUR boundary never feeds the input); ``neighbor_assumed_q_mj_m2
  = 0`` (no MPZP for the neighbor parcel → ZL assumption per §272 ust. 1 in
  fine).
  The whole parcel boundary is conservatively treated as a granica sąsiedniej
  niezabudowanej działki budowlanej (road-parcel boundaries need Phase 12
  neighbor data — documented assumption);
* §273 — ``same_plot_within_fire_zone_limit``: the strefa pożarowa area table
  is NOT in our YAML corpus, so the joint-strefa exemption can only be claimed
  when the caller supplies ``strefa_pozarowa_limit_m2``; then the flag is
  ``combined FOOTPRINT area ≤ limit`` (footprints approximate "łączna
  powierzchnia wewnętrzna" — documented simplification). Without the limit the
  flag stays at the rule's conservative default (false → distances enforced).

A single-building plan with no neighbors evaluates §272 alone per building (the
§271 separation check then reports *unknown* — annotated "brak pary budynków").
"""

from __future__ import annotations

from itertools import combinations

from plot_rules import EvaluationMode, OverrideStore, RuleCheck, RulesetRegistry, RuleStatus
from plot_rules import evaluate as evaluate_rule
from shapely.geometry import LineString, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from plot_planning.wt_validators.config import (
    RULE_PPOZ_271,
    decided_entry,
    find_override,
    missing_rule_check,
    with_evidence,
)
from plot_planning.wt_validators.context import ZL_BY_USE, ValidationContext


def _building_q(uses: tuple[str, ...], pm_q_mj_m2: float) -> float:
    """Q for the §271 table: ZL uses → 0; any PM segment → the config Q default."""
    has_pm = any(u not in ZL_BY_USE for u in uses)
    return pm_q_mj_m2 if has_pm else 0.0


def check_fire_separation(
    ctx: ValidationContext,
    registry: RulesetRegistry,
    *,
    mode: EvaluationMode | str = EvaluationMode.CONSERVATIVE,
    overrides: OverrideStore | None = None,
    analysis_id: str | None = None,
    strefa_pozarowa_limit_m2: float | None = None,
) -> list[RuleCheck]:
    """Evaluate WT §271/§272/§273 over every relevant building pair."""
    rule = registry.get(RULE_PPOZ_271)
    if rule is None:
        return [missing_rule_check(RULE_PPOZ_271, mode)]
    cfg = ctx.config
    boundary = ctx.parcel.boundary

    # Units: (name, footprint, q, is_new, on_plot)
    units: list[tuple[str, BaseGeometry, float, bool, bool]] = [
        (b.name, b.footprint, _building_q(b.uses, cfg.pm_q_mj_m2), b.is_new, True)
        for b in ctx.buildings
    ]
    # Neighbor buildings: use unknown → ZL assumption (q=0, documented; per the
    # YAML contract pure ZL/IN pairs pass max_q = 0).
    units.extend(
        (p.owner, p.geometry, 0.0, False, False) for p in ctx.neighbor_parts
    )

    out: list[RuleCheck] = []
    pair_seen: set[str] = set()
    for (name_a, geom_a, q_a, new_a, plot_a), (
        name_b, geom_b, q_b, new_b, plot_b,
    ) in combinations(units, 2):
        if not (new_a or new_b):
            continue  # both already stand — nothing is being situated
        separation = float(geom_a.distance(geom_b))
        # §272 ust. 1 binds the WZNOSZONY budynek: its wall → our parcel boundary.
        # Only NEW on-plot members of the pair feed the input (a neighbor footprint
        # sits OUTSIDE the parcel — its distance to our boundary is meaningless and
        # would falsely fail §272 for any adjacent neighbor).
        boundary_dists = [
            float(geom.distance(boundary))
            for geom, is_new, on_plot in (
                (geom_a, new_a, plot_a),
                (geom_b, new_b, plot_b),
            )
            if is_new and on_plot
        ]
        dist_boundary = min(boundary_dists) if boundary_dists else None
        inputs: dict[str, object] = {
            "separation_distance_m": separation,
            "max_q_mj_m2": max(q_a, q_b),
            # None (no new on-plot member) → input absent → engine: unknown,
            # never a silent pass.
            "distance_to_unbuilt_boundary_m": dist_boundary,
            "neighbor_assumed_q_mj_m2": 0.0,  # §272: no plan → ZL (documented)
            # ust. 2–7 modifier flags + same_plot flag intentionally absent →
            # the rule's documented conservative input_defaults apply (traced).
        }
        if strefa_pozarowa_limit_m2 is not None and plot_a and plot_b:
            combined = float(geom_a.area + geom_b.area)
            inputs["same_plot_within_fire_zone_limit"] = (
                combined <= strefa_pozarowa_limit_m2
            )
        # Pairwise subject: "pair:A|B" with the names SORTED (order-independent).
        override = find_override(
            overrides,
            analysis_id,
            RULE_PPOZ_271,
            subject="pair:" + "|".join(sorted((name_a, name_b))),
        )
        check = evaluate_rule(rule, inputs, mode=mode, override=override)
        entry = decided_entry(check)
        required = entry["target_value"] if entry else "?"
        pair_seen.update((name_a, name_b))
        connector = LineString([(p.x, p.y) for p in nearest_points(geom_a, geom_b)])
        if check.status is RuleStatus.FAIL:
            message = (
                f"'{name_a}' ↔ '{name_b}': odleglosc {separation:.2f} m — wymagane "
                f">= {required} m (WT par. 271-273)"
            )
        else:
            message = (
                f"'{name_a}' ↔ '{name_b}': odleglosc {separation:.2f} m vs prog "
                f"{required} m — WT par. 271-273"
            )
        out.append(
            with_evidence(
                check,
                message=message,
                evidence={
                    "geometry": dict(mapping(connector)),
                    "pair": [name_a, name_b],
                    "separation_distance_m": round(separation, 3),
                    "distance_to_unbuilt_boundary_m": (
                        round(dist_boundary, 3) if dist_boundary is not None else None
                    ),
                    "max_q_mj_m2": max(q_a, q_b),
                    "q_basis": "simplified_classification (WT par. 209 mapping)",
                },
            )
        )

    # §272 must still hold for buildings with no pair at all (single new
    # building, no neighbors): evaluate with the separation input absent — the
    # engine reports the §271 check unknown (annotated), the §272 boundary
    # check decides; never a silent skip.
    for building in ctx.buildings:
        if not building.is_new or building.name in pair_seen:
            continue
        dist_boundary = float(building.footprint.distance(boundary))
        check = evaluate_rule(
            rule,
            {
                "max_q_mj_m2": _building_q(building.uses, cfg.pm_q_mj_m2),
                "distance_to_unbuilt_boundary_m": dist_boundary,
                "neighbor_assumed_q_mj_m2": 0.0,
            },
            mode=mode,
            # No pair → the subject is the single building itself.
            override=find_override(
                overrides, analysis_id, RULE_PPOZ_271, subject=building.name
            ),
        )
        out.append(
            with_evidence(
                check,
                message=(
                    f"'{building.name}': odleglosc od granicy niezabudowanej "
                    f"{dist_boundary:.2f} m (par. 272); par. 271 bez zastosowania — "
                    "brak pary budynkow"
                ),
                evidence={
                    "geometry": dict(mapping(building.footprint)),
                    "building": building.name,
                    "distance_to_unbuilt_boundary_m": round(dist_boundary, 3),
                },
                extra_trace={"no_pair": "brak drugiego budynku/sasiada — par. 271 n/d"},
            )
        )
    return out
