"""``run_quick_screening`` — the §27 shared quick-screening use-case (Phase 7 §C).

Orchestrates the MVP analysis pipeline end-to-end, with INJECTED connectors (no live
network) and full evidence + partial-result discipline:

1. **resolve parcel** (ULDK) — id or point → geometry (WKT, EPSG:2180) + admin context;
2. **metrics** (plot_geo) — area / perimeter / compactness / frontage-ready geometry;
3. **fetch MVP risk layers** via the injected :class:`RiskLayerSource` (flood / protected /
   landslide / heritage / utilities / roads + watercourses / forest) over the parcel bbox;
4. **overlay → constraints** + **buildable envelope v1** (plot_envelope), with statutory
   boundary setback from the ruleset;
5. **red flags + decision** with HARD-BLOCKER DOMINANCE (§14.2);
6. **assemble** a schema-conforming :class:`~plot_domain.AnalysisResult` with evidence,
   risks, unknowns, next_actions, scores (reusing the Phase 4 :class:`ScoreEvaluator`),
   planning summary and the buildable-envelope artifact.

Partial results (NFR-REL-001/010): a layer returning ``source_unavailable`` is recorded as
an unknown and the run continues; ``status`` becomes ``partial`` if any source failed. The
``not_detected`` vs ``source_unavailable`` distinction is preserved (§21). Every claim
carries an EvidenceItem (source_id + retrieved_at) or an explicit ``no_source`` marker
(NFR-AUD-001).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import plot_geo
from plot_connectors import BBox, ParcelByIdQuery, ParcelByXYQuery, ResultStatus
from plot_domain import (
    AdministrativeContext,
    AnalysisInput,
    AnalysisResult,
    AnalysisScores,
    AnalysisStatus,
    BuildableEnvelope,
    EvidenceItem,
    GeometryPrecision,
    Parcel,
    ReportArtifact,
    SourceRecord,
)
from plot_envelope import (
    RiskKind,
    RiskLayer,
    buildable_envelope_v1,
    decision,
    next_actions,
    no_build_zones,
    overlay_layers,
    red_flags,
    soft_constraints,
    unknowns_for_unavailable,
)
from plot_rules import RulesetRegistry, load_rulesets
from shapely import from_wkt
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from plot_agent.analysis.connectors import Connectors
from plot_agent.context import AnalysisContext
from plot_agent.selfimprove.evaluator import ScoreEvaluator

#: Which ``profiles.PL`` source_id backs each MVP risk theme (§32 / §18.2). Roads /
#: watercourses / forest are derived from the BDOT10k topographic layer in v1 (one WFS).
RISK_LAYER_PROFILE_MAP: dict[RiskKind, str] = {
    RiskKind.FLOOD: "pl.isok.wfs.flood",
    RiskKind.PROTECTED: "pl.gdos.wfs.crfop",
    RiskKind.LANDSLIDE: "pl.pig.wfs.sopo",
    RiskKind.HERITAGE: "pl.nid.wfs.heritage",
    RiskKind.UTILITIES: "pl.geoportal.wfs.gesut",
    RiskKind.ROADS: "pl.geoportal.wfs.bdot10k",
    RiskKind.WATERCOURSES: "pl.geoportal.wfs.bdot10k",
    RiskKind.FOREST: "pl.geoportal.wfs.bdot10k",
}

#: Themes the MVP gate (§18.2) requires to be CHECKED (present as checked or unavailable).
MVP_RISK_THEMES: tuple[RiskKind, ...] = (
    RiskKind.FLOOD,
    RiskKind.PROTECTED,
    RiskKind.LANDSLIDE,
    RiskKind.HERITAGE,
    RiskKind.UTILITIES,
    RiskKind.ROADS,
    RiskKind.WATERCOURSES,
    RiskKind.FOREST,
)

# Soft themes whose buffer strip is subtracted for a realistic envelope footprint.
_ENVELOPE_SOFT_THEMES = {RiskKind.ROADS, RiskKind.FOREST, RiskKind.WATERCOURSES, RiskKind.UTILITIES}

_BBOX_PAD_M = 50.0  # analysis window padding around the parcel bbox (F-0083 minimised).


def _now() -> datetime:
    return datetime.now(UTC)


def _setback_from_ruleset(registry: RulesetRegistry) -> tuple[float, str | None, str | None]:
    """Read the statutory boundary setback from the building-technical ruleset (§12).

    Uses ``rulesets/PL/building-technical/setback-granica.yaml`` —
    ``default_setback_without_windows_m`` (the conservative minimum any wall must keep).
    Returns ``(setback_m, rule_id, rule_version)``; ``(0.0, None, None)`` if not found.
    """
    for rule in registry.by_category("building-technical"):
        # The loader keeps only the canonical fields; re-read the file for scalars.
        import yaml

        try:
            with open(rule.path, "rb") as fh:
                doc = yaml.safe_load(fh) or {}
        except OSError:
            doc = {}
        val = doc.get("default_setback_without_windows_m")
        if isinstance(val, int | float):
            return float(val), rule.id, rule.valid_from
    return 0.0, None, None


def _parcel_geometry(parcel_payload: dict[str, Any]) -> BaseGeometry | None:
    """Coerce the ULDK parcel payload (WKT in EPSG:2180) to a shapely geometry."""
    wkt = parcel_payload.get("geometry_wkt")
    if isinstance(wkt, str) and wkt.strip():
        geom = from_wkt(wkt)
        if geom is not None and not geom.is_empty:
            return geom
    geojson = parcel_payload.get("geometry")
    if isinstance(geojson, dict):
        geom = shape(geojson)
        if not geom.is_empty:
            return geom
    return None


def _evidence_for_run(evidence: list[EvidenceItem], analysis_id: str) -> list[EvidenceItem]:
    """Stamp the analysis_id onto evidence the connector left blank (§11 / NFR-AUD-001)."""
    out: list[EvidenceItem] = []
    for ev in evidence:
        out.append(ev if ev.analysis_id else ev.model_copy(update={"analysis_id": analysis_id}))
    return out


@dataclass
class ScreeningInternals:
    """Intermediate screening artifacts the Phase 12 full pipeline builds on.

    ``run_full_due_diligence`` reuses the SAME fetched layers/envelope (one fetch
    per theme — no duplicate network calls) to drive the site-context modules.
    """

    parcel_geom: BaseGeometry | None = None
    registry: RulesetRegistry | None = None
    bbox: BBox | None = None
    risk_layers: dict[RiskKind, RiskLayer] = field(default_factory=dict)
    layer_status: dict[str, str] = field(default_factory=dict)
    envelope_geom: BaseGeometry | None = None
    any_source_failed: bool = False
    # Phase 14B (F-0517/0525): when the analysis started (time.monotonic()) so the
    # full-DD site-source fetches share the SAME total deadline budget.
    started_monotonic: float = 0.0

    def layer_geoms(self, kind: RiskKind) -> list[BaseGeometry] | None:
        """Geometries for a theme; ``None`` when the source was UNAVAILABLE (§21)."""
        if self.layer_status.get(kind.value) == "source_unavailable":
            return None
        layer = self.risk_layers.get(kind)
        return list(layer.geometries) if layer is not None else []

    def status_of(self, kind: RiskKind) -> str:
        return self.layer_status.get(kind.value, "source_unavailable")


async def run_quick_screening(
    input: AnalysisInput,
    *,
    connectors: Connectors,
    ruleset_dir: str = "rulesets/PL",
    ruleset_registry: RulesetRegistry | None = None,
    analysis_id: str | None = None,
    artifact_store: Any | None = None,
    planning_store: Any | None = None,
) -> AnalysisResult:
    """Run a quick-screening analysis end-to-end (§4.1 / §18.2).

    ``connectors`` is the injected bundle (ULDK + risk-layer source) so no live network is
    used in tests. ``ruleset_registry`` may be supplied (e.g. the hot-reloaded one from the
    MCP context); otherwise it is loaded fresh from ``ruleset_dir``. ``planning_store``
    (Phase 8) defaults to :data:`plot_planning.DEFAULT_PLANNING_STORE` — when it holds
    parsed APP/GML zones intersecting the parcel, the planning block reports real
    coverage instead of the pending note.
    """
    result, _internals = await run_screening_with_internals(
        input,
        connectors=connectors,
        ruleset_dir=ruleset_dir,
        ruleset_registry=ruleset_registry,
        analysis_id=analysis_id,
        artifact_store=artifact_store,
        planning_store=planning_store,
    )
    return result


async def run_screening_with_internals(
    input: AnalysisInput,
    *,
    connectors: Connectors,
    ruleset_dir: str = "rulesets/PL",
    ruleset_registry: RulesetRegistry | None = None,
    analysis_id: str | None = None,
    artifact_store: Any | None = None,
    planning_store: Any | None = None,
) -> tuple[AnalysisResult, ScreeningInternals]:
    """The screening pipeline, additionally returning :class:`ScreeningInternals`.

    Phase 12: the full-due-diligence pipeline builds its site-context modules on
    the SAME fetched layers (one fetch per theme). Behaviour of the returned
    result is byte-identical to :func:`run_quick_screening`.
    """
    analysis_id = analysis_id or str(uuid.uuid4())
    started_monotonic = time.monotonic()  # analysis-level deadline anchor (F-0525)
    registry = ruleset_registry or load_rulesets(ruleset_dir)
    evidence: list[EvidenceItem] = []
    sources: list[SourceRecord] = []
    artifacts: list[ReportArtifact] = []
    any_source_failed = False
    unavailable_kinds: list[RiskKind] = []
    not_detected_kinds: list[RiskKind] = []

    # ----------------------------------------------------------------- #
    # 1) Resolve parcel (ULDK) — id or point.
    # ----------------------------------------------------------------- #
    parcel_obj, parcel_geom, resolve_failed = await _resolve_parcel(
        input, connectors, analysis_id, evidence, sources
    )
    if resolve_failed:
        any_source_failed = True

    if parcel_geom is None:
        # Cannot do geometry work without a parcel; return a partial result that is still
        # useful (NFR-REL-010) — the parcel resolution unknown is recorded.
        return (
            _no_parcel_result(analysis_id, parcel_obj, evidence, sources),
            ScreeningInternals(
                registry=registry,
                any_source_failed=any_source_failed,
                started_monotonic=started_monotonic,
            ),
        )

    # ----------------------------------------------------------------- #
    # 2) Geometry metrics (plot_geo).
    # ----------------------------------------------------------------- #
    metrics = _geometry_metrics(parcel_geom)
    parcel_area = metrics["area_m2"]
    if parcel_obj is not None:
        parcel_obj = parcel_obj.model_copy(
            update={"area_m2": parcel_area, "geometry": mapping(parcel_geom)}
        )

    # ----------------------------------------------------------------- #
    # 3) Fetch MVP risk layers via injected connectors (no live network in tests).
    # ----------------------------------------------------------------- #
    bbox = _parcel_bbox(parcel_geom)
    risk_layers: list[RiskLayer] = []
    risk_layers_by_kind: dict[RiskKind, RiskLayer] = {}
    layer_status: dict[str, str] = {}
    precision_by_source: dict[str, GeometryPrecision] = {}

    # Phase 14B (F-0510/0511): the 8 theme fetches run CONCURRENTLY (the connector
    # layer is async) under a semaphore cap, unless a backpressure delay is
    # configured — then sequential with the delay (NFR-PERF-014). The analysis
    # deadline (F-0517/0525) degrades themes that cannot finish in budget to
    # source_unavailable (explicit unknowns), never silently dropping them.
    fetches = await _fetch_risk_layers(connectors, bbox, started_monotonic=started_monotonic)
    for fetch in fetches:
        kind = fetch.kind
        layer_status[kind.value] = fetch.status.value
        if fetch.status is ResultStatus.SOURCE_UNAVAILABLE:
            any_source_failed = True
            unavailable_kinds.append(kind)
            continue
        if fetch.result is not None:
            sources.append(fetch.result.source_record)
            precision_by_source[fetch.result.source_record.source_id] = (
                fetch.result.source_record.geometry_precision
            )
            evidence.extend(_evidence_for_run(fetch.result.evidence, analysis_id))
        if fetch.status is ResultStatus.OK and fetch.result is not None:
            geoms = _features_to_geoms(fetch.result, parcel_geom)
            layer = RiskLayer(
                kind=kind,
                geometries=geoms,
                status="ok",
                source_id=fetch.result.source_record.source_id,
                source_legal_status=fetch.result.source_record.legal_status,
                source_confidence=fetch.result.source_record.confidence,
            )
            risk_layers.append(layer)
            risk_layers_by_kind[kind] = layer
        else:
            # not_detected / confirmed_absent: theme checked & clear (NOT unavailable).
            not_detected_kinds.append(kind)

    # ----------------------------------------------------------------- #
    # 4) Overlay → constraints + buildable envelope v1.
    # ----------------------------------------------------------------- #
    constraints = overlay_layers(parcel_geom, risk_layers)
    setback_m, setback_rule_id, setback_rule_ver = _setback_from_ruleset(registry)
    nbz = no_build_zones(
        parcel_geom,
        constraints,
        boundary_setback_m=setback_m,
        setback_rule_id=setback_rule_id,
        setback_rule_version=setback_rule_ver,
    )
    soft = soft_constraints(constraints)
    envelope_soft = [c for c in soft if _theme_of(c) in _ENVELOPE_SOFT_THEMES]
    envelope = buildable_envelope_v1(
        parcel_geom,
        no_build=nbz,
        soft_setbacks=envelope_soft,
        constraints=constraints,
        precision_by_source=precision_by_source,
        analysis_id=analysis_id,
    )

    # ----------------------------------------------------------------- #
    # 5) Red flags + unknowns + decision (hard-blocker dominance, §14.2).
    # ----------------------------------------------------------------- #
    risks = red_flags(constraints, envelope, parcel_area_m2=parcel_area)
    unknowns = unknowns_for_unavailable(unavailable_kinds)
    decision_value = decision(risks, envelope, parcel_area_m2=parcel_area)
    actions = next_actions(risks, unknowns, decision_value)

    # ----------------------------------------------------------------- #
    # 6) Scores (reuse Phase 4 ScoreEvaluator for the computable ones).
    # ----------------------------------------------------------------- #
    scores = _scores(parcel_geom, envelope, registry, unknowns_count=len(unknowns))

    # buildable-envelope artifact (exposed as a resource, never inlined — NFR-PERF-009).
    artifacts.append(
        ReportArtifact(
            id=f"art:{uuid.uuid4().hex[:8]}",
            analysis_id=analysis_id,
            artifact_type="geojson",
            uri=f"analysis://{analysis_id}/buildable-envelope.geojson",
            created_at=_now(),
        )
    )

    planning = _planning_summary(
        parcel_obj, layer_status, registry, parcel_geom=parcel_geom, planning_store=planning_store
    )

    status = AnalysisStatus.PARTIAL if any_source_failed else AnalysisStatus.COMPLETE
    if decision_value.value in ("NEEDS_MANUAL_REVIEW", "LIKELY_BLOCKED"):
        # manual_review_required is a normal status, not an error (§20.12). Only escalate
        # status when nothing failed (else partial dominates so the user sees the gap).
        if status is AnalysisStatus.COMPLETE and decision_value.value == "NEEDS_MANUAL_REVIEW":
            status = AnalysisStatus.MANUAL_REVIEW_REQUIRED

    result = AnalysisResult(
        analysis_id=analysis_id,
        status=status,
        decision=decision_value,
        scores=scores,
        parcel=parcel_obj,
        planning=planning,
        constraints=constraints,
        buildable_envelope=envelope,
        capacity_scenarios=[],  # capacity is Phase 9
        risks=risks,
        unknowns=unknowns,
        next_actions=actions,
        evidence=evidence,
        artifacts=artifacts,
    )
    # Stash the source records + layer status + metrics in the planning block so the report
    # and sources_collect can surface them without a second pass (and they validate as
    # free-form planning data).
    result.planning["_sources"] = [s.model_dump(mode="json") for s in sources]
    result.planning["_risk_layer_status"] = layer_status
    result.planning["_geometry_metrics"] = metrics
    internals = ScreeningInternals(
        parcel_geom=parcel_geom,
        registry=registry,
        bbox=bbox,
        risk_layers=risk_layers_by_kind,
        layer_status=layer_status,
        envelope_geom=shape(envelope.geometry) if envelope.geometry else None,
        any_source_failed=any_source_failed,
        started_monotonic=started_monotonic,
    )
    return result, internals


# --------------------------------------------------------------------------- #
# Phase 14B: parallel theme fetches + analysis-level deadline (F-0510/0517/0525)
# --------------------------------------------------------------------------- #
DEADLINE_DETAIL = "analysis_deadline_exceeded (F-0517/0525)"


def _deadline_fetch(kind: RiskKind) -> Any:
    """Synthetic source_unavailable fetch for a theme cut off by the deadline."""
    from plot_agent.analysis.connectors import RiskLayerFetch

    return RiskLayerFetch(
        kind=kind,
        status=ResultStatus.SOURCE_UNAVAILABLE,
        result=None,
        source_id="analysis_deadline",
        detail=DEADLINE_DETAIL,
    )


def deadline_remaining_s(started_monotonic: float) -> float | None:
    """Seconds left in the analysis deadline budget; ``None`` when disabled."""
    from plot_shared import get_settings

    deadline = get_settings().analysis_deadline_s
    if not deadline:
        return None
    return deadline - (time.monotonic() - started_monotonic)


async def _fetch_risk_layers(
    connectors: Connectors, bbox: BBox, *, started_monotonic: float
) -> list[Any]:
    """Fetch all MVP themes; concurrent by default, sequential under backpressure.

    Order of the returned fetches always matches :data:`MVP_RISK_THEMES`
    (``asyncio.gather`` preserves order), so downstream processing is
    deterministic regardless of completion order.
    """
    from plot_shared import get_settings

    settings = get_settings()

    if settings.backpressure_delay_s > 0:
        # Sequential with the configured delay — never a parallel hammer on
        # public services when a deployment throttles egress (NFR-PERF-014).
        out: list[Any] = []
        for i, kind in enumerate(MVP_RISK_THEMES):
            remaining = deadline_remaining_s(started_monotonic)
            if remaining is not None and remaining <= 0:
                out.append(_deadline_fetch(kind))
                continue
            if i:
                await asyncio.sleep(settings.backpressure_delay_s)
            out.append(await connectors.risk_layers.fetch(kind, bbox))
        return out

    semaphore = asyncio.Semaphore(max(1, settings.parallel_fetch_limit))

    async def _one(kind: RiskKind) -> Any:
        async with semaphore:
            remaining = deadline_remaining_s(started_monotonic)
            if remaining is None:
                return await connectors.risk_layers.fetch(kind, bbox)
            if remaining <= 0:
                return _deadline_fetch(kind)
            try:
                return await asyncio.wait_for(
                    connectors.risk_layers.fetch(kind, bbox), timeout=remaining
                )
            except TimeoutError:
                return _deadline_fetch(kind)

    return list(await asyncio.gather(*(_one(kind) for kind in MVP_RISK_THEMES)))


# --------------------------------------------------------------------------- #
# Steps as helpers
# --------------------------------------------------------------------------- #
async def _resolve_parcel(
    input: AnalysisInput,
    connectors: Connectors,
    analysis_id: str,
    evidence: list[EvidenceItem],
    sources: list[SourceRecord],
) -> tuple[Parcel | None, BaseGeometry | None, bool]:
    """Resolve the parcel via ULDK (id or point). Returns ``(parcel, geom, failed)``."""
    from plot_connectors import SourceUnavailable

    data = input.input
    query: ParcelByIdQuery | ParcelByXYQuery | None = None
    if data.parcel_id:
        query = ParcelByIdQuery(parcel_id=data.parcel_id)
    elif data.point is not None:
        # ULDK GetParcelByXY default CRS is EPSG:2180; if the point is geographic, project.
        x, y, srid = _point_in_2180(data.point)
        query = ParcelByXYQuery(x=x, y=y, srid=srid)

    if query is None:
        return None, None, False

    try:
        norm = await connectors.uldk.resolve(query, snapshot=False)
    except SourceUnavailable:
        return None, None, True

    sources.append(norm.source_record)
    evidence.extend(_evidence_for_run(norm.evidence, analysis_id))

    if norm.status is not ResultStatus.OK or "parcel" not in norm.payload:
        return None, None, False  # not_detected, not a failure

    payload = norm.payload["parcel"]
    geom = _parcel_geometry(payload)
    admin = payload.get("administrative_context") or {}
    parcel = Parcel(
        id=payload.get("id") or f"parcel:{uuid.uuid4().hex[:8]}",
        external_id=payload.get("external_id"),
        teryt=payload.get("teryt"),
        number=payload.get("number"),
        geometry_wkt=payload.get("geometry_wkt"),
        geometry=mapping(geom) if geom is not None else None,
        input_crs=payload.get("input_crs") or "EPSG:2180",
        source_id=norm.source_record.source_id,
        administrative_context=AdministrativeContext(
            teryt=str(admin.get("teryt") or payload.get("teryt") or ""),
            commune=admin.get("commune"),
            county=admin.get("county"),
            voivodeship=admin.get("voivodeship"),
            district=admin.get("district"),
        ),
    )
    return parcel, geom, False


def _point_in_2180(point: Any) -> tuple[float, float, int]:
    """Return ``(x, y, srid)`` for a ULDK GetParcelByXY query (analytical 2180)."""
    crs = (point.crs or "EPSG:2180").upper()
    if crs in ("EPSG:2180", "2180"):
        return point.x, point.y, 2180
    # Project geographic / other CRS to EPSG:2180 (never query in degrees).
    from shapely.geometry import Point

    projected = plot_geo.to_analytical(Point(point.x, point.y), crs)
    return float(projected.x), float(projected.y), 2180


def _geometry_metrics(parcel_geom: BaseGeometry) -> dict[str, Any]:
    """Compute the §8.6 geometry metrics quick_screening reports (§4.1)."""
    return {
        "area_m2": round(plot_geo.area_m2(parcel_geom), 3),
        "perimeter_m": round(plot_geo.perimeter_m(parcel_geom), 3),
        "compactness": round(plot_geo.compactness(parcel_geom), 4),
        "convexity": round(plot_geo.convexity(parcel_geom), 4),
        "irregularity": round(plot_geo.irregularity(parcel_geom), 4),
        "main_axis_length_m": round(plot_geo.main_axis(parcel_geom).length_m, 3),
        "main_axis_azimuth_deg": round(plot_geo.main_axis(parcel_geom).azimuth_deg, 2),
    }


def _parcel_bbox(parcel_geom: BaseGeometry) -> BBox:
    """Analysis-window bbox (parcel bounds + padding), minimised for fetch (F-0083)."""
    minx, miny, maxx, maxy = parcel_geom.bounds
    return BBox(
        minx=minx - _BBOX_PAD_M,
        miny=miny - _BBOX_PAD_M,
        maxx=maxx + _BBOX_PAD_M,
        maxy=maxy + _BBOX_PAD_M,
        crs="EPSG:2180",
    )


def _features_to_geoms(norm: Any, parcel_geom: BaseGeometry) -> list[BaseGeometry]:
    """Coerce WFS evidence geometries (GeoJSON) to shapely, dropping empties."""
    geoms: list[BaseGeometry] = []
    for ev in norm.evidence:
        if ev.geometry is None:
            continue
        try:
            g = shape(ev.geometry)
        except (ValueError, TypeError, KeyError):
            continue
        if not g.is_empty:
            geoms.append(g)
    return geoms


def _theme_of(constraint: Any) -> RiskKind | None:
    try:
        return RiskKind(constraint.constraint_type)
    except (ValueError, TypeError):
        return None


def _scores(
    parcel_geom: BaseGeometry,
    envelope: BuildableEnvelope,
    registry: RulesetRegistry,
    *,
    unknowns_count: int,
) -> AnalysisScores:
    """Map the computable Phase 4 §14 scores onto the §10.7 score block.

    The §10.7 schema has fixed numeric fields; the Phase 4 ScoreEvaluator produces the
    computable ones (buildability, data_confidence, coverage). Non-computable §14 scores
    stay 0.0 (their unknown status is carried in the result's unknowns, never faked —
    §20.10).
    """
    ctx = AnalysisContext(
        parcel=parcel_geom,
        buildable_envelope=shape(envelope.geometry) if envelope.geometry else parcel_geom,
        ruleset=registry,
        analysis_mode="quick_screening",
    )
    evaluated = ScoreEvaluator().evaluate(ctx)
    buildability = evaluated.value("buildability_score") or 0.0
    data_conf = evaluated.value("data_confidence_score") or 0.0
    # data confidence is reduced by outstanding unknowns (low certainty raises data risk).
    if unknowns_count:
        data_conf = max(0.0, data_conf - 0.1 * min(unknowns_count, 3))
    return AnalysisScores(
        buildability=round(buildability, 4),
        data_confidence=round(data_conf, 4),
        # The remaining §14 scores require Phase 8/10 data — left at 0.0 (unknowns persist).
    )


def _planning_summary(
    parcel: Parcel | None,
    layer_status: dict[str, str],
    registry: RulesetRegistry,
    *,
    parcel_geom: BaseGeometry | None = None,
    planning_store: Any | None = None,
) -> dict[str, Any]:
    """Build the planning context summary block (§4.1 "MPZP/POG/WZ coverage").

    Phase 8: when the planning store holds parsed APP/GML zones that intersect the
    parcel, the block reports real zone coverage (acts + symbols + coverage % +
    stability trace, F-0108–0111/F-0126). An empty/non-intersecting store keeps the
    explicit pending status — never silently claiming "no plan" (§21).
    """
    from dataclasses import asdict

    from plot_planning import DEFAULT_PLANNING_STORE, stability_score, zone_coverage

    summary: dict[str, Any] = {
        "mpzp_pog_wz": "coverage_check_pending",
        "coverage_status": "no_planning_data_in_store",
        "note": (
            "Brak zaimportowanych aktów planistycznych (APP/GML) dla tej lokalizacji — "
            "to nie oznacza braku planu (§21). Użyć planning_fetch / dostarczyć dokument."
        ),
        "ruleset_version": registry.ruleset_version,
        "municipality": (
            parcel.administrative_context.commune
            if parcel and parcel.administrative_context
            else None
        ),
    }
    store = planning_store if planning_store is not None else DEFAULT_PLANNING_STORE
    if parcel_geom is None or store.is_empty():
        return summary
    coverage = zone_coverage(parcel_geom, store.zones_for(None))
    if not coverage:
        summary["coverage_status"] = "no_zone_intersection_in_store"
        return summary
    act_ids = {c.act_id for c in coverage}
    acts = [a for a in (store.act_by_id(aid) for aid in sorted(act_ids)) if a is not None]
    summary.update(
        {
            "mpzp_pog_wz": "covered",
            "coverage_status": "parsed",
            "zone_coverage": [asdict(c) for c in coverage],
            "acts": [
                {**a.model_dump(mode="json"), "stability": stability_score(a)} for a in acts
            ],
            "note": "Pokrycie stref planistycznych z APP/GML (Phase 8, F-0108–0111).",
        }
    )
    return summary


def _no_parcel_result(
    analysis_id: str,
    parcel: Parcel | None,
    evidence: list[EvidenceItem],
    sources: list[SourceRecord],
) -> AnalysisResult:
    """Partial result when the parcel could not be resolved (still useful, NFR-REL-010)."""
    from plot_domain import Recommendation, Severity, UnknownItem

    unknown = UnknownItem(
        id=f"unk:parcel:{uuid.uuid4().hex[:8]}",
        analysis_id=analysis_id,
        topic="Identyfikacja działki",
        severity=Severity.HIGH,
        reason="not_detected" if sources else "source_unavailable",
        suggested_action="Podać poprawny identyfikator działki lub punkt; sprawdzić dostępność ULDK.",
    )
    action = Recommendation(
        id=f"rec:{uuid.uuid4().hex[:8]}",
        analysis_id=analysis_id,
        title="Doprecyzować lokalizację działki",
        detail="Geometria działki nie została ustalona — uzupełnić identyfikator lub punkt.",
        priority=Severity.HIGH,
    )
    from plot_domain import Decision

    return AnalysisResult(
        analysis_id=analysis_id,
        status=AnalysisStatus.PARTIAL,
        decision=Decision.NEEDS_MANUAL_REVIEW,
        parcel=parcel,
        unknowns=[unknown],
        next_actions=[action],
        evidence=evidence,
        planning={"_sources": [s.model_dump(mode="json") for s in sources]},
    )
