"""``run_full_due_diligence`` — the Phase 12 full-analysis use-case (v1 Phase 10).

Builds ON TOP of the screening pipeline (one fetch per theme — the screening
internals are reused, never re-fetched) and runs the site-context modules
(:mod:`plot_planning.site_context`) over the SAME layers plus two Phase 12
sources:

* **terrain** — NMT/DEM GeoTIFF via the injected :class:`TerrainSource` (WCS in
  production; fixture raster in tests). The coverage raster goes to object
  storage; only its URI travels (§26.3).
* **neighbor buildings** — BDOT10k features via :class:`NeighborBuildingsSource`
  → typed ``NeighborBuilding`` records for the Phase 10 validators + the
  windows-at-boundary precheck.

Degradation (NFR-REL-001 / §21): a missing/unavailable source makes its themes
EXPLICIT unknowns (and the dependent §14 scores unknown) — never "no
constraint"; the run continues and the result is PARTIAL.

The §14.1 site scores are computed by ``compute_site_scores`` and wired through
the ``PHASE10_SCORE_HOOKS`` of :class:`~plot_agent.selfimprove.evaluator
.ScoreEvaluator` (plan delta 2); the numeric ``AnalysisScores`` block of the
result carries the computed values, the full explainable set travels in
``planning['_scores_explained']``. Hard-blocker dominance (§14.2) is preserved:
the decision is recomputed over the MERGED risk register via the same
``plot_envelope.risk.decision``.
"""

from __future__ import annotations

import uuid
from typing import Any

from plot_connectors import ResultStatus
from plot_domain import (
    AnalysisInput,
    AnalysisResult,
    AnalysisStatus,
    InvestmentGoal,
    Severity,
    UnknownItem,
)
from plot_envelope import RiskKind, decision
from plot_planning.site_context import (
    SiteContext,
    analyze_access,
    analyze_environment,
    analyze_geology,
    analyze_terrain,
    analyze_water,
    compute_site_scores,
    neighbors_from_features,
    terrain_unavailable,
    windows_at_boundary_precheck,
)
from plot_rules import RulesetRegistry
from plot_shared import get_logger
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from plot_agent.analysis.connectors import Connectors
from plot_agent.analysis.quick_screening import (
    ScreeningInternals,
    run_screening_with_internals,
)
from plot_agent.context import AnalysisContext
from plot_agent.selfimprove.evaluator import ScoreEvaluator

_log = get_logger(__name__)


async def run_full_due_diligence(
    input: AnalysisInput,
    *,
    connectors: Connectors,
    ruleset_dir: str = "rulesets/PL",
    ruleset_registry: RulesetRegistry | None = None,
    analysis_id: str | None = None,
    planning_store: Any | None = None,
    site_store: Any | None = None,
) -> AnalysisResult:
    """Run the full due-diligence analysis (Phase 12 / v1 Phase 10 §10.1).

    ``site_store`` (optional) receives the TYPED :class:`SiteContext` keyed by
    the analysis id, so the masterplan integration (plan delta 1) can consume
    live geometries/grids without re-fetching.
    """
    result, internals = await run_screening_with_internals(
        input,
        connectors=connectors,
        ruleset_dir=ruleset_dir,
        ruleset_registry=ruleset_registry,
        analysis_id=analysis_id,
        planning_store=planning_store,
    )
    if internals.parcel_geom is None or internals.registry is None:
        # No parcel geometry → the screening partial result IS the full result
        # (site modules need geometry; the resolution unknown is already recorded).
        _store_freshness_verdict(result)
        return result

    parcel = internals.parcel_geom
    registry = internals.registry
    site = SiteContext(analysis_id=result.analysis_id)
    extra_sources: list[dict[str, Any]] = []
    any_site_source_failed = False

    # ------------------------------------------------------------------ #
    # Terrain (§10.1.1) — injected TerrainSource (None → not configured).
    # Every site-module call is exception-isolated (review M1 / NFR-REL-001):
    # a crashing module degrades to ITS theme's explicit unknown + a partial
    # result, never killing the whole analysis.
    # ------------------------------------------------------------------ #
    if connectors.terrain is None:
        site.terrain = terrain_unavailable("source_not_configured")
    else:
        assert internals.bbox is not None
        fetch = await connectors.terrain.fetch(internals.bbox)
        if fetch.status is ResultStatus.OK and fetch.raster_bytes:
            try:
                site.terrain = analyze_terrain(
                    fetch.raster_bytes,
                    parcel,
                    storage_uri=fetch.storage_uri,
                    source_id=fetch.source_id,
                )
            except Exception as exc:  # e.g. RasterioIOError on unreadable bytes
                _log_module_error("terrain", result.analysis_id, exc)
                any_site_source_failed = True
                site.terrain = terrain_unavailable(_module_error_reason(exc))
            if fetch.result is not None:
                extra_sources.append(fetch.result.source_record.model_dump(mode="json"))
        else:
            any_site_source_failed = True
            site.terrain = terrain_unavailable("source_unavailable")

    mean_slope: float | None = None
    if site.terrain is not None and site.terrain.status == "ok":
        slope_val = site.terrain.slope.get("mean_pct")
        mean_slope = float(slope_val) if slope_val is not None else None

    # ------------------------------------------------------------------ #
    # Water / geology / environment / roads+utilities over the SAME layers
    # (each exception-isolated — review M1).
    # ------------------------------------------------------------------ #
    try:
        site.water = analyze_water(
            parcel,
            flood_geoms=internals.layer_geoms(RiskKind.FLOOD),
            watercourse_geoms=internals.layer_geoms(RiskKind.WATERCOURSES),
            flood_status=internals.status_of(RiskKind.FLOOD),
            watercourse_status=internals.status_of(RiskKind.WATERCOURSES),
            mean_slope_pct=mean_slope,
        )
    except Exception as exc:
        _log_module_error("water", result.analysis_id, exc)
        any_site_source_failed = True
        result.unknowns.append(
            _module_error_unknown(
                "Hydrologia / powódź (moduł site-context water)", exc, result.analysis_id
            )
        )
    try:
        site.geology = analyze_geology(
            parcel,
            landslide_geoms=internals.layer_geoms(RiskKind.LANDSLIDE),
            landslide_status=internals.status_of(RiskKind.LANDSLIDE),
        )
    except Exception as exc:
        _log_module_error("geology", result.analysis_id, exc)
        any_site_source_failed = True
        result.unknowns.append(
            _module_error_unknown(
                "Geologia / osuwiska (moduł site-context geology)", exc, result.analysis_id
            )
        )
    goal = input.investment_goal
    # Review M4: the EIA thresholds are powierzchnia zabudowy/terenu (ha) — feed a
    # FOOTPRINT-basis area, never raw GFA, and label the basis for consumers.
    eia_area_m2, eia_area_basis = _eia_investment_area(goal, parcel, result.analysis_id)
    try:
        site.environment = analyze_environment(
            parcel,
            registry,
            protected_geoms=internals.layer_geoms(RiskKind.PROTECTED),
            heritage_geoms=internals.layer_geoms(RiskKind.HERITAGE),
            protected_status=internals.status_of(RiskKind.PROTECTED),
            heritage_status=internals.status_of(RiskKind.HERITAGE),
            investment_type=goal.type.value,
            investment_area_m2=eia_area_m2,
            investment_area_basis=eia_area_basis,
        )
    except Exception as exc:
        _log_module_error("environment", result.analysis_id, exc)
        any_site_source_failed = True
        result.unknowns.append(
            _module_error_unknown(
                "Środowisko / zabytki (moduł site-context environment)",
                exc,
                result.analysis_id,
            )
        )
    utility_layer = internals.risk_layers.get(RiskKind.UTILITIES)
    utility_features: list[dict[str, Any]] | None
    if internals.status_of(RiskKind.UTILITIES) == "source_unavailable":
        utility_features = None
    else:
        utility_features = [
            {"type": "Feature", "geometry": g.__geo_interface__, "properties": {}}
            for g in (utility_layer.geometries if utility_layer else [])
        ]
    try:
        site.access = analyze_access(
            parcel,
            road_geoms=internals.layer_geoms(RiskKind.ROADS),
            utility_features=utility_features,
            roads_status=internals.status_of(RiskKind.ROADS),
            utilities_status=internals.status_of(RiskKind.UTILITIES),
            road_source_id=(
                internals.risk_layers[RiskKind.ROADS].source_id
                if RiskKind.ROADS in internals.risk_layers
                else None
            ),
            utility_source_id=utility_layer.source_id if utility_layer else None,
        )
    except Exception as exc:
        _log_module_error("roads", result.analysis_id, exc)
        any_site_source_failed = True
        result.unknowns.append(
            _module_error_unknown(
                "Drogi / uzbrojenie (moduł site-context roads)", exc, result.analysis_id
            )
        )

    # ------------------------------------------------------------------ #
    # Neighborhood (§10.1.6) — injected buildings source (None → unknown).
    # ------------------------------------------------------------------ #
    if connectors.buildings is None:
        site.neighbor_notes.append(
            "Źródło budynków sąsiednich (BDOT10k) nieskonfigurowane — analiza "
            "sąsiedztwa/zacieniania pominięta (wpływ NIEZNANY, §21)."
        )
        result.unknowns.append(
            UnknownItem(
                id=f"unk:neighbors:{result.analysis_id[:8]}",
                analysis_id=result.analysis_id,
                topic="Zabudowa sąsiednia (BDOT10k)",
                severity=Severity.MEDIUM,
                reason="source_not_configured",
                suggested_action="Skonfigurować źródło budynków BDOT10k dla analizy sąsiedztwa.",
            )
        )
    else:
        assert internals.bbox is not None
        bfetch = await connectors.buildings.fetch(internals.bbox)
        if bfetch.status is ResultStatus.SOURCE_UNAVAILABLE:
            any_site_source_failed = True
            result.unknowns.append(
                UnknownItem(
                    id=f"unk:neighbors:{result.analysis_id[:8]}",
                    analysis_id=result.analysis_id,
                    topic="Zabudowa sąsiednia (BDOT10k)",
                    severity=Severity.MEDIUM,
                    reason="source_unavailable",
                    suggested_action=(
                        "Ponowić pobranie warstwy budynków — zacienianie sąsiadów "
                        "i okna przy granicy niezweryfikowane."
                    ),
                )
            )
        else:
            try:
                neighbors, notes = neighbors_from_features(
                    [f for f in bfetch.features if _outside(parcel, f)]
                )
                site.neighbors = list(neighbors)
                site.neighbor_notes = notes
                site.windows_at_boundary = windows_at_boundary_precheck(neighbors, parcel)
            except Exception as exc:  # exception isolation (review M1)
                _log_module_error("neighborhood", result.analysis_id, exc)
                any_site_source_failed = True
                result.unknowns.append(
                    _module_error_unknown(
                        "Zabudowa sąsiednia (moduł site-context neighborhood)",
                        exc,
                        result.analysis_id,
                    )
                )
            if bfetch.result is not None:
                extra_sources.append(bfetch.result.source_record.model_dump(mode="json"))

    # ------------------------------------------------------------------ #
    # Merge: risks / unknowns / recommendations / sources / site summary.
    #
    # Review M3 — the register must not duplicate a theme:
    # * risks: the screening red_flags already emit one RiskItem per detected
    #   constraint (flood/protected/landslide/heritage…). A site-module risk of a
    #   risk_type already present is SKIPPED — the screening entry is kept because
    #   it carries the source_id → evidence link (NFR-AUD-001) and is the entry
    #   the §14.2 decision semantics were defined over; the site modules' extra
    #   detail (coverage %, mitigations) still travels in planning['_site_context'].
    # * unknowns: a site-module ``source_unavailable`` unknown for a screening
    #   theme duplicates the screening's unknowns_for_unavailable entry → skipped
    #   (terrain/buildings are Phase 12-only sources, NOT screening themes, so
    #   their unknowns always merge); plus an exact-topic dedupe as a guard.
    # ------------------------------------------------------------------ #
    seen_risk_types = {r.risk_type for r in result.risks}
    for risk in site.all_risks():
        if risk.risk_type in seen_risk_types:
            continue
        seen_risk_types.add(risk.risk_type)
        result.risks.append(risk)
    screening_covered_unknown_ids = {
        u.id
        for module in (site.water, site.geology, site.environment, site.access)
        if module is not None
        for u in module.unknowns
        if u.reason == "source_unavailable"
    }
    seen_topics = {u.topic for u in result.unknowns}
    for unknown in site.all_unknowns():
        if unknown.id in screening_covered_unknown_ids or unknown.topic in seen_topics:
            continue
        seen_topics.add(unknown.topic)
        result.unknowns.append(
            unknown.model_copy(update={"analysis_id": result.analysis_id})
        )
    result.next_actions.extend(
        r.model_copy(update={"analysis_id": result.analysis_id})
        for r in site.all_recommendations()
    )
    if isinstance(result.planning, dict):
        sources = list(result.planning.get("_sources") or [])
        sources.extend(extra_sources)
        result.planning["_sources"] = sources
        result.planning["_site_context"] = site.to_dict()

    # ------------------------------------------------------------------ #
    # §14 scores (delta 2): site scores → PHASE10_SCORE_HOOKS + result block.
    # ------------------------------------------------------------------ #
    site_scores = compute_site_scores(
        site,
        planning_block=result.planning if isinstance(result.planning, dict) else None,
        investment_goal=goal.model_dump(mode="json"),
        envelope_area_m2=(
            float(result.buildable_envelope.area_m2)
            if result.buildable_envelope and result.buildable_envelope.area_m2
            else None
        ),
        parcel_area_m2=float(parcel.area),
    )
    evaluator = ScoreEvaluator(site_scores=site_scores)
    evaluated = evaluator.evaluate(
        AnalysisContext(
            parcel=parcel,
            buildable_envelope=(
                internals.envelope_geom if internals.envelope_geom is not None else parcel
            ),
            ruleset=registry,
            analysis_mode="full_due_diligence",
        )
    )
    if isinstance(result.planning, dict):
        result.planning["_scores_explained"] = evaluated.to_dict()
    # Numeric §10.7 score block: fill the fixed fields the schema defines.
    scores = result.scores
    for score_name, attr in (
        ("terrain_score", "terrain"),
        ("environmental_risk_score", "environmental_risk"),
        ("infrastructure_score", "infrastructure"),
        ("planning_certainty_score", "planning_certainty"),
        ("procedural_risk_score", "procedural_risk"),
    ):
        value = evaluated.value(score_name)
        if value is not None:
            setattr(scores, attr, round(float(value), 4))

    # ------------------------------------------------------------------ #
    # Decision + status: recompute over the MERGED register (§14.2 dominance
    # via the same plot_envelope.risk.decision — hard blockers short-circuit).
    # ------------------------------------------------------------------ #
    new_decision = decision(
        result.risks,
        result.buildable_envelope,
        parcel_area_m2=float(parcel.area),
    )
    result.decision = new_decision
    if any_site_source_failed or internals.any_source_failed:
        result.status = AnalysisStatus.PARTIAL
    elif (
        result.status is AnalysisStatus.COMPLETE
        and new_decision.value == "NEEDS_MANUAL_REVIEW"
    ):
        result.status = AnalysisStatus.MANUAL_REVIEW_REQUIRED

    _store_freshness_verdict(result)
    if site_store is not None:
        site_store.put(result.analysis_id, site)
    return result


def _store_freshness_verdict(result: AnalysisResult) -> None:
    """Stamp the source-freshness verdict on the analysis (review M2; F-0439/0443).

    The verdict (``planning['_freshness']``: per-source staleness + ``degraded``
    + ``stale_sources``) is what the analysis-bound ``propose_layout`` path
    reads to FORCE conservative rule evaluation when the sources behind the
    analysis are stale or unverifiable — freshness can only tighten the
    consumer's mode, never relax it (§21 safe failure).
    """
    if not isinstance(result.planning, dict):  # pragma: no cover - defensive
        return
    from plot_agent.monitoring.freshness import source_freshness

    sources = result.planning.get("_sources") or []
    report = source_freshness(sources)
    verdict = report.to_dict()
    if not sources:
        # Fail-safe (§21): zero visible sources is never "fresh".
        verdict["degraded"] = True
        verdict["reason"] = "no_sources_visible"
    result.planning["_freshness"] = verdict


def _outside(parcel: Any, feature: dict[str, Any]) -> bool:
    """Keep only buildings OUTSIDE the parcel (neighbors, not own existing)."""
    geom_json = feature.get("geometry")
    if not isinstance(geom_json, dict):
        return False
    try:
        geom = shape(geom_json)
    except (ValueError, TypeError, KeyError):
        return False
    return not geom.is_empty and not parcel.contains(geom.centroid)


def _module_error_reason(exc: Exception) -> str:
    return f"module_error:{type(exc).__name__}"


def _log_module_error(module: str, analysis_id: str, exc: Exception) -> None:
    """Structured log for an isolated site-module failure (review M1)."""
    _log.error(
        "site_module_failed",
        module=module,
        analysis_id=analysis_id,
        exc_type=type(exc).__name__,
        error=str(exc),
    )


def _module_error_unknown(topic: str, exc: Exception, analysis_id: str) -> UnknownItem:
    """Explicit unknown for a site module that crashed (review M1 / §21)."""
    return UnknownItem(
        id=f"unk:module:{uuid.uuid4().hex[:8]}",
        analysis_id=analysis_id,
        topic=topic,
        severity=Severity.MEDIUM,
        reason=_module_error_reason(exc),
        suggested_action=(
            "Moduł analizy kontekstu zgłosił błąd przetwarzania — sprawdzić dane "
            "wejściowe/logi i ponowić; temat pozostaje NIEZNANY (§21), "
            "nie zakładać braku ograniczeń."
        ),
    )


def _eia_investment_area(
    goal: InvestmentGoal, parcel: BaseGeometry, analysis_id: str
) -> tuple[float | None, str | None]:
    """Footprint-basis area for the EIA screening + its basis label (review M4).

    The ``eia-screening.yaml`` thresholds are POWIERZCHNIA ZABUDOWY/TERENU in ha
    — raw GFA over-triggers (a 25 000 m² GFA tower on 0.3 ha of footprint is far
    below the 2 ha próg). Basis precedence (documented in the output):

    1. ``masterplan_footprint`` — a stored masterplan variant of THIS analysis
       supplies its real powierzchnia_zabudowy total;
    2. ``gfa_capped_proxy_conservative`` — ``min(target_gfa_m2, parcel.area)``
       (footprint can never exceed the parcel; still conservative — it assumes
       single-storey worst case);
    3. ``parcel_area_upper_bound`` — explicit goal type but no target: the parcel
       area is the upper bound;
    4. unknown goal → ``(None, None)`` (screening honestly unknown, §0v2.4).
    """
    parcel_area = float(parcel.area)
    # plot_agent-internal lookup (analysis → drawing is not a layering edge the
    # §9.4 decoupling rules forbid); imported lazily to keep module import light.
    from plot_agent.drawing import DEFAULT_VARIANT_STORE

    variant = DEFAULT_VARIANT_STORE.latest(analysis_id=analysis_id)
    if variant is not None:
        footprint = variant.totals.get("powierzchnia_zabudowy_m2")
        if isinstance(footprint, int | float) and footprint > 0:
            return float(footprint), "masterplan_footprint"
    if goal.target_gfa_m2:
        return min(float(goal.target_gfa_m2), parcel_area), "gfa_capped_proxy_conservative"
    if goal.type.value != "unknown":
        return parcel_area, "parcel_area_upper_bound"
    return None, None


__all__ = ["run_full_due_diligence", "ScreeningInternals"]
