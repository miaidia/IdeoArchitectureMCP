"""Reloadable use-case layer for the MCP server (Phase 2 §2.1.2 / §2.4; Phase 7 §E).

The thin tool functions in ``server.py`` delegate all domain work to the functions
in this module. This module holds NO MCP/transport state, so it can be safely
``importlib.reload``-ed by the ``dev_reload`` tool / file watcher without touching
the live stdio pipe to Claude Code (Phase 2 anti-pattern: never reload the transport
from within itself — IMPLEMENTATION_PLAN.md Phase 0.5 / §2.4).

Phase 7 §E wires the MVP analysis surface to the REAL §27 shared use-cases
(``plot_agent.analysis.run_quick_screening``): ``parcel_resolve`` → ULDK,
``parcel_analyze`` (quick_screening) → orchestrator, ``analysis_get_status`` /
``analysis_get_result`` → the in-memory :class:`~plot_agent.analysis.AnalysisStore`,
``report_generate`` (md/json) → the real report, ``risks_list`` → real risks/unknowns,
``sources_collect`` → the evidence pack. Connectors are INJECTED (default = the
production bundle); tests pass mocks so no live network is used. The buildable envelope
is exposed as the ``analysis://{id}/buildable-envelope.geojson`` resource, never inlined
(NFR-PERF-009). Phases the MVP gate does not cover (capacity, full planning parse) stay
stubs and record that in their ``unknowns`` / notes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from plot_agent.analysis import (
    DEFAULT_STORE,
    Connectors,
    build_default_connectors,
    run_quick_screening,
)
from plot_domain import (
    AnalysisInput,
    AnalysisResult,
    AnalysisRun,
    BuildableEnvelope,
    Constraint,  # noqa: F401  (re-exported shape used by stubs/tests indirectly)
    Parcel,
    Recommendation,
    RiskItem,
    SourceRecord,
    UnknownItem,
)
from plot_domain.enums import (
    AnalysisMode,
    AnalysisStatus,
    Decision,
    Severity,
)

# Marker version so reload is observable in tests/diagnostics even when ruleset
# content is unchanged. Bump-by-reload is verified via the registry hash, but this
# string also lets a test confirm the module object was re-imported.
USECASES_BUILD = "phase13-orchestration"

PHASE7_NOTE = "Stub: analysis logic lands in Phase 7; MCP delegates to the worker in Phase 12."

# Injectable connector bundle for the analysis use-cases. The MCP server uses the
# production bundle by default; tests set this to a mock so the run is zero-network.
_CONNECTORS: Connectors | None = None


def set_connectors(connectors: Connectors | None) -> None:
    """Override the connector bundle used by ``parcel_analyze`` (tests inject mocks)."""
    global _CONNECTORS
    _CONNECTORS = connectors


def _get_connectors() -> Connectors:
    if _CONNECTORS is not None:
        return _CONNECTORS
    return build_default_connectors()


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _run_async(coro: Any) -> Any:
    """Run an async use-case from a SYNC MCP tool function.

    The FastMCP tool wrappers here are synchronous but are invoked from inside the
    server's running event loop, so ``asyncio.run`` would raise "loop already running".
    We run the coroutine on a dedicated worker thread with its own loop instead. When
    there is NO running loop (unit tests calling the use-case directly), we just use
    ``asyncio.run``.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop → safe to drive it directly.
        return asyncio.run(coro)

    # A loop is running on this thread; offload to a worker thread with a fresh loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# --------------------------------------------------------------------------- #
# Read-only analysis surface (§10.3)
# --------------------------------------------------------------------------- #
def parcel_resolve(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a parcel via ULDK (id or point) — real Phase 6 connector (Phase 7 §E).

    Returns a Parcel-shaped dict. On a source failure / not-found it returns a Parcel with
    a ``source_id`` of ``no_source`` and the original input echoed, never a fabricated
    geometry (§21). Runs the async connector via :func:`anyio.from_thread`-safe runner.
    """
    from plot_connectors import ParcelByIdQuery, ParcelByXYQuery, ResultStatus, SourceUnavailable

    connectors = _get_connectors()
    parcel_id = payload.get("parcel_id")
    point = payload.get("point")

    async def _resolve() -> dict[str, Any]:
        query: ParcelByIdQuery | ParcelByXYQuery | None = None
        if parcel_id:
            query = ParcelByIdQuery(parcel_id=str(parcel_id))
        elif isinstance(point, dict) and "x" in point and "y" in point:
            srid = 2180
            crs = str(point.get("crs", "EPSG:2180")).upper()
            if crs in ("EPSG:2180", "2180"):
                query = ParcelByXYQuery(x=float(point["x"]), y=float(point["y"]), srid=srid)
        if query is None:
            return Parcel(id=_new_id(), external_id=parcel_id, number=parcel_id).model_dump(mode="json")
        try:
            norm = await connectors.uldk.resolve(query, snapshot=False)
        except SourceUnavailable:
            return Parcel(
                id=_new_id(), external_id=parcel_id, number=parcel_id, source_id="no_source"
            ).model_dump(mode="json")
        if norm.status is ResultStatus.OK and "parcel" in norm.payload:
            return norm.payload["parcel"]
        return Parcel(id=_new_id(), external_id=parcel_id, number=parcel_id).model_dump(mode="json")

    return _run_async(_resolve())


def parcel_analyze(payload: AnalysisInput, ruleset_version: str) -> AnalysisResult:
    """Run the analysis (Phase 7 §C/§E + Phase 12).

    * ``quick_screening`` → :func:`plot_agent.analysis.run_quick_screening`;
    * ``full_due_diligence`` (Phase 12) → :func:`plot_agent.analysis
      .run_full_due_diligence`: the screening pipeline PLUS the site-context
      modules (terrain/water/geology/environment/heritage/roads/utilities/
      neighborhood) over the same fetched layers, §14 scores wired, typed site
      context stored for the masterplan integration;
    * other modes (design/portfolio) run the quick pipeline (never a fabricated
      full result — their gap stays visible in the result contents).

    The completed result is stored in the in-memory store so ``analysis_get_result`` /
    ``report_generate`` / ``risks_list`` / ``sources_collect`` can return it.
    """
    from plot_agent.analysis import DEFAULT_SITE_CONTEXT_STORE, run_full_due_diligence

    connectors = _get_connectors()
    analysis_id = _new_id()

    async def _analyze() -> AnalysisResult:
        if payload.analysis_mode is AnalysisMode.FULL_DUE_DILIGENCE:
            return await run_full_due_diligence(
                payload,
                connectors=connectors,
                analysis_id=analysis_id,
                site_store=DEFAULT_SITE_CONTEXT_STORE,
            )
        return await run_quick_screening(
            payload,
            connectors=connectors,
            analysis_id=analysis_id,
        )

    result = _run_async(_analyze())
    DEFAULT_STORE.put(result)
    return result


def analysis_get_status(analysis_id: str) -> dict[str, Any]:
    """Return status + streaming progress for an analysis or task graph (Phase 13).

    Resolution order (F-0434 / NFR-PERF-010):

    1. a TASK GRAPH whose graph id or bound analysis id matches → live graph
       status (``running`` / ``awaiting_review`` incl. WHAT to review /
       ``complete`` / ``aborted``), per-node progress and the ordered event
       tail from the orchestrator status store (what ``ctx.report_progress``
       streams in ``server.py``);
    2. a stored completed analysis → the Phase 7 result-status shape;
    3. neither → ``not_found`` (a normal answer, never an error).
    """
    from plot_agent.orchestrator import DEFAULT_GRAPH_STORE, DEFAULT_STATUS_STORE

    graph_state = DEFAULT_GRAPH_STORE.get(analysis_id)
    if graph_state is None:
        # The id may be the ANALYSIS id of a graph keyed under its own graph id.
        for state in DEFAULT_GRAPH_STORE.all_states():
            if state.analysis_id == analysis_id:
                graph_state = state
                break
    if graph_state is not None:
        events = DEFAULT_STATUS_STORE.events(graph_state.graph_id)
        review: dict[str, Any] | None = None
        if graph_state.pending_gate is not None:
            gate_outcome = graph_state.outcomes.get(graph_state.pending_gate)
            pending_review = graph_state.pending_review or {}
            review = {
                "node_id": graph_state.pending_gate,
                "what_to_review": next(
                    (
                        e.get("detail")
                        for e in reversed(events)
                        if e.get("state") == "awaiting_review"
                    ),
                    graph_state.pending_gate,
                ),
                "review_payload_available": gate_outcome is not None,
                # Review m6: the ACTUAL payload under review (size-capped at
                # pause time by the orchestrator) — not just a flag.
                "review_payload": pending_review.get("review_payload"),
            }
        node_states = {nid: o.status for nid, o in graph_state.outcomes.items()}
        return {
            "analysis_id": graph_state.analysis_id or analysis_id,
            "graph_id": graph_state.graph_id,
            "status": graph_state.status,
            "progress": graph_state.progress(),
            "nodes": node_states,
            "awaiting_review": review,
            "events": events[-20:],  # ordered tail (NFR-PERF-009: never unbounded)
            "partial_available": any(
                o.status in ("ok", "degraded") for o in graph_state.outcomes.values()
            ),
        }

    result = DEFAULT_STORE.get(analysis_id)
    if result is None:
        return {
            "analysis_id": analysis_id,
            "status": "not_found",
            "progress": 0.0,
            "partial_available": False,
        }
    return {
        "analysis_id": analysis_id,
        "status": result.status.value,
        "decision": result.decision.value,
        "progress": 1.0,
        "partial_available": result.status is AnalysisStatus.PARTIAL,
    }


def analysis_get_result(analysis_id: str) -> AnalysisResult:
    """Return the stored structured result (Phase 7 §E).

    A missing id yields a schema-valid partial result flagging the unknown id (never an
    error — manual_review_required / partial are normal statuses, §20.12).
    """
    result = DEFAULT_STORE.get(analysis_id)
    if result is not None:
        return result
    return AnalysisResult(
        analysis_id=analysis_id,
        status=AnalysisStatus.PARTIAL,
        decision=Decision.NEEDS_MANUAL_REVIEW,
        unknowns=[
            UnknownItem(
                id=_new_id(),
                analysis_id=analysis_id,
                topic="analysis_run",
                severity=Severity.INFO,
                reason="not_found",
                suggested_action="Uruchomić parcel_analyze, aby utworzyć analizę o tym id.",
            )
        ],
    )


def planning_fetch(municipality_id: str | None, parcel_id: str | None) -> dict[str, Any]:
    """Planning context: acts + zones + parcel coverage from the planning store (Phase 8).

    Acts/zones come from parsed APP/GML in :data:`plot_planning.DEFAULT_PLANNING_STORE`
    (ingested via :func:`planning_ingest_gml` / :func:`planning_ingest_from_url` — the
    live path goes through the egress-allowlisted ``AppGmlConnector``). When ``parcel_id``
    is given the parcel is resolved (ULDK, injected connectors) and intersected with the
    zones → coverage % (F-0108–0111). An empty store yields ``no_planning_data`` — data
    absence is NEVER reported as "no plan exists" (§21).
    """
    from dataclasses import asdict

    from plot_planning import DEFAULT_PLANNING_STORE, stability_score, use_matrix_for, zone_coverage

    store = DEFAULT_PLANNING_STORE
    municipality_ids = [municipality_id] if municipality_id else store.municipalities()
    acts = [a for mid in municipality_ids for a in store.acts_for(mid)]
    zones = [z for mid in municipality_ids for z in store.zones_for(mid)]

    if not acts:
        return {
            "municipality_id": municipality_id,
            "parcel_id": parcel_id,
            "acts": [],
            "zones": [],
            "coverage": [],
            "coverage_status": "no_planning_data",
            "note": (
                "Brak zaimportowanych aktów planistycznych dla tej gminy w magazynie — "
                "to NIE oznacza braku planu (§21). Zaimportować APP/GML (rejestr "
                "urbanistyczny / gmina) albo dostarczyć dokument przez planning_parse_document."
            ),
        }

    coverage: list[dict[str, Any]] = []
    coverage_status = "acts_listed"
    if parcel_id:
        parcel_payload = parcel_resolve({"parcel_id": parcel_id})
        geom = _payload_geometry(parcel_payload)
        if geom is not None:
            coverage = [asdict(c) for c in zone_coverage(geom, zones)]
            coverage_status = "computed"
        else:
            coverage_status = "parcel_not_resolved"

    return {
        "municipality_id": municipality_id,
        "parcel_id": parcel_id,
        "acts": [
            {**a.model_dump(mode="json"), "stability": stability_score(a)} for a in acts
        ],
        "zones": [
            {
                "id": z.id,
                "act_id": z.act_id,
                "symbol": z.symbol,
                "has_geometry": z.geometry is not None,
                "attributes": z.attributes,
                "use_matrix": use_matrix_for(z.symbol),
            }
            for z in zones
        ],
        "coverage": coverage,
        "coverage_status": coverage_status,
        "note": "Pokrycie stref planistycznych z APP/GML (XSD v2.0, Dz.U. 2023 poz. 2409).",
    }


def _payload_geometry(parcel_payload: dict[str, Any]) -> Any:
    """Coerce a Parcel-shaped dict (WKT or GeoJSON, EPSG:2180) to shapely or None."""
    from shapely import from_wkt
    from shapely.geometry import shape

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


def planning_ingest_gml(
    content: bytes | str, municipality_id: str, source_id: str | None = None
) -> dict[str, Any]:
    """Parse an APP/GML document and ingest it into the planning store (F-0108/0109).

    Not a public MCP tool — called by the connector-driven path below and by tests
    (recorded fixtures). Malformed/unsafe GML raises through as a clear error.
    """
    from plot_planning import DEFAULT_PLANNING_STORE, parse_app_gml

    parsed = parse_app_gml(content, municipality_id=municipality_id, source_id=source_id)
    DEFAULT_PLANNING_STORE.ingest(municipality_id, parsed)
    return {
        "municipality_id": municipality_id,
        "acts_ingested": [a.id for a in parsed.acts],
        "zones_ingested": len(parsed.zones),
        "warnings": parsed.warnings,
    }


def planning_ingest_from_url(
    url: str, municipality_id: str, connector: Any | None = None
) -> dict[str, Any]:
    """Live APP/GML path: fetch via the egress-allowlisted connector → parse → store.

    Reuses :class:`plot_connectors.AppGmlConnector` (SSRF allowlist + snapshot,
    §20.13); the raw bytes are snapshotted before parsing so the source artifact is
    reproducible. Tests inject a respx-mocked ``connector`` (zero live network).
    """
    from plot_connectors import AppGmlConnector, AppGmlQuery
    from plot_connectors.profiles import get_profile

    conn = connector or AppGmlConnector(get_profile("pl.app.gml.planning"))

    async def _fetch() -> tuple[Any, str]:
        raw = await conn.fetch(AppGmlQuery(url=url))  # egress allowlist runs here
        snapshot_uri = await conn.snapshot(raw)
        return raw, snapshot_uri

    raw, snapshot_uri = _run_async(_fetch())
    result = planning_ingest_gml(raw.content, municipality_id, source_id=conn.source_id)
    result["snapshot_uri"] = snapshot_uri
    result["url"] = url
    return result


def planning_parse_document(
    file_id: str | None, text: str | None, candidates: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Parse a planning document with evidence (Phase 8, §29 parser DoD).

    Two modes — the server never calls an LLM itself:

    * **(a) deterministic** (``candidates is None``): regex extractors over ``text``
      for the §8.1.3 indicator set, each with a verbatim source fragment + offsets;
    * **(b) candidate validation**: the calling model supplies its own LLM
      extraction as ``candidates``; each is validated against
      ``schemas/planning-indicators.schema.json`` AND its cited fragment must occur
      verbatim in the document — otherwise it is rejected with a reason (F-0550).

    Runs in untrusted-content mode (NFR-SEC-002/003/009): size cap, binary
    rejection, injection screening; document text never steers the result. Parsed
    indicators become ``PlanningIndicator`` records with citations; missing
    indicators become ``UnknownItem`` records — never defaults (§21).
    """
    from plot_domain import EvidenceItem, PlanningIndicator
    from plot_planning import (
        extract_indicators,
        missing_indicators,
        screen_document,
        validate_candidates,
    )

    source_type = "text"
    if text is None:
        if file_id:
            # Phase 14B: read the sandboxed upload's extracted text (F-0491–0493).
            from plot_security import DEFAULT_UPLOAD_STORE

            record = DEFAULT_UPLOAD_STORE.get(file_id)
            if record is None:
                return {
                    "file_id": file_id,
                    "source_type": "file",
                    "indicators": [],
                    "evidence": [],
                    "unknowns": [],
                    "rejected": [],
                    "status": "file_not_found",
                    "note": (
                        "Brak uploadu o tym file_id w sandboxie — wgrać plik przez "
                        "HTTP API (POST /v1/documents/ingest) albo podać 'text'."
                    ),
                }
            if record.text is None:
                return {
                    "file_id": file_id,
                    "source_type": "file",
                    "indicators": [],
                    "evidence": [],
                    "unknowns": [],
                    "rejected": [],
                    "status": "text_extraction_unavailable",
                    "note": (
                        f"Upload '{record.filename}' ({record.declared_type}) nie ma "
                        "wyekstrahowanego tekstu — ekstrakcja tekstu z PDF nie jest "
                        "dostępna w tym środowisku (uczciwy brak, §21); dostarczyć "
                        "treść parametrem 'text'."
                    ),
                }
            text = record.text
            source_type = "file"
        else:
            return {
                "file_id": None,
                "source_type": None,
                "indicators": [],
                "evidence": [],
                "unknowns": [],
                "rejected": [],
                "status": "no_input",
                "note": "Wymagany file_id albo text.",
            }

    screen = screen_document(text)
    if not screen.ok:
        return {
            "file_id": file_id,
            "source_type": source_type,
            "indicators": [],
            "evidence": [],
            "unknowns": [],
            "rejected": [],
            "status": "rejected_input",
            "reason": screen.reason,
            "security": screen.security_block(),
        }

    rejected: list[dict[str, Any]] = []
    if candidates is not None:
        validation = validate_candidates(text, candidates)
        extractions = validation.accepted
        rejected = [{"candidate": r.candidate, "reasons": r.reasons} for r in validation.rejected]
        mode = "candidate_validation"
    else:
        extractions = extract_indicators(text)
        mode = "deterministic"

    source_record_id = f"doc:{file_id or 'text'}:{uuid.uuid4().hex[:8]}"
    indicators: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for ext in extractions:
        ev = EvidenceItem(
            id=f"ev:{uuid.uuid4().hex[:8]}",
            analysis_id="",
            source_id=source_record_id,
            subject_type="planning_provision",
            subject_id=ext.name,
            claim=f"planning indicator '{ext.name}' extracted with verbatim citation",
            value_json={
                "fragment": ext.source_fragment.model_dump(),
                "method": ext.method,
                "redacted": ext.redacted,
            },
            confidence=ext.confidence,
            created_at=_now(),
        )
        indicator = PlanningIndicator(
            id=f"ind:{uuid.uuid4().hex[:8]}",
            name=ext.name,
            value=ext.value,
            unit=ext.unit,
            source_id=source_record_id,
            evidence_id=ev.id,
            confidence=ext.confidence,
        )
        evidence.append(ev.model_dump(mode="json"))
        indicators.append(indicator.model_dump(mode="json"))

    from plot_domain import UnknownItem as _UnknownItem
    from plot_domain.enums import Severity as _Severity

    unknowns = [
        _UnknownItem(
            id=f"unk:{uuid.uuid4().hex[:8]}",
            analysis_id="",
            topic=f"planning_indicator:{name}",
            severity=_Severity.MEDIUM,
            reason="not_found_in_document",
            suggested_action=(
                "Sprawdzić pełny tekst uchwały / zapytać gminę — wskaźnik pozostaje "
                "unknown, nigdy wartość domyślna (§21)."
            ),
        ).model_dump(mode="json")
        for name in missing_indicators(extractions)
    ]

    status = "parsed"
    if rejected or screen.injection_flags:
        # Rejected candidates / suspected injection → human eyes (§29 DoD).
        status = "manual_review_required"

    return {
        "file_id": file_id,
        "source_type": source_type,
        "mode": mode,
        "indicators": indicators,
        "evidence": evidence,
        "unknowns": unknowns,
        "rejected": rejected,
        "security": screen.security_block(),
        "status": status,
        "note": (
            "Każdy wskaźnik cytuje fragment źródłowy (NFR-AUD-002); kandydaci bez "
            "weryfikowalnego cytatu odrzuceni (F-0550); braki pozostają unknown."
        ),
    }


def _inter_building_rules_summary(ruleset_dir: str = "rulesets/PL") -> dict[str, Any]:
    """The Phase 10 inter-building WT/ppoż rules in force (plan §10.1.7).

    ``constraints_compute`` exposes WHICH rules the masterplan validators enforce
    (id + title + Dz.U. citation, freshly loaded — hot-reload semantics); the
    CHECKS themselves need a concrete masterplan proposal, so they are evaluated
    in the ``propose_layout`` masterplan path (and travel with the stored variant).
    """
    from plot_planning.wt_validators import CONSUMED_RULE_IDS
    from plot_rules import load_rulesets

    registry = load_rulesets(ruleset_dir)
    rules = []
    for rule_id in CONSUMED_RULE_IDS:
        rule = registry.get(rule_id)
        rules.append(
            {
                "rule_id": rule_id,
                "loaded": rule is not None,
                "title": rule.title if rule else None,
                "severity": rule.severity if rule else None,
                "source_reference": rule.source_reference if rule else None,
            }
        )
    return {
        "rules": rules,
        "ruleset_version": registry.ruleset_version,
        "evaluated_by": "propose_layout (masterplan path, Phase 10 wt_validators)",
    }


def constraints_compute(analysis_id: str | None) -> dict[str, Any]:
    """Return the real constraints + buildable envelope from a stored analysis (Phase 7).

    The heavy geometry (envelope GeoJSON) is exposed via the
    ``analysis://{id}/buildable-envelope.geojson`` resource (NFR-PERF-009); this tool
    returns the constraint records + an envelope summary (area / confidence / lir
    present) + the Phase 10 inter-building WT/ppoż rules in force (§10.1.7).
    """
    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None
    if result is None:
        envelope = BuildableEnvelope(id=_new_id(), analysis_id=analysis_id, area_m2=None)
        return {
            "constraints": [],
            "buildable_envelope": envelope.model_dump(mode="json"),
            "status": "not_found",
        }
    env = result.buildable_envelope
    return {
        "analysis_id": analysis_id,
        "constraints": [c.model_dump(mode="json") for c in result.constraints],
        "buildable_envelope_summary": {
            "area_m2": env.area_m2 if env else None,
            "confidence": env.confidence if env else None,
            "has_largest_inscribed_rectangle": bool(env and env.largest_inscribed_rectangle),
            "geojson_resource": f"analysis://{analysis_id}/buildable-envelope.geojson",
            "removed_by": env.metadata.get("removed_by", []) if env and isinstance(env.metadata, dict) else [],
        },
        "inter_building_rules": _inter_building_rules_summary(),
    }


def buildable_envelope_geojson(analysis_id: str) -> dict[str, Any]:
    """The buildable-envelope FeatureCollection for a stored analysis (§10.4).

    Shared by the MCP ``analysis://{id}/buildable-envelope.geojson`` resource AND
    the HTTP ``GET /v1/analyses/{id}/buildable-envelope`` endpoint (§27 — one
    assembly, two surfaces). Fetched on demand, never inlined into tool results
    (NFR-PERF-009).
    """
    result = DEFAULT_STORE.get(analysis_id)
    if result is None or result.buildable_envelope is None:
        return {"type": "FeatureCollection", "features": [], "status": "not_found"}
    env = result.buildable_envelope
    features: list[dict[str, Any]] = []
    if result.parcel and result.parcel.geometry:
        features.append(
            {"type": "Feature", "properties": {"role": "parcel"}, "geometry": result.parcel.geometry}
        )
    if env.geometry:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "role": "buildable_envelope",
                    "area_m2": env.area_m2,
                    "confidence": env.confidence,
                },
                "geometry": env.geometry,
            }
        )
    if env.largest_inscribed_rectangle:
        features.append(
            {
                "type": "Feature",
                "properties": {"role": "largest_inscribed_rectangle"},
                "geometry": env.largest_inscribed_rectangle,
            }
        )
    return {"type": "FeatureCollection", "crs_note": "EPSG:2180", "features": features}


def design_brief_for_analysis(
    analysis_id: str,
    *,
    ruleset_dir: str | None = None,
    context_edges: list[dict[str, Any]] | None = None,
    heritage_footprints: list[dict[str, Any]] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Build (or return the cached) design brief for a stored analysis (Phase 11 §11.1.1).

    Backs the ``analysis://{analysis_id}/design-brief`` resource: the brief is
    generated lazily from the stored :class:`AnalysisResult` (parcel geometry +
    buildable envelope + planning indicators), kept in
    :data:`plot_planning.DEFAULT_BRIEF_STORE` (variants-store pattern) and returned as
    BOTH the structured model dump (structuredContent) and the rendered Markdown
    (what the model reads). ``refresh=True`` regenerates (e.g. after hot-reloading a
    typology YAML — the registry is loaded fresh per call either way). Explicitly
    provided ``context_edges``/``heritage_footprints`` imply a refresh too (review
    fix F3): a cached bare brief must never silently swallow new arguments.
    """
    from plot_planning import DEFAULT_BRIEF_STORE, generate_design_brief
    from plot_rules import load_rulesets
    from shapely.geometry import shape

    explicit_inputs = context_edges is not None or heritage_footprints is not None
    cached = DEFAULT_BRIEF_STORE.get(analysis_id)
    if cached is not None and not refresh and not explicit_inputs:
        return {
            "status": "ok",
            "analysis_id": analysis_id,
            "brief": cached.model_dump(mode="json"),
            "markdown": cached.to_markdown(),
        }

    result = DEFAULT_STORE.get(analysis_id)
    if result is None or result.parcel is None or result.parcel.geometry is None:
        return {
            "status": "not_found",
            "analysis_id": analysis_id,
            "note": "Brak zapisanej analizy z geometrią działki — uruchom parcel_analyze.",
        }

    indicators = _indicator_map(result.planning.get("indicators"))
    srodmiejska = bool(indicators.get("zabudowa_srodmiejska") is True)
    brief = generate_design_brief(
        shape(result.parcel.geometry),
        registry=load_rulesets(ruleset_dir or "rulesets/PL"),
        envelope=result.buildable_envelope,
        indicators=indicators,
        heritage_footprints=heritage_footprints or [],
        context_edges=context_edges or [],
        srodmiejska=srodmiejska,
        analysis_id=analysis_id,
    )
    DEFAULT_BRIEF_STORE.put(analysis_id, brief)
    return {
        "status": "ok",
        "analysis_id": analysis_id,
        "brief": brief.model_dump(mode="json"),
        "markdown": brief.to_markdown(),
    }


def _indicator_map(
    indicators: dict[str, Any] | list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Normalise indicators to a ``name -> value`` map (Phase 9 §9.1.2).

    Accepts the plain map form OR the ``planning_parse_document`` indicator list
    (``PlanningIndicator``-shaped dicts with ``name``/``value``). Only the canonical
    Phase 8 indicator names are kept — the capacity engine reads EXACT keys.
    """
    from plot_planning import INDICATOR_NAMES

    if not indicators:
        return {}
    if isinstance(indicators, dict):
        return {k: v for k, v in indicators.items() if k in INDICATOR_NAMES and v is not None}
    out: dict[str, Any] = {}
    for item in indicators:
        name = item.get("name")
        if name in INDICATOR_NAMES and item.get("value") is not None:
            out[str(name)] = item["value"]
    return out


def capacity_generate_scenarios(
    analysis_id: str | None,
    indicators: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Real capacity scenarios (Phase 9 §9.1.2; F-0202–0206) — no drawing required.

    Computes conservative/base/optimistic/max scenarios + sensitivity from the STORED
    analysis' buildable envelope and the supplied Phase 8 MPZP/WZ indicators (the
    ``planning_parse_document`` output or a plain ``name -> value`` map). Missing
    indicators propagate as ``unknowns`` and partial scenarios — never defaults (§0v2.4).
    """
    from plot_planning import CapacityConfig
    from plot_planning import generate_capacity_scenarios as _generate

    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None
    if result is None:
        return {
            "analysis_id": analysis_id,
            "scenarios": [],
            "status": "not_found",
            "note": (
                "Brak zapisanej analizy o tym id — uruchomić parcel_analyze; chłonność "
                "liczy się z buildable envelope tej analizy (F-0202–0205)."
            ),
        }
    envelope = result.buildable_envelope
    if envelope is None or not envelope.area_m2:
        return {
            "analysis_id": analysis_id,
            "scenarios": [],
            "status": "no_envelope",
            "note": "Analiza nie ma policzonego buildable envelope — chłonność niedostępna.",
        }
    parcel_area: float | None = None
    if result.parcel is not None and result.parcel.area_m2:
        parcel_area = float(result.parcel.area_m2)
    elif isinstance(envelope.metadata, dict) and envelope.metadata.get("parcel_area_m2"):
        parcel_area = float(envelope.metadata["parcel_area_m2"])
    if parcel_area is None or parcel_area <= 0:
        return {
            "analysis_id": analysis_id,
            "scenarios": [],
            "status": "no_parcel_area",
            "note": "Brak powierzchni działki — wskaźniki coverage/intensywności nieobliczalne.",
        }

    cfg = CapacityConfig()
    scenario_set = _generate(
        envelope_area_m2=float(envelope.area_m2),
        parcel_area_m2=parcel_area,
        indicators=_indicator_map(indicators),
        config=cfg,
        analysis_id=analysis_id,
    )
    return {
        "analysis_id": analysis_id,
        "envelope_area_m2": float(envelope.area_m2),
        "parcel_area_m2": parcel_area,
        **scenario_set.to_dict(),
        "config": cfg.basis_block(),
        "status": "computed",
        "note": (
            "Scenariusze conservative/base/optimistic/max z buildable envelope + "
            "wskaźników MPZP/WZ (F-0202–0206); brakujące wskaźniki pozostają unknown "
            "(nigdy wartości domyślne, §0v2.4)."
        ),
    }


def risks_list(analysis_id: str | None) -> dict[str, Any]:
    """Return the real red flags / risk register / unknowns for a stored analysis (§11.2)."""
    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None
    if result is None:
        return {"analysis_id": analysis_id, "risks": [], "unknowns": [], "status": "not_found"}
    return {
        "analysis_id": analysis_id,
        "decision": result.decision.value,
        "risks": [r.model_dump(mode="json") for r in result.risks],
        "unknowns": [u.model_dump(mode="json") for u in result.unknowns],
        "next_actions": [a.model_dump(mode="json") for a in result.next_actions],
    }


def sources_collect(analysis_id: str | None) -> dict[str, Any]:
    """Return the source records + evidence pack for a stored analysis (§5 / NFR-AUD-001).

    Phase 14 (F-0393/F-0407): the evidence pack (all SourceRecords +
    EvidenceItems with their claims/citations) is ALSO persisted as a standalone
    JSON artifact via the ArtifactStore so it is downloadable independently of
    the report — ``evidence_pack_uri`` + the ``analysis://{id}/evidence``
    resource link travel in the result (never the inlined pack twice).
    """
    import json as _json

    from plot_reports import get_artifact_store

    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None
    if result is None:
        return {"analysis_id": analysis_id, "sources": [], "evidence": [], "status": "not_found"}
    sources = result.planning.get("_sources", []) if isinstance(result.planning, dict) else []
    evidence = [e.model_dump(mode="json") for e in result.evidence]
    pack = {
        "analysis_id": analysis_id,
        "generated_at": _now().isoformat(),
        "sources": sources,
        "evidence": evidence,
        # Citations: every evidence claim with its backing source (the §5 chain).
        "citations": [
            {
                "claim": e["claim"],
                "source_id": e["source_id"],
                "subject": f"{e['subject_type']}:{e['subject_id']}",
                "confidence": e["confidence"],
            }
            for e in evidence
        ],
    }
    pack_uri = get_artifact_store().put(
        f"analysis/{analysis_id}/evidence-pack.json",
        _json.dumps(pack, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json",
    )
    return {
        "analysis_id": analysis_id,
        "sources": sources,
        "evidence": evidence,
        "evidence_count": len(result.evidence),
        "evidence_pack_uri": pack_uri,
        "evidence_pack_resource": f"analysis://{analysis_id}/evidence",
    }


def report_generate(
    analysis_id: str | None,
    fmt: str,
    variant_id: str | None = None,
    audience: str = "architect",
) -> dict[str, Any]:
    """Generate a report artifact for a stored analysis (Phase 7 §E; §22; Phase 14 §31).

    * ``md``  → the §22 Markdown report (returned inline as ``content`` — it is small text).
    * ``json``→ the AnalysisResult (§10.7) returned as ``content`` (and the canonical contract).
    * ``png`` → the buildable-envelope map rendered + persisted, advertised as a
      ``resource_link`` by DEFAULT (image NOT inlined into every result — NFR-PERF-009;
      inline image is only via the dedicated ``map_preview`` tool).
    * ``koncepcja`` (Phase 11 §11.1.7) → the multi-building chłonność concept
      deliverable assembled from the variant store + capacity metrics + design
      brief + rule/staging checks + the design-rationale audit; ``variant_id``
      selects the masterplan variant (default: the latest stored one).
    * ``html`` / ``pdf`` (Phase 14, §31 DoD) → rendered from the unified
      :class:`~plot_reports.ReportModel` (the SAME model behind md/json — one
      model, all formats): with an explicit ``variant_id`` the koncepcja
      deliverable, otherwise the screening report. The HTML/PDF artifact AND
      the model JSON land in the ArtifactStore (``resource_link``, never
      inlined). PDF needs the weasyprint system stack — when absent, the
      result is an honest ``pdf_unavailable`` with the reason (never a fake).
    * ``pzt-draft`` (Phase 15) → the PZT draft package per Dz.U. 2020 poz. 1609
      (t.j. 2022 poz. 1679) §13–18: część opisowa (§14, MD+JSON), część
      rysunkowa (§15, scaled vector SVG + PDF via the matplotlib PDF backend,
      e-form naming ``PZT_rrrr.mm.dd``), and the honest §13–18 checklist
      (done/missing/requires_projektant/requires_uprawnienia). ALWAYS carries
      the draft-not-projekt-budowlany disclaimer (§15.4).

    ``audience`` (F-0392, F-0403–F-0406: ``architect | investor | lawyer |
    bank``) selects the report SECTION LIST for md/html/pdf/koncepcja — same
    numbers, different emphasis (config in ``plot_reports.model``).
    """
    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None

    if fmt == "koncepcja":
        return _report_koncepcja(analysis_id, variant_id, audience=audience)

    if fmt in ("pzt-draft", "pzt_draft"):
        # Phase 15: the PZT draft package (§13–18 Dz.U. 2020/1609 t.j. 2022/1679)
        # — opisowa MD+JSON, rysunkowa SVG+vector PDF, §13–18 checklist. Same
        # report_generate tool, new format value (tool surface stays frozen).
        return _report_pzt_draft(analysis_id, variant_id)

    if fmt in ("html", "pdf"):
        return _report_model_format(analysis_id, fmt, variant_id, audience)

    if fmt in ("md", "markdown"):
        from plot_reports import build_screening_model, headline_numbers, render_model_markdown

        if result is None:
            return {"analysis_id": analysis_id, "format": "md", "status": "not_found"}
        # One model, all formats (§31): the architect output equals the Phase 7
        # render_markdown byte-for-byte; other audiences select fewer sections.
        model = build_screening_model(result, audience=audience)  # type: ignore[arg-type]
        model, pii_block = _shared_export_redaction(model, audience)
        return {
            "analysis_id": analysis_id,
            "format": "md",
            "audience": audience,
            "content": render_model_markdown(model),
            "headline_numbers": headline_numbers(result),
            "report_version": model.report_version,
            "analysis_snapshot_hash": model.analysis_snapshot_hash,
            "pii_redaction": pii_block,
            "status": "rendered",
        }

    if fmt == "json":
        from plot_reports import headline_numbers, render_json

        if result is None:
            return {"analysis_id": analysis_id, "format": "json", "status": "not_found"}
        return {
            "analysis_id": analysis_id,
            "format": "json",
            "content": render_json(result),
            "headline_numbers": headline_numbers(result),
            "status": "rendered",
        }

    if fmt == "png":
        from plot_reports import get_artifact_store, render_envelope_map, render_preview

        aid = analysis_id or _new_id()
        # Render the real buildable-envelope map when a stored result exists; otherwise
        # fall back to the Phase 3 sample preview (so the tool always returns a valid PNG).
        render = render_envelope_map(result) if result is not None else render_preview(analysis_id=aid, fmt="png")
        store = get_artifact_store()
        key = f"analysis/{aid}/map-preview.png"
        uri = store.put_render(key, render)
        return {
            "analysis_id": aid,
            "format": "png",
            # resource_link descriptor — Claude Code fetches the bytes on demand from
            # the analysis://{id}/map-preview.png resource (NFR-PERF-009 default path).
            "artifact_uri": uri,
            "resource_link": f"analysis://{aid}/map-preview.png",
            "mime_type": render.mime_type,
            "byte_size": len(render.data),
            "style_metadata": render.style_metadata,  # CRS + layers + style (NFR-AUD-009)
            "status": "rendered",
            "note": "Image returned as resource_link by default; inline via map_preview (NFR-PERF-009).",
        }

    return {
        "analysis_id": analysis_id,
        "format": fmt,
        "artifact_uri": None,
        "status": "unsupported_format",
        "note": (
            "Obsługiwane formaty raportu: md | html | pdf | json | png | "
            "koncepcja | pzt-draft (GIS/CAD przez export_layers)."
        ),
    }


def _shared_export_redaction(model: Any, audience: str) -> tuple[Any, dict[str, Any]]:
    """PII redaction pass for SHARED report exports (Phase 14B; F-0483).

    ``audience != architect`` (investor/lawyer/bank) is the share channel —
    parcel-owner fields (none are stored today; the hook guards future sources)
    are stripped from the :class:`~plot_reports.ReportModel` before rendering.
    Returns ``(model, pii_block)`` where the block records what was redacted.
    """
    from plot_reports import redact_model_for_sharing

    if audience == "architect":
        return model, {"applied": False, "redacted_fields": []}
    redacted_model, paths = redact_model_for_sharing(model)
    return redacted_model, {"applied": bool(paths), "redacted_fields": paths}


def map_preview_render(analysis_id: str | None, fmt: str = "png") -> Any:
    """Render the inline map for ``map_preview`` (Phase 3 channel + Phase 12 layers).

    A STORED analysis renders its real buildable-envelope map — including the
    Phase 12 site-context layers (flood/landslide/heritage as hard/soft
    constraint layers, utility networks as the NETWORK layer — the
    ``render_envelope_map`` role mapping). An UNKNOWN id is a hard error
    (review m1, matching ``propose_layout``'s ``_context_for`` semantics): a
    sample render is visually indistinguishable from the real map, so falling
    back silently would fake analysis output (§21). Only the documented no-id
    call renders the Phase 3 sample preview. Returns a
    ``plot_reports.RenderResult``.
    """
    from plot_reports import render_envelope_map, render_preview

    if analysis_id is None:
        return render_preview(analysis_id=None, fmt="png" if fmt != "svg" else "svg")
    result = DEFAULT_STORE.get(analysis_id)
    if result is None:
        raise ValueError(
            f"analysis_id '{analysis_id}' nie istnieje w magazynie analiz — najpierw "
            "uruchom parcel_analyze; map_preview bez analysis_id renderuje "
            "przykładowy podgląd (Phase 3)."
        )
    return render_envelope_map(result, fmt="png" if fmt != "svg" else "svg")


def _variant_lineage_entries(variant: Any) -> list[dict[str, Any]]:
    """Audit entries that produced ``variant`` — its variant-id parent chain (F1).

    Lineage key (review fix F1, documented choice): every masterplan audit entry
    is stamped with ``analysis_id`` + ``variant_id`` + ``parent_variant_id`` (the
    variant of the previous iteration in the SAME analysis — the entry whose score
    components seeded the critique). The koncepcja report walks that chain from the
    reported variant backwards and renders ONLY those entries' rationales, in
    chronological order. Every MCP masterplan iteration stores a variant, so the
    chain has no gaps; entries from other analyses/sessions can never appear.
    Since Phase 12 a bound ``propose_layout(analysis_id=...)`` stamps real
    analysis ids; unbound calls still share :data:`ADHOC_ANALYSIS_ID`.
    """
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT

    by_variant: dict[str, dict[str, Any]] = {}
    for entry in DEFAULT_MASTERPLAN_AUDIT.entries(analysis_id=variant.analysis_id):
        vid = entry.get("variant_id")
        if isinstance(vid, str) and vid:
            by_variant[vid] = entry
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor: Any = variant.id
    while isinstance(cursor, str) and cursor and cursor not in seen:
        seen.add(cursor)
        link = by_variant.get(cursor)
        if link is None:
            break  # audit cleared / pre-fix entry without lineage: stop honestly
        chain.append(link)
        cursor = link.get("parent_variant_id")
    chain.reverse()  # oldest iteration first (chronological "dlaczego tak")
    return chain


def _assemble_koncepcja(
    analysis_id: str | None, variant_id: str | None = None, audience: str = "architect"
) -> dict[str, Any]:
    """Assemble the koncepcja deliverable (Phase 11 §11.1.7) — no recomputation.

    Sources (all already computed and stored by earlier tool calls):

    * masterplan variant (:data:`plot_agent.drawing.DEFAULT_VARIANT_STORE`) —
      buildings, stage table, totals, WT/ppoż + staging checks, unknowns, critique;
    * design brief (:data:`plot_planning.DEFAULT_BRIEF_STORE` via the cached
      ``design_brief_for_analysis``);
    * design rationale — the reported variant's LINEAGE of masterplan audit
      entries (:data:`plot_agent.drawing.DEFAULT_MASTERPLAN_AUDIT` filtered by
      the variant-id parent chain, see :func:`_variant_lineage_entries` — plan
      §11.1.6 + review fix F1: never another session's rationales).

    The plan render (renderer v2 WITH the stage-table panel) uses the same parcel
    geometry the variant was scored against: the variant's OWN analysis context
    when it was analysis-bound (Phase 12), else the unbound drawing context (test
    seam / Phase 3 sample).

    Phase 14 (§31): returns the assembly — the unified ``ReportModel`` (built
    ONCE; md/html/pdf/json render from it) + the variant + the plan-PNG artifact
    — or the ``not_found`` result dict when no variant exists. The Markdown path
    (:func:`_report_koncepcja`) returns the text inline (small, like ``md``) and
    it is also served by the variant-scoped
    ``analysis://{analysis_id}/masterplan/{variant_id}/report.md`` resource —
    chosen over reusing ``analysis://{id}/report.md`` because a koncepcja is
    per-VARIANT (several iterations may be stored per analysis), mirroring the
    existing ``metrics.json`` resource pattern.
    """
    from plot_agent.drawing import DEFAULT_VARIANT_STORE
    from plot_planning import DEFAULT_BRIEF_STORE
    from plot_reports import build_koncepcja_model, get_artifact_store, render_masterplan

    aid = analysis_id or ADHOC_ANALYSIS_ID
    # Variant resolution (F1): an explicit variant_id is honoured as-is (it may be
    # an adhoc-bound variant reported under a real analysis id — legacy sessions);
    # WITHOUT one, "latest" may only pick a variant of THIS analysis — never
    # another session's most recent variant.
    variant = (
        DEFAULT_VARIANT_STORE.get(variant_id)
        if variant_id
        else DEFAULT_VARIANT_STORE.latest(analysis_id=aid)
    )
    if variant is None:
        return {
            "analysis_id": analysis_id,
            "format": "koncepcja",
            "variant_id": variant_id,
            "status": "not_found",
            "note": (
                "Brak zapisanego wariantu masterplanu — najpierw zaproponuj koncepcję "
                "przez propose_layout (DSL v2, klucz 'buildings')."
            ),
        }

    brief = DEFAULT_BRIEF_STORE.get(aid) if aid else None
    brief_dump = brief.model_dump(mode="json") if brief is not None else None
    metadata = variant.metadata if isinstance(variant.metadata, dict) else {}

    # Plan render with the per-stage table panel (renderer v2, Phase 9 §9.1.4).
    # Phase 12: a variant bound to a stored analysis renders on THAT analysis'
    # geometry; otherwise the unbound drawing context (test seam / sample).
    # Review m2: a BOUND variant whose analysis vanished / lost its parcel
    # geometry is a hard error (like ``_context_for``) — rendering the bound
    # koncepcja on the Phase 3 sample geometry would silently misrepresent the
    # deliverable (§21). Unbound (adhoc/legacy) variants keep the sample path.
    if bool(metadata.get("analysis_bound")) and variant.analysis_id is not None:
        context = _analysis_drawing_context(variant.analysis_id)
        if context is None:
            raise ValueError(
                f"Wariant '{variant.id}' jest związany z analizą "
                f"'{variant.analysis_id}', której nie ma w magazynie analiz albo "
                "nie ma geometrii działki — koncepcja nie może być wyrenderowana "
                "na geometrii przykładowej; ponownie uruchom parcel_analyze."
            )
    else:
        context = _drawing_context()
    render = render_masterplan(
        variant,
        context.parcel_geom(),
        metrics={"stage_table": variant.stage_table},
        envelope=context.envelope_geom(),
        title=f"Plan zagospodarowania terenu (koncepcja) — {aid}",
    )
    store = get_artifact_store()
    png_key = f"analysis/{aid}/koncepcja-{variant.id.replace(':', '-')}.png"
    png_uri = store.put_render(png_key, render)

    # Phase 14 (§31): the unified ReportModel is assembled ONCE here; the
    # md/html/pdf/json renderers all consume THIS model (one model, all formats).
    model = build_koncepcja_model(
        analysis_id=aid,
        variant=variant,
        generated_at=_now().isoformat(),
        brief=brief_dump,
        rationales=_variant_lineage_entries(variant),
        unknowns=list(metadata.get("unknowns") or []),
        capacity=(metadata.get("critique") or {}).get("capacity"),
        plan_png_resource=png_uri,
        audience=audience,  # type: ignore[arg-type]
        analysis_result=DEFAULT_STORE.get(variant.analysis_id) if variant.analysis_id else None,
    )
    return {
        "model": model,
        "variant": variant,
        "aid": aid,
        "plan_png_uri": png_uri,
        "plan_png_bytes": len(render.data),
    }


def _report_koncepcja(
    analysis_id: str | None, variant_id: str | None = None, audience: str = "architect"
) -> dict[str, Any]:
    """The ``report_generate(format="koncepcja")`` path — Markdown deliverable.

    Thin over :func:`_assemble_koncepcja` (the shared model assembly) +
    ``render_model_markdown`` — byte-compatible architect output (Phase 11).
    """
    from plot_reports import get_artifact_store, render_model_markdown

    out = _assemble_koncepcja(analysis_id, variant_id, audience)
    if "model" not in out:
        return out  # not_found (honest miss, never a fabricated deliverable)
    model, pii_block = _shared_export_redaction(out["model"], audience)
    variant = out["variant"]
    aid = out["aid"]
    content = render_model_markdown(model)
    md_key = f"analysis/{aid}/koncepcja-{variant.id.replace(':', '-')}.md"
    md_uri = get_artifact_store().put(md_key, content.encode("utf-8"), "text/markdown")
    return {
        "analysis_id": aid,
        "format": "koncepcja",
        "audience": audience,
        "variant_id": variant.id,
        "content": content,
        "artifact_uri": md_uri,
        "plan_png_uri": out["plan_png_uri"],
        "plan_png_bytes": out["plan_png_bytes"],
        "resource_link": f"analysis://{aid}/masterplan/{variant.id}/report.md",
        "report_version": model.report_version,
        "analysis_snapshot_hash": model.analysis_snapshot_hash,
        "pii_redaction": pii_block,
        "status": "rendered",
        "note": (
            "Koncepcja zestawiona z zapisanych danych (wariant + brief + audyt "
            "uzasadnień) — bez ponownych obliczeń; rysunek planu z panelem tabeli "
            "etapów zapisany w ArtifactStore."
        ),
    }


def _report_model_format(
    analysis_id: str | None,
    fmt: str,
    variant_id: str | None,
    audience: str,
) -> dict[str, Any]:
    """``report_generate(format="html"|"pdf")`` — rendered from the ONE model (§31).

    An explicit ``variant_id`` selects the koncepcja deliverable; otherwise the
    screening report of the stored analysis. The HTML/PDF artifact AND the model
    JSON (the numbers source of truth) land in the ArtifactStore; the result
    carries links only (NFR-PERF-009). PDF is honest about availability:
    ``pdf_unavailable`` + reason when the weasyprint system stack is missing.
    """
    import json as _json

    from plot_reports import (
        PdfUnavailableError,
        build_screening_model,
        get_artifact_store,
        render_model_html,
        render_model_json,
        render_model_pdf,
    )

    if variant_id is not None:
        out = _assemble_koncepcja(analysis_id, variant_id, audience)
        if "model" not in out:
            return {**out, "format": fmt}
        model = out["model"]
        aid = out["aid"]
        stem = f"report-koncepcja-{audience}-{out['variant'].id.replace(':', '-')}"
    else:
        result = DEFAULT_STORE.get(analysis_id) if analysis_id else None
        if result is None:
            return {"analysis_id": analysis_id, "format": fmt, "status": "not_found"}
        model = build_screening_model(
            result,
            audience=audience,  # type: ignore[arg-type]
            generated_at=_now().isoformat(),
        )
        aid = str(analysis_id)
        stem = f"report-screening-{audience}"

    # Shared-export PII redaction (F-0483): non-architect audiences are the
    # share channel — owner fields (if any ever appear) never leave the system.
    model, pii_block = _shared_export_redaction(model, audience)

    store = get_artifact_store()
    # The model JSON always accompanies html/pdf (§31: the same numbers, the
    # reproducibility snapshot hash inside).
    model_json = _json.dumps(
        render_model_json(model), ensure_ascii=False, indent=2
    ).encode("utf-8")
    model_uri = store.put(
        f"analysis/{aid}/export/{stem}.json", model_json, "application/json"
    )

    base = {
        "analysis_id": aid,
        "format": fmt,
        "audience": audience,
        "kind": model.kind,
        "variant_id": model.variant_id,
        "report_version": model.report_version,
        "analysis_snapshot_hash": model.analysis_snapshot_hash,
        "model_json_uri": model_uri,
        "model_json_resource": f"analysis://{aid}/export/{stem}.json",
        "headline_numbers": model.headline,
        "pii_redaction": pii_block,
    }
    if fmt == "html":
        html_text = render_model_html(model)
        uri = store.put(
            f"analysis/{aid}/export/{stem}.html",
            html_text.encode("utf-8"),
            "text/html",
        )
        return {
            **base,
            "artifact_uri": uri,
            "resource_link": f"analysis://{aid}/export/{stem}.html",
            "byte_size": len(html_text.encode("utf-8")),
            "status": "rendered",
            "note": "HTML deterministyczny, bez zasobów zewnętrznych — z tego samego modelu co md/json (§31).",
        }
    try:
        pdf_bytes = render_model_pdf(model)
    except PdfUnavailableError as exc:  # pragma: no cover - host-dependent
        return {
            **base,
            "artifact_uri": None,
            "status": "pdf_unavailable",
            "note": (
                f"PDF niedostępny na tym hoście: {exc} — weasyprint wymaga "
                "systemowych bibliotek pango/cairo. Użyj format='html' "
                "(ten sam model raportu, te same liczby — §31)."
            ),
        }
    uri = store.put(
        f"analysis/{aid}/export/{stem}.pdf", pdf_bytes, "application/pdf"
    )
    return {
        **base,
        "artifact_uri": uri,
        "resource_link": f"analysis://{aid}/export/{stem}.pdf",
        "byte_size": len(pdf_bytes),
        "status": "rendered",
        "note": "PDF wyrenderowany przez weasyprint z TEGO SAMEGO HTML/modelu (§31).",
    }


def _pzt_spot_elevations(site_context: Any, parcel: Any, variant: Any) -> list[dict[str, float]]:
    """Rzędne terenu samples for the PZT rysunkowa (Phase 15 §15.1.3).

    Samples the STORED Phase 12 terrain grids (``TerrainAnalysis.elevation_at``)
    at the parcel exterior corners + every building footprint corner. Returns an
    EMPTY list when no terrain context exists or no sample hits the raster — the
    drawing then carries the honest omission note and the checklist flips the
    rzędne item to ``missing`` (never silently dropped, plan §15.3).
    """
    terrain = getattr(site_context, "terrain", None) if site_context is not None else None
    if terrain is None or getattr(terrain, "status", "no_data") != "ok":
        return []
    from shapely.geometry import shape as _shape

    points: list[tuple[float, float]] = []
    if parcel is not None and parcel.geom_type == "Polygon":
        points.extend((float(x), float(y)) for x, y in list(parcel.exterior.coords)[:-1])
    for record in getattr(variant, "buildings", []) or []:
        geometry = getattr(record, "geometry", None)
        if geometry is None:
            continue
        geom = _shape(geometry)
        if geom.geom_type != "Polygon":
            continue
        points.extend((float(x), float(y)) for x, y in list(geom.exterior.coords)[:-1])
    spots: list[dict[str, float]] = []
    for x, y in points:
        z = terrain.elevation_at(x, y)
        if z is not None:
            spots.append({"x": x, "y": y, "z": round(float(z), 2)})
    return spots


def _report_pzt_draft(analysis_id: str | None, variant_id: str | None) -> dict[str, Any]:
    """``report_generate(format="pzt-draft")`` — the Phase 15 PZT draft package.

    Assembles, from STORED data only (no recomputation):

    * **część opisowa** (§14) — MD + structured JSON from ONE ``PztOpisowa``
      model: variant + the capacity-engine zestawienie powierzchni stored in the
      variant metadata (single source of truth) + the Phase 12 site context
      (BDOT10k/GESUT/constraints; honest ``data_unavailable`` when absent) + the
      Phase 10 fire RuleChecks as evidence-backed statements;
    * **część rysunkowa** (§15) — scaled (1:500) VECTOR SVG + PDF (matplotlib
      PDF backend — weasyprint is not involved) with wymiary zewnętrzne,
      kondygnacje, sieci (where known), układ komunikacyjny (function-tagged,
      incl. drogi pożarowe), zieleń and rzędne terenu from the Phase 12 NMT
      (honest omission note when terrain is absent); file named per the e-form
      załącznik convention ``PZT_{rrrr.mm.dd}_{analysis_id}.pdf``;
    * **§13–18 checklist** — done / missing / requires_projektant /
      requires_uprawnienia with reasons.

    The result and every artifact carry the MANDATORY draft-not-PB disclaimer
    (anti-pattern §15.4, test-enforced). Artifacts land in the ArtifactStore
    under ``analysis/{aid}/export/`` and travel as resource links (NFR-PERF-009).
    """
    import json as _json
    from datetime import date as _date

    from plot_agent.analysis import DEFAULT_SITE_CONTEXT_STORE
    from plot_agent.drawing import DEFAULT_VARIANT_STORE
    from plot_reports import (
        PZT_DISCLAIMER,
        build_pzt_checklist,
        build_pzt_opisowa,
        get_artifact_store,
        render_pzt_opisowa_json,
        render_pzt_opisowa_markdown,
        render_pzt_rysunkowa,
    )
    from plot_reports.pzt import pzt_filename

    aid = analysis_id or ADHOC_ANALYSIS_ID
    # Variant resolution — same semantics as the koncepcja path (F1): explicit
    # variant_id honoured as-is; otherwise only THIS analysis' latest variant.
    variant = (
        DEFAULT_VARIANT_STORE.get(variant_id)
        if variant_id
        else DEFAULT_VARIANT_STORE.latest(analysis_id=aid)
    )
    if variant is None:
        return {
            "analysis_id": analysis_id,
            "format": "pzt-draft",
            "variant_id": variant_id,
            "status": "not_found",
            "note": (
                "Brak zapisanego wariantu masterplanu — pakiet PZT powstaje z "
                "ZAAKCEPTOWANEGO wariantu; najpierw propose_layout (DSL v2)."
            ),
        }
    metadata = variant.metadata if isinstance(variant.metadata, dict) else {}

    # Parcel geometry: the bound analysis' geometry, else the drawing context —
    # a BOUND variant whose analysis vanished is a hard error (same rule as the
    # koncepcja path; rendering PZT on sample geometry would misrepresent, §21).
    if bool(metadata.get("analysis_bound")) and variant.analysis_id is not None:
        context = _analysis_drawing_context(variant.analysis_id)
        if context is None:
            raise ValueError(
                f"Wariant '{variant.id}' jest związany z analizą "
                f"'{variant.analysis_id}', której nie ma w magazynie analiz albo "
                "nie ma geometrii działki — pakiet PZT nie może użyć geometrii "
                "przykładowej; ponownie uruchom parcel_analyze."
            )
    else:
        context = _drawing_context()
    parcel_geom = context.parcel_geom()

    # Site context: the stored analysis' serialized block, else the typed store's
    # serialization (test seam) — None stays None (honest data_unavailable).
    owner_aid = variant.analysis_id or aid
    stored = DEFAULT_STORE.get(owner_aid)
    site_dict: dict[str, Any] | None = None
    if stored is not None and isinstance(stored.planning, dict):
        raw = stored.planning.get("_site_context")
        if isinstance(raw, dict):
            site_dict = raw
    typed_site = DEFAULT_SITE_CONTEXT_STORE.get(owner_aid)
    if site_dict is None and typed_site is not None:
        site_dict = typed_site.to_dict()

    generated_at = _now()
    opisowa = build_pzt_opisowa(
        analysis_id=aid,
        variant=variant,
        generated_at=generated_at.isoformat(),
        zestawienie=metadata.get("zestawienie_powierzchni"),
        site_context=site_dict,
        inter_building_checks=list(metadata.get("inter_building_checks") or []),
    )
    opisowa_md = render_pzt_opisowa_markdown(opisowa)
    opisowa_json = render_pzt_opisowa_json(opisowa)

    # Rysunkowa inputs from STORED data: GESUT networks (serialized utilities)
    # + NMT spot elevations from the typed terrain grids (honest [] when absent).
    networks = [
        {"network_type": u.get("network_type"), "geometry": u.get("geometry")}
        for u in ((site_dict or {}).get("access") or {}).get("utilities") or []
        if u.get("geometry")
    ]
    spots = _pzt_spot_elevations(typed_site, parcel_geom, variant)
    generated_on = _date(generated_at.year, generated_at.month, generated_at.day)
    drawing = render_pzt_rysunkowa(
        variant,
        parcel_geom,
        analysis_id=aid,
        generated_on=generated_on,
        investor=None,  # never invented (§21) — title block prints the placeholder
        networks=networks,
        spot_elevations=spots,
        building_lines=None,  # no geometric linia-zabudowy source wired (honest)
    )
    checklist = build_pzt_checklist(opisowa, drawing.metadata)

    store = get_artifact_store()
    # Review F1: the e-form stem (PZT_{rrrr.mm.dd}_{analysis_id}) is shared by
    # ALL variants of an analysis on a given day, and LocalArtifactStore.put
    # silently overwrites — storage keys therefore carry the slugged variant id
    # (the koncepcja convention: ``koncepcja-{variant.id}``) so two variants'
    # drafts never clobber each other. The response's ``pdf_filename`` keeps
    # the pure e-form convention name (the file name the projektant submits).
    stem = drawing.filename_stem  # PZT_{rrrr.mm.dd}_{analysis_id}
    key_stem = f"{stem}-{variant.id.replace(':', '-')}"
    base_key = f"analysis/{aid}/export"
    md_uri = store.put(
        f"{base_key}/{key_stem}-opisowa.md", opisowa_md.encode("utf-8"), "text/markdown"
    )
    opisowa_json_uri = store.put(
        f"{base_key}/{key_stem}-opisowa.json",
        _json.dumps(opisowa_json, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json",
    )
    svg_uri = store.put(f"{base_key}/{key_stem}.svg", drawing.svg, "image/svg+xml")
    pdf_uri = store.put(f"{base_key}/{key_stem}.pdf", drawing.pdf, "application/pdf")
    checklist_uri = store.put(
        f"{base_key}/{key_stem}-checklist.json",
        _json.dumps(checklist, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json",
    )

    return {
        "analysis_id": aid,
        "format": "pzt-draft",
        "variant_id": variant.id,
        "status": "rendered",
        # MANDATORY + prominent (anti-pattern §15.4, test-enforced).
        "disclaimer": PZT_DISCLAIMER,
        "is_projekt_budowlany": False,
        "checklist": checklist,
        "opisowa_sections": [
            {"id": s.id, "title": s.title, "status": s.status} for s in opisowa.sections
        ],
        "zestawienie_powierzchni": opisowa.zestawienie,
        "rysunkowa_metadata": drawing.metadata,
        "pdf_filename": pzt_filename(generated_on, aid, "pdf"),
        "artifacts": {
            "opisowa_md": md_uri,
            "opisowa_json": opisowa_json_uri,
            "rysunkowa_svg": svg_uri,
            "rysunkowa_pdf": pdf_uri,
            "checklist_json": checklist_uri,
        },
        "resource_links": {
            "opisowa_md": f"analysis://{aid}/export/{key_stem}-opisowa.md",
            "opisowa_json": f"analysis://{aid}/export/{key_stem}-opisowa.json",
            "rysunkowa_svg": f"analysis://{aid}/export/{key_stem}.svg",
            "rysunkowa_pdf": f"analysis://{aid}/export/{key_stem}.pdf",
            "checklist_json": f"analysis://{aid}/export/{key_stem}-checklist.json",
        },
        "note": (
            "Pakiet PZT (szkic) zestawiony z zapisanych danych: opisowa §14 "
            "(MD+JSON), rysunkowa §15 (SVG + PDF wektorowy, skala 1:500, "
            "nazewnictwo e-formy), checklista §13–18. TO NIE JEST projekt "
            "budowlany — patrz disclaimer."
        ),
    }


#: Formats the export tool produces (Phase 14; F-0393/F-0394 + GeoJSON + IFC).
_EXPORT_FORMATS = ("geojson", "gpkg", "dxf", "ifc")

_EXPORT_MIME = {
    "geojson": "application/geo+json",
    "gpkg": "application/geopackage+sqlite3",
    "dxf": "image/vnd.dxf",
    "ifc": "application/x-step",
}


def _variant_violation_geoms(variant: Any) -> list[Any]:
    """Failing inter-building checks' evidence geometries (PA-NARUSZENIA layer)."""
    from shapely.geometry import shape

    metadata = variant.metadata if isinstance(variant.metadata, dict) else {}
    geoms = []
    for check in metadata.get("inter_building_checks") or []:
        evidence = check.get("geometry_evidence") or {}
        if str(check.get("status")) == "fail" and evidence.get("geometry"):
            geoms.append(shape(evidence["geometry"]))
    return geoms


def _export_context_for_variant(variant: Any) -> Any:
    """Parcel/envelope context for a variant export — same rules as koncepcja.

    A BOUND variant whose analysis vanished is a hard error (never the sample
    geometry, §21); unbound (adhoc/test) variants use the drawing context.
    """
    metadata = variant.metadata if isinstance(variant.metadata, dict) else {}
    if bool(metadata.get("analysis_bound")) and variant.analysis_id is not None:
        context = _analysis_drawing_context(variant.analysis_id)
        if context is None:
            raise ValueError(
                f"Wariant '{variant.id}' jest związany z analizą "
                f"'{variant.analysis_id}', której nie ma w magazynie analiz albo "
                "nie ma geometrii działki — eksport nie może użyć geometrii "
                "przykładowej; ponownie uruchom parcel_analyze."
            )
        return context
    return _drawing_context()


def export_layers(
    analysis_id: str | None, fmt: str, variant_id: str | None = None
) -> dict[str, Any]:
    """Real GIS/CAD/BIM export (Phase 14; F-0393/F-0394, §17 formats, §30).

    * ``geojson`` / ``gpkg`` — the masterplan variant layers (renderer-v2 layer
      assembly) when a variant exists for the analysis (explicit ``variant_id``
      wins, else the analysis' latest); otherwise the screening layer set
      (parcel + buildable envelope + constraints) from the stored analysis.
    * ``dxf`` — the masterplan variant on the documented ``PA-*`` CAD layer
      convention (``plot_reports.export.dxf``), metres, $INSUNITS=6.
    * ``ifc`` — the IFC4 massing model (IfcProject/Site/Building/Storey +
      extruded footprints, EPSG:2180 IfcMapConversion; MASSING ONLY).

    The artifact lands in the ArtifactStore and the result carries
    ``artifact_uri`` + a ``resource_link`` (``analysis://{id}/export/{file}``) —
    bytes are NEVER inlined (NFR-PERF-009).
    """
    from plot_agent.drawing import DEFAULT_VARIANT_STORE
    from plot_reports import (
        collect_analysis_layers,
        collect_variant_layers,
        export_geojson,
        export_gpkg,
        export_masterplan_dxf,
        export_masterplan_ifc,
        get_artifact_store,
    )

    fmt = fmt.lower()
    if fmt not in _EXPORT_FORMATS:
        return {
            "analysis_id": analysis_id,
            "format": fmt,
            "artifact_uri": None,
            "status": "unsupported_format",
            "note": f"Obsługiwane formaty eksportu: {', '.join(_EXPORT_FORMATS)}.",
        }

    aid = analysis_id or ADHOC_ANALYSIS_ID
    # Variant resolution mirrors the koncepcja report (F1): explicit variant_id
    # is honoured as-is; otherwise only THIS analysis' latest variant.
    variant = (
        DEFAULT_VARIANT_STORE.get(variant_id)
        if variant_id
        else DEFAULT_VARIANT_STORE.latest(analysis_id=aid)
    )
    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None

    if variant is not None:
        context = _export_context_for_variant(variant)
        parcel_geom = context.parcel_geom()
        envelope_geom = context.envelope_geom()
        if fmt == "dxf":
            data = export_masterplan_dxf(
                variant,
                parcel_geom,
                envelope=envelope_geom,
                violations=_variant_violation_geoms(variant) or None,
            )
        elif fmt == "ifc":
            data = export_masterplan_ifc(variant, parcel_geom)
        else:
            layers = collect_variant_layers(variant, parcel_geom, envelope=envelope_geom)
            data = export_geojson(layers) if fmt == "geojson" else export_gpkg(layers)
        source: dict[str, Any] = {
            "variant_id": variant.id,
            "layers_from": "masterplan_variant",
        }
    else:
        if fmt in ("dxf", "ifc"):
            return {
                "analysis_id": analysis_id,
                "format": fmt,
                "variant_id": variant_id,
                "artifact_uri": None,
                "status": "not_found",
                "note": (
                    "Eksport DXF/IFC wymaga zapisanego wariantu masterplanu — "
                    "najpierw zaproponuj koncepcję przez propose_layout (DSL v2)."
                ),
            }
        if result is None:
            return {
                "analysis_id": analysis_id,
                "format": fmt,
                "artifact_uri": None,
                "status": "not_found",
                "note": "Brak zapisanej analizy o tym id — uruchom parcel_analyze.",
            }
        env = result.buildable_envelope
        layers = collect_analysis_layers(
            result.parcel.geometry if result.parcel else None,
            env.geometry if env is not None else None,
            [
                {
                    "constraint_type": c.constraint_type,
                    "geometry": c.geometry,
                    "hard": bool(c.machine_summary.get("hard")),
                }
                for c in result.constraints
            ],
        )
        data = export_geojson(layers) if fmt == "geojson" else export_gpkg(layers)
        source = {"variant_id": None, "layers_from": "analysis_screening"}

    suffix = f"-{variant.id.replace(':', '-')}" if variant is not None else ""
    filename = f"layers{suffix}.{fmt}"
    uri = get_artifact_store().put(
        f"analysis/{aid}/export/{filename}", data, _EXPORT_MIME[fmt]
    )
    return {
        "analysis_id": aid,
        "format": fmt,
        **source,
        "artifact_uri": uri,
        "resource_link": f"analysis://{aid}/export/{filename}",
        "mime_type": _EXPORT_MIME[fmt],
        "byte_size": len(data),
        "status": "exported",
        "note": (
            "Plik zapisany w ArtifactStore; bajty przez zasób resource_link, "
            "nigdy inline (NFR-PERF-009). DXF: warstwy PA-* (konwencja w "
            "plot_reports.export.dxf), jednostki metry ($INSUNITS=6). IFC: "
            "model masowy IFC4 (bez ścian/stropów/okien), georeferencja "
            "EPSG:2180 (IfcMapConversion)."
        ),
    }


def portfolio_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Real portfolio/batch analysis (Phase 13; §4.4, F-0419, NFR-REL-008).

    * **Queued path** — when the env configures a broker (``PLOT_QUEUE_ENABLED``)
      the batch is enqueued on the Dramatiq worker
      (:func:`plot_worker.actors.portfolio_batch_task`) and the tool returns the
      batch id immediately; progress streams via ``analysis_get_status(batch_id)``.
    * **In-process fallback** (graceful degradation, no broker configured) —
      :func:`plot_agent.portfolio.run_portfolio_analysis` runs synchronously
      over the injected connectors and the FULL result (dedupe, ranking,
      red-flag table, CSV/JSON/GeoJSON artifact uris) is returned inline.

    One bad parcel never fails the batch (NFR-REL-008); duplicates are analyzed
    once; sequential fan-out honours the backpressure delay (NFR-PERF-014).
    """
    from plot_shared import get_settings

    parcels = list(payload.get("parcels") or [])
    batch_id = f"batch:{uuid.uuid4().hex[:12]}"
    if not parcels:
        return {"batch_id": batch_id, "submitted": 0, "status": "empty", "items": []}

    settings = get_settings()
    if settings.queue_enabled:
        from plot_worker import actors as worker_actors

        worker_actors.portfolio_batch_task.send(batch_id, parcels)
        return {
            "batch_id": batch_id,
            "submitted": len(parcels),
            "status": "queued",
            "note": (
                "Batch w kolejce Dramatiq (PLOT_QUEUE_ENABLED) — postęp przez "
                "analysis_get_status(batch_id); artefakty CSV/JSON/GeoJSON po "
                "zakończeniu (F-0419)."
            ),
        }

    from plot_agent.portfolio import run_portfolio_analysis

    return _run_async(
        run_portfolio_analysis(
            parcels,
            connectors=_get_connectors(),
            batch_id=batch_id,
            delay_s=settings.backpressure_delay_s,
        )
    )


def ruleset_explain(registry: Any) -> dict[str, Any]:
    """Return the currently loaded ruleset versions + applied rules (live, §10.3).

    ``registry`` is a :class:`plot_rules.RulesetRegistry`. Because rulesets are loaded
    fresh, this reflects the on-disk YAML as of the most recent reload (hot-reload proof).
    """
    return {
        "ruleset_version": registry.ruleset_version,
        "categories": list(registry.categories),
        "rule_count": len(registry.rules),
        # Schema-validation failures: "<path>: <error>" per skipped rule file —
        # surfaced so a malformed YAML is visible, never silently absent.
        "ruleset_errors": list(getattr(registry, "errors", ()) or ()),
        "rules": [
            {
                "id": r.id,
                "title": r.title,
                "category": r.category,
                "jurisdiction": r.jurisdiction,
                "valid_from": r.valid_from,
                "valid_to": r.valid_to,
                "source_reference": r.source_reference,
                "logic": r.logic,
                # Surface every extra scalar so the hot-reload test can read edited values.
                "raw": _rule_raw(r),
            }
            for r in registry.rules
        ],
    }


def _rule_raw(rule: Any) -> dict[str, Any]:
    """Re-read the rule's YAML file fresh so edited scalar values are visible (hot-reload)."""
    import yaml  # local import keeps reload cheap

    try:
        with open(rule.path, "rb") as fh:
            doc = yaml.safe_load(fh) or {}
        return doc if isinstance(doc, dict) else {}
    except OSError:
        return {}


def source_healthcheck(source_id: str | None) -> dict[str, Any]:
    """Real external-source availability via the connector autotest (F-0441).

    Runs the EXISTING Phase 6 ``healthcheck()`` contract (NFR-REL-005) over the
    active connector bundle — the injected mock bundle in tests (zero network),
    the production bundle in a live session. ``source_id`` filters by name /
    connector source id; connectors without a healthcheck are reported as such,
    never as healthy (§21).
    """
    from plot_agent.monitoring import connector_autotest

    named = _named_connectors()
    if source_id:
        named = {
            name: conn
            for name, conn in named.items()
            if name == source_id or getattr(conn, "source_id", None) == source_id
        }
    return {
        "source_id": source_id,
        "connectors": _run_async(connector_autotest(named)),
        "note": "Autotest connectorów przez kontrakt healthcheck() (F-0441, NFR-REL-005).",
    }


# --------------------------------------------------------------------------- #
# Write / side-effecting surface (§16: explicit purpose + audit; annotated in server.py)
# --------------------------------------------------------------------------- #
def cache_warm(scope: str, target_id: str | None) -> dict[str, Any]:
    """Real cache warming for a parcel/municipality (Phase 13; NFR-PERF-011/014).

    Queued via the Dramatiq worker when the env configures a broker; otherwise
    runs in-process over the injected connectors. Layers are fetched
    SEQUENTIALLY with the configured backpressure delay — never a parallel
    hammer on public services (v1 §11.4 anti-pattern guard).
    """
    from plot_shared import get_settings

    settings = get_settings()
    if settings.queue_enabled:
        from plot_worker import actors as worker_actors

        worker_actors.cache_warm_task.send(scope, target_id)
        return {
            "scope": scope,
            "target_id": target_id,
            "status": "queued",
            "note": "Nagrzewanie cache w kolejce Dramatiq (sekwencyjnie, z opóźnieniem).",
        }

    from plot_agent.warming import warm_cache as _warm

    return _run_async(
        _warm(
            scope,
            target_id,
            connectors=_get_connectors(),
            delay_s=settings.backpressure_delay_s,
        )
    )


def document_upload(
    filename: str,
    content: bytes,
    *,
    declared_type: str | None = None,
    purpose: str,
    analysis_id: str | None = None,
) -> dict[str, Any]:
    """Sandboxed user-document upload (Phase 14B; F-0491–0493, §16).

    The §27 shared use-case behind the HTTP ``POST /v1/documents/ingest`` —
    MCP stdio has no byte-upload channel, so this is NOT a new MCP tool (the
    tool surface stays frozen); ``document_ingest`` attaches an already-uploaded
    ``file_id`` to an analysis. Pipeline (``plot_security.ingest_upload``):
    filename traversal guard → size cap → type allowlist (pdf/html/txt) → PDF
    page-cap heuristic → AV hook (HONEST ``not_scanned`` — no AV engine here) →
    quarantine storage in the ArtifactStore → capped text extraction for the
    untrusted-content parser channel (text is DATA, never instructions). Typed
    ``UploadRejected`` errors propagate to the API layer (413/415/400). Every
    successful upload writes an audit entry (NFR-SEC-010).
    """
    from plot_agent.orchestrator import DEFAULT_ORCHESTRATOR_AUDIT
    from plot_reports import get_artifact_store
    from plot_security import DEFAULT_UPLOAD_STORE, ingest_upload

    record = ingest_upload(
        filename,
        content,
        declared_type=declared_type,
        purpose=purpose,
        analysis_id=analysis_id,
        store_bytes=get_artifact_store().put,
    )
    DEFAULT_UPLOAD_STORE.put(record)
    DEFAULT_ORCHESTRATOR_AUDIT.append(
        {
            "graph_id": None,
            "analysis_id": analysis_id,
            "event": "document_uploaded",
            "node_id": None,
            # Metadata only — never the content (it lives in quarantine storage).
            "detail": record.summary(),
            "at": _now().isoformat(),
        }
    )
    return {
        **record.summary(),
        "ingested": True,
        "status": "quarantined",
        "note": (
            "Plik w kwarantannie ArtifactStore (prefiks quarantine/); treść jest "
            "DANYMI, nigdy instrukcjami (NFR-SEC-002/003) — parsowanie wyłącznie "
            "przez planning_parse_document w trybie untrusted-content. "
            "AV: status uczciwie 'not_scanned' bez silnika AV (F-0492)."
        ),
    }


def document_ingest(analysis_id: str | None, file_id: str, purpose: str) -> dict[str, Any]:
    """Attach a sandboxed upload to an analysis (Phase 14B real implementation; §16).

    ``file_id`` must reference an upload that already passed the sandbox
    (``document_upload`` via the HTTP API). The attach is audited (``purpose``
    is the §16/NFR-SEC-010 explicit intent). An unknown ``file_id`` is an honest
    miss explaining the upload channel — MCP stdio carries no file bytes.
    """
    from plot_agent.orchestrator import DEFAULT_ORCHESTRATOR_AUDIT
    from plot_security import DEFAULT_UPLOAD_STORE

    record = DEFAULT_UPLOAD_STORE.attach(file_id, analysis_id, purpose)
    if record is None:
        return {
            "analysis_id": analysis_id,
            "file_id": file_id,
            "purpose": purpose,
            "ingested": False,
            "status": "file_not_found",
            "note": (
                "Brak uploadu o tym file_id w sandboxie — pliki wgrywa się przez "
                "HTTP API (POST /v1/documents/ingest, sandbox F-0491–0493); "
                "kanał MCP stdio nie przenosi bajtów plików."
            ),
        }
    DEFAULT_ORCHESTRATOR_AUDIT.append(
        {
            "graph_id": None,
            "analysis_id": analysis_id,
            "event": "document_attached",
            "node_id": None,
            "detail": {"file_id": file_id, "purpose": purpose, "filename": record.filename},
            "at": _now().isoformat(),
        }
    )
    return {
        **record.summary(),
        "purpose": purpose,
        "ingested": True,
        "status": "attached",
        "audit_logged": True,
        "note": (
            "Upload powiązany z analizą; treść w kwarantannie, parsowanie przez "
            "planning_parse_document(file_id=...) w trybie untrusted-content (§16)."
        ),
    }


def monitoring_create(
    scope: str,
    target_id: str,
    purpose: str,
    interval_hours: float | None = None,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """Real monitoring-profile creation (Phase 13; §4.5, F-0418).

    Registers a :class:`plot_agent.monitoring.Monitor` (scope/target/purpose/
    interval/optional webhook) in the monitor store and writes an orchestrator
    audit entry (``purpose`` is the §16/NFR-SEC-010 explicit intent). Checks
    are executed by ``monitoring_check_task`` (worker) — the store exposes the
    in-memory ``due()`` scheduler INTERFACE; real periodic triggering (cron)
    is a deployment concern (documented, not faked). Webhook alerts POST
    through the EXISTING egress allowlist — a non-allowlisted host is blocked
    at check time (F-0418).
    """
    from plot_agent.monitoring import create_monitor
    from plot_agent.orchestrator import DEFAULT_ORCHESTRATOR_AUDIT
    from plot_shared import get_settings

    # Review m5: a non-positive interval would make the monitor ALWAYS due
    # (every scheduler pass re-enqueues it) — rejected cleanly at the boundary.
    if interval_hours is not None and not interval_hours > 0:
        return {
            "monitoring_id": None,
            "scope": scope,
            "target_id": target_id,
            "purpose": purpose,
            "active": False,
            "audit_logged": False,
            "status": "rejected_input",
            "note": (
                f"interval_hours={interval_hours} jest nieprawidłowe — interwał "
                "kontroli musi być > 0 godzin (monitor z interwałem <= 0 byłby "
                "zawsze 'due' i młóciłby źródła publiczne, NFR-PERF-014)."
            ),
        }
    settings = get_settings()
    hours = interval_hours if interval_hours else settings.monitoring_default_interval_hours
    monitor = create_monitor(
        scope,
        target_id,
        purpose,
        interval_s=float(hours) * 3600.0,
        webhook_url=webhook_url,
    )
    DEFAULT_ORCHESTRATOR_AUDIT.append(
        {
            "graph_id": None,
            "analysis_id": None,
            "event": "monitor_created",
            "node_id": None,
            "detail": monitor.config_echo(),
            "at": _now().isoformat(),
        }
    )
    return {
        **monitor.config_echo(),
        "audit_logged": True,
        "note": (
            "Monitor zarejestrowany (§4.5): worker monitoring_check_task wykonuje "
            "kontrole; harmonogram okresowy (cron) to kwestia wdrożenia — magazyn "
            "udostępnia interfejs due(). Webhook przechodzi przez allowlistę "
            "egress (F-0418)."
        ),
    }


#: The analysis id the masterplan path of ``propose_layout`` evaluates under when
#: NOT analysis-bound. Since Phase 12 ``propose_layout(analysis_id=...)`` also
#: evaluates under STORED analysis ids, so ``manual_override`` may honestly report
#: ``applied``/``active`` for this id OR any analysis present in the store (§21).
ADHOC_ANALYSIS_ID = "adhoc"


def _gate_decision(
    analysis_id: str,
    graph_id: str,
    reason: str,
    user_id: str,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve a task-graph manual-review gate (Phase 13, F-0430/0437).

    ``after.status`` must be an explicit ``approved`` / ``rejected`` AND
    ``after.node_id`` must name the SPECIFIC gate being decided (review B1) —
    nothing is defaulted (§21). A decision whose ``node_id`` is not the
    currently pending gate is rejected as ``gate_mismatch`` (a duplicate MCP
    approval retry must never silently approve the NEXT gate), and a decision
    whose ``analysis_id`` does not own the graph is rejected as
    ``analysis_mismatch`` (review M1). Approval resumes the graph from the
    paused gate; rejection aborts it. Every path is audited (F-0446).
    """
    from plot_agent.orchestrator import (
        DEFAULT_GRAPH_REGISTRY,
        DEFAULT_ORCHESTRATOR_AUDIT,
        GateMismatchError,
    )

    def _refused(status: str, note: str, **extra: Any) -> dict[str, Any]:
        return {
            "override_id": None,
            "analysis_id": analysis_id,
            "target_type": "task_graph_gate",
            "target_id": graph_id,
            "applied": False,
            "audit_logged": bool(extra.pop("audit_logged", False)),
            "status": status,
            "note": note,
            **extra,
        }

    status = (after or {}).get("status")
    if status not in ("approved", "rejected"):
        return _refused(
            "rejected_input",
            "Decyzja bramki wymaga jawnego after={'status': 'approved'|"
            "'rejected', 'node_id': '<bramka>'} — nic nie jest domyślne "
            "(§21, F-0437).",
        )
    gate_node = (after or {}).get("node_id")
    if not gate_node or not isinstance(gate_node, str):
        return _refused(
            "rejected_input",
            "Decyzja bramki wymaga jawnego after.node_id wskazującego "
            "KONKRETNĄ bramkę (np. 'design_brief') — duplikat wywołania nie "
            "może po cichu zatwierdzić następnej bramki (B1, §21).",
        )
    graph = DEFAULT_GRAPH_REGISTRY.get(graph_id)
    if graph is None:
        return _refused(
            "graph_not_found",
            f"Brak żywego grafu '{graph_id}' w rejestrze procesu — bramkę "
            "można rozstrzygnąć tylko w procesie, który graf zbudował "
            "(stan in-memory; trwała wznowa międzyprocesowa to Phase 14+).",
        )
    owner = graph.state.analysis_id
    if analysis_id not in (graph_id, owner):
        # Review M1: a gate decision must be bound to the graph it claims to
        # decide — approving graph B while citing analysis A is rejected.
        DEFAULT_ORCHESTRATOR_AUDIT.append(
            {
                "graph_id": graph_id,
                "analysis_id": owner,
                "event": "gate_analysis_mismatch",
                "node_id": gate_node,
                "detail": {"claimed_analysis_id": analysis_id, "reviewer": user_id},
                "at": _now().isoformat(),
            }
        )
        return _refused(
            "analysis_mismatch",
            f"analysis_id '{analysis_id}' nie jest właścicielem grafu "
            f"'{graph_id}' (analiza grafu: {owner!r}) — decyzja bramki "
            "odrzucona i zaudytowana (M1).",
            audit_logged=True,
        )
    try:
        state = graph.resume(
            approved=status == "approved",
            reason=reason,
            reviewer=user_id,
            expected_gate=gate_node,
        )
    except GateMismatchError as exc:
        return _refused(
            "gate_mismatch",
            f"Bramka '{gate_node}' nie jest bramką oczekującą grafu "
            f"'{graph_id}' (oczekuje: {exc.pending_gate!r}) — decyzja "
            "odrzucona i zaudytowana; duplikat zatwierdzenia nigdy nie "
            "przechodzi na kolejną bramkę (B1).",
            pending_gate=exc.pending_gate,
            audit_logged=True,
        )
    return {
        "override_id": None,
        "analysis_id": state.analysis_id or analysis_id,
        "target_type": "task_graph_gate",
        "target_id": graph_id,
        "decision": status,
        "applied": True,
        "audit_logged": True,  # OrchestratorAudit gate_approved/gate_rejected (F-0446)
        "status": state.status,
        "pending_gate": state.pending_gate,
        "note": (
            "Bramka zatwierdzona — graf kontynuuje (F-0430)."
            if status == "approved"
            else "Bramka odrzucona — graf przerwany (aborted) z audytem (F-0430)."
        ),
    }


def manual_override(
    analysis_id: str,
    target_type: str,
    target_id: str,
    reason: str,
    user_id: str,
    after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expert override with audit trail (Phase 8, F-0137/0138; NFR-AUD-003).

    Creates an audited :class:`~plot_domain.Override` record in the
    :data:`plot_rules.DEFAULT_OVERRIDE_STORE`. Since Phase 10 the inter-building
    validators (:func:`plot_planning.wt_validators.run_inter_building_checks`)
    CONSUME the store. ``target_id`` is either a bare consumed WT/ppoż rule id
    (the override applies RULE-WIDE — every building/pair of the rule; recorded
    as ``scope: rule-wide`` in the audit) or ``"<rule_id>#<subject>"`` scoping
    it to ONE evaluation subject (a building name; ``"pair:A|B"`` with names
    sorted for the pairwise §271 evaluation; ``"parking:N"`` for §19).

    §21 honesty (two conditions for ``applied=True`` / ``status="active"``):
    the rule must be consumed by the Phase 10 validators AND ``analysis_id``
    must actually be queryable by a production path — that is
    :data:`ADHOC_ANALYSIS_ID` (``"adhoc"``, the unbound ``propose_layout``
    path) or, since Phase 12, ANY analysis id present in the analysis store
    (``propose_layout(analysis_id=...)`` evaluates under it). An id of an
    analysis that does not exist is ``recorded`` with a note explaining when
    it will activate. ``after`` must explicitly carry a valid new ``status``
    — NOTHING is defaulted (§21) and an invalid status is rejected at the
    tool boundary.
    """
    # Phase 13 (F-0430/0437): task-graph gate decisions route to the orchestrator.
    # target_type="task_graph_gate" + target_id=<graph_id> + after={"status":
    # "approved"|"rejected", "node_id": "<gate>"} resumes/aborts the PAUSED graph
    # — the decision binds to ONE named gate and to the owning analysis (B1/M1);
    # fully audited (OrchestratorAudit), reusing this write tool instead of a new one
    # (tool surface frozen at 22).
    if target_type == "task_graph_gate":
        return _gate_decision(analysis_id, target_id, reason, user_id, after)

    from plot_planning.wt_validators import CONSUMED_RULE_IDS
    from plot_rules import DEFAULT_OVERRIDE_STORE, RuleStatus, override_subject

    def _rejected(note: str) -> dict[str, Any]:
        return {
            "override_id": None,
            "analysis_id": analysis_id,
            "target_type": target_type,
            "target_id": target_id,
            "applied": False,
            "audit_logged": False,
            "status": "rejected",
            "note": note,
        }

    if not after or "status" not in after:
        return _rejected(
            "Override wymaga jawnej wartości 'after' z polem 'status' "
            "(np. {'status': 'pass', 'confidence': 0.95}) — nic nie jest "
            "domyślne (§21, F-0137)."
        )

    # Validate the requested status at the tool boundary so an invalid value is
    # a clean rejection here, not a deferred ValueError inside evaluate().
    try:
        RuleStatus(after["status"])
    except ValueError:
        allowed = ", ".join(s.value for s in RuleStatus)
        return _rejected(
            f"Nieprawidłowy status '{after['status']}' w 'after' — dozwolone "
            f"wartości RuleStatus: {allowed} (F-0137)."
        )

    from plot_domain import Override

    override = Override(
        id=_new_id(),
        analysis_id=analysis_id,
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        before_json={},
        after_json=dict(after),
        reason=reason,
        created_at=_now(),
    )
    DEFAULT_OVERRIDE_STORE.put(override)
    # Phase 10/12: the wt_validators evaluation path consumes DEFAULT_OVERRIDE_STORE
    # (matched by analysis_id + rule_id [+ subject] → plot_rules.evaluate
    # override hook, F-0137) for the inter-building WT/ppoż rules. applied/active
    # may ONLY be claimed when BOTH the rule is consumed AND the analysis_id is
    # reachable by a production path — the unbound propose_layout evaluates under
    # ADHOC_ANALYSIS_ID, and since Phase 12 propose_layout(analysis_id=...)
    # evaluates under any STORED analysis id. Anything else is
    # recorded-but-not-yet-consumable (§21 honesty).
    rule_part, _, _ = target_id.partition("#")
    subject = override_subject(target_id)
    consumed_rule = rule_part in CONSUMED_RULE_IDS
    analysis_consumable = analysis_id == ADHOC_ANALYSIS_ID or DEFAULT_STORE.has(analysis_id)
    applied = consumed_rule and analysis_consumable
    scope = f"subject:{subject}" if subject else "rule-wide"
    if applied:
        bound_via = (
            "pod analysis_id='adhoc' (ścieżka niezwiązana z analizą)"
            if analysis_id == ADHOC_ANALYSIS_ID
            else f"pod analysis_id='{analysis_id}' (propose_layout analysis-bound, Phase 12)"
        )
        note = (
            "Override zapisany z pełnym audytem (F-0138) i AKTYWNY "
            f"(zakres: {scope}): walidatory między-budynkowe Phase 10 konsumują "
            f"OverrideStore dla tej reguły {bound_via}. Wynik nadpisany przy "
            "najbliższej ewaluacji, z audytem w trace; override bez sufiksu "
            "'#podmiot' obejmuje CAŁĄ regułę (rule-wide)."
        )
    elif consumed_rule:
        note = (
            "Override zapisany z pełnym audytem (F-0138), ale jeszcze NIE "
            f"konsumowany: analysis_id='{analysis_id}' nie istnieje w magazynie "
            "analiz, więc żadna ścieżka produkcyjna pod nim nie ewaluuje. "
            "Aktywuje się, gdy parcel_analyze utworzy analizę o tym id i "
            "propose_layout(analysis_id=...) zostanie pod nim wywołane — "
            "applied=False (§21)."
        )
    else:
        note = (
            "Override zapisany z pełnym audytem (F-0138), ale ta reguła nie jest "
            "konsumowana przez żadną ścieżkę produkcyjną (Phase 10 obejmuje "
            "reguły WT/ppoż między-budynkowe) — applied=False (§21)."
        )
    return {
        "override_id": override.id,
        "analysis_id": analysis_id,
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
        "user_id": user_id,
        "applied": applied,
        "audit_logged": True,
        "status": "active" if applied else "recorded",
        "scope": scope,
        "audit": override.model_dump(mode="json"),
        "note": note,
    }


# --------------------------------------------------------------------------- #
# Diagnostics (§10.3 diagnostics_run / F-0442)
# --------------------------------------------------------------------------- #
def diagnostics_run(
    *,
    ruleset_version: str,
    rule_count: int,
    dev_hot_reload: bool,
    last_reload_at: str | None,
    server_version: str,
    ruleset_errors: list[str] | tuple[str, ...] = (),
    probe_connectors: bool = False,
) -> dict[str, Any]:
    """Self-diagnostics (F-0442) extended by Phase 13 (F-0439–0446):

    * **source freshness** — every SourceRecord referenced by the STORED
      analyses vs the per-source max-age config (F-0439); degraded freshness
      reports the safe-failure recommendation ``evaluation_mode=conservative``
      (F-0443/0445);
    * **ruleset freshness** — registry version + per-rule valid_from/valid_to
      span vs today (F-0440);
    * **connector autotest** (F-0441) — the EXISTING ``healthcheck()`` contract
      over the active connector bundle; network probes run ONLY when
      ``probe_connectors=True`` (the default report lists the connectors with
      ``not_probed`` so diagnostics stays zero-network);
    * **last task-graph failures** — recent failed nodes across graphs.
    """
    from plot_agent.monitoring import (
        connector_autotest,
        evaluation_mode_for,
        ruleset_freshness,
        source_freshness,
    )
    from plot_agent.orchestrator import DEFAULT_GRAPH_STORE
    from plot_rules import load_rulesets

    # Source freshness over every source record the stored analyses cite.
    sources: list[dict[str, Any]] = []
    for result in DEFAULT_STORE.all():
        if isinstance(result.planning, dict):
            sources.extend(result.planning.get("_sources") or [])
    source_report = source_freshness(sources)

    # Ruleset freshness from a FRESH load (hot-reload semantics, like _rule_raw).
    ruleset_report = ruleset_freshness(load_rulesets("rulesets/PL"))

    return {
        "server_version": server_version,
        "usecases_build": USECASES_BUILD,
        "ruleset_version": ruleset_version,
        "rule_count": rule_count,
        # Rules skipped at load time due to schema validation ("<path>: <error>").
        "ruleset_errors": list(ruleset_errors),
        "dev_hot_reload": dev_hot_reload,
        "last_reload_at": last_reload_at,
        "connectors": (
            _run_async(connector_autotest(_named_connectors()))
            if probe_connectors
            else [
                {"name": name, "status": "not_probed", "healthy": None}
                for name in _named_connectors()
            ]
        ),
        "source_freshness": source_report.to_dict(),
        "ruleset_freshness": ruleset_report.to_dict(),
        # Safe-failure recommendation (F-0443/0445): stale sources → conservative.
        "evaluation_mode": evaluation_mode_for(source_report, default="strict"),
        "task_graph_failures": DEFAULT_GRAPH_STORE.recent_failures(),
        "checked_at": _now().isoformat(),
    }


def _named_connectors() -> dict[str, Any]:
    """The active connector bundle as a name → connector map (F-0441 autotest).

    Uses the INJECTED bundle when one is set (tests: mocks; healthcheck-less
    mocks report ``no_healthcheck``), else the production bundle.
    """
    bundle = _get_connectors()
    named: dict[str, Any] = {"uldk": bundle.uldk}
    risk = bundle.risk_layers
    for kind, connector in (getattr(risk, "connectors", None) or {}).items():
        named[f"risk:{getattr(kind, 'value', kind)}"] = connector
    if bundle.terrain is not None:
        named["terrain"] = getattr(bundle.terrain, "connector", bundle.terrain)
    if bundle.buildings is not None:
        named["buildings"] = getattr(bundle.buildings, "connector", bundle.buildings)
    return named


# --------------------------------------------------------------------------- #
# Phase 4 generative drawing loop wiring (§4.1.C). propose_layout validates a typed
# LayoutProposal against hard constraints, renders it, scores+critiques it, and returns
# the structured result; the server inlines the PNG image content block so the model
# SEES its drawing (reuses the Phase 3 image path). Since Phase 12 the tool is
# ANALYSIS-BINDABLE: an explicit ``analysis_id`` makes parcel/envelope/indicators/
# context come from the STORED analysis; without one the legacy adhoc path (sample
# geometry / injected test context) is byte-compatible.
# --------------------------------------------------------------------------- #
# Injectable drawing context for propose_layout/report koncepcja (same idiom as
# set_connectors): tests inject an AnalysisContext; it applies ONLY to the
# UNBOUND (adhoc) path — an explicit analysis_id always wins (Phase 12 binding).
_DRAWING_CONTEXT: Any | None = None


def set_drawing_context(context: Any | None) -> None:
    """Override the AnalysisContext used by the UNBOUND drawing paths (test seam)."""
    global _DRAWING_CONTEXT
    _DRAWING_CONTEXT = context


def _drawing_context(ruleset_dir: str | None = None) -> Any:
    """Build an AnalysisContext from the Phase 3 sample geometry + loaded rules (§4.1.C)."""
    from plot_agent import AnalysisContext
    from plot_reports.preview import sample_preview_layers
    from plot_reports.render import LayerRole

    if _DRAWING_CONTEXT is not None:
        return _DRAWING_CONTEXT
    layers = sample_preview_layers()
    by_role: dict[Any, list[Any]] = {}
    for layer in layers:
        by_role.setdefault(layer.role, []).extend(layer.shapely_geometries())
    parcel = by_role[LayerRole.PARCEL][0]
    envelope = by_role[LayerRole.BUILDABLE_ENVELOPE][0]
    hard = by_role.get(LayerRole.NO_BUILD, [])
    soft = by_role.get(LayerRole.CONSTRAINT_SOFT, [])
    return AnalysisContext.with_loaded_rules(
        parcel=parcel,
        buildable_envelope=envelope,
        ruleset_dir=ruleset_dir or "rulesets/PL",
        hard_constraints=list(hard),
        soft_constraints=list(soft),
    )


def _analysis_drawing_context(analysis_id: str) -> Any | None:
    """Build an AnalysisContext from a STORED analysis (Phase 12 delta 3 binding).

    Parcel + buildable envelope + hard/soft constraint geometries come from the
    stored :class:`AnalysisResult` (the same data the analysis tools report).
    Returns ``None`` when the analysis is missing or carries no parcel geometry.
    """
    from plot_agent import AnalysisContext
    from shapely.geometry import shape

    result = DEFAULT_STORE.get(analysis_id)
    if result is None or result.parcel is None or result.parcel.geometry is None:
        return None
    parcel = shape(result.parcel.geometry)
    env = result.buildable_envelope
    envelope = shape(env.geometry) if env is not None and env.geometry else parcel
    hard: list[Any] = []
    soft: list[Any] = []
    for con in result.constraints:
        if con.geometry is None:
            continue
        geom = shape(con.geometry)
        if geom.is_empty:
            continue
        (hard if bool(con.machine_summary.get("hard")) else soft).append(geom)
    return AnalysisContext.with_loaded_rules(
        parcel=parcel,
        buildable_envelope=envelope,
        hard_constraints=hard,
        soft_constraints=soft,
    )


def _context_for(analysis_id: str | None) -> Any:
    """Resolve the drawing context: analysis-bound when an id is given (Phase 12).

    Precedence (documented): explicit ``analysis_id`` → the stored analysis (a
    missing/geometry-less analysis raises a clear error — never a silent fall
    back to sample geometry, §21); no id → the injected test context or the
    Phase 3 sample.
    """
    if analysis_id is None:
        return _drawing_context()
    context = _analysis_drawing_context(analysis_id)
    if context is None:
        raise ValueError(
            f"analysis_id '{analysis_id}' nie istnieje w magazynie analiz albo nie ma "
            "geometrii działki — najpierw uruchom parcel_analyze (Phase 12 binding)."
        )
    return context


def propose_layout_render(
    proposal_payload: dict[str, Any],
    indicators: dict[str, Any] | list[dict[str, Any]] | None = None,
    rationale: str | None = None,
    analysis_id: str | None = None,
) -> dict[str, Any]:
    """Validate + render + score + critique a typed proposal (§4.1.C / §4.4; Phase 9).

    Discrimination (Phase 9 §9.1.1): a payload carrying a ``buildings`` key (or
    ``schema_version == 2``) takes the MASTERPLAN path (ingest validation → capacity
    metrics → renderer v2 → masterplan score); anything else takes the UNCHANGED v1
    ``LayoutProposal`` path byte-for-byte. Returns a dict with the rendered PNG bytes
    plus structured ``{score, critique, accepted, violations}`` (masterplans add
    ``metrics``/``unknowns``/``variant_id``). The proposal is parsed by Pydantic (typed
    DATA, never trusted free-form — NFR-SEC-003); a hard-violating proposal returns
    ``accepted=False`` regardless of its score (§14.2).

    ``rationale`` (Phase 11 §11.1.6) is the model's free-text design reasoning for
    this iteration: it is persisted in the audit record and surfaced in the
    deliverable ONLY — validators/scoring/critique never receive it (NFR-SEC-003).

    ``analysis_id`` (Phase 12 delta 3) BINDS the evaluation to a stored analysis:
    parcel/envelope/constraints come from that analysis (not the sample context),
    indicators default to its parsed planning indicators, variants/audit/overrides
    scope to it, and the site-context masterplan checks (earthworks per building,
    per-stage flood clip, heritage interventions, zjazd KDW) run when the analysis
    has stored site context. Without an id the legacy adhoc path is unchanged.
    """
    if "buildings" in proposal_payload or proposal_payload.get("schema_version") == 2:
        return _propose_masterplan_render(proposal_payload, indicators, rationale, analysis_id)

    from plot_agent.drawing import DrawingLoop, LayoutProposal

    context = _context_for(analysis_id)
    # Parse/validate the proposal — malformed/out-of-range input fails here (typed guard).
    proposal = LayoutProposal.model_validate(proposal_payload)
    loop = DrawingLoop(context=context)
    result = loop.iterate(proposal, rationale=rationale)
    return {
        "png_bytes": result.render_image_bytes,
        "mime_type": result.render_mime,
        "accepted": result.accepted,
        "valid": result.score.valid,
        "score": result.score.to_dict(),
        "critique": result.critique.to_dict(),
        "violations": [v.to_dict() for v in result.score.violations],
        "artifact_uri": result.artifact_uri,
        "rationale": result.rationale,
        # Audit entry (F-0446) recorded inside the loop; surface its summary here.
        "audit": loop.audit_log[-1].to_dict() if loop.audit_log else None,
        "note": "Footprint validated against hard constraints BEFORE scoring (§14.2).",
    }


def _masterplan_site_checks(
    site_context: Any | None, context: Any, proposal: Any
) -> dict[str, Any] | None:
    """Phase 12 delta 1: site-context checks for a BOUND masterplan iteration.

    Consumes the typed :class:`plot_planning.site_context.SiteContext` stored by
    ``parcel_analyze(full_due_diligence)``:

    * **earthworks_per_building** — terrain slope inside each footprint → risk
      class (unknown when the terrain raster is absent — never "flat by default");
    * **flood_stages** — per-stage flood flags + envelope area after the flood
      clip (stages whose buildings stand in a flood zone are flagged);
    * **heritage_interventions** — ``zabytek_do_remontu`` → konserwator question;
      new buildings inside a heritage zone → warning (soft, validator-style);
    * **zjazd** — the KDW network must reach the parcel frontage at a public
      road (fail = masterplan has no legal access point);
    * **utility_collisions** — buildings/roads crossing technical zones;
    * **neighbor_shading** — impact report TO neighbors, REUSING the §60 sun
      engine (soft; thresholds/window read from the wt-60 ruleset).

    Returns ``None`` when no site context is bound (unbound/adhoc evaluation).
    """
    if site_context is None:
        return None
    from plot_planning.site_context import (
        check_zjazd_kdw,
        heritage_interventions,
        neighbor_shading_impact,
    )
    from plot_planning.wt_validators import ValidatorConfig
    from plot_planning.wt_validators.config import RULE_WT60, rule_threshold
    from plot_planning.wt_validators.context import NEW_STATUSES, ObstructorPart

    checks: dict[str, Any] = {}
    envelope_geom = context.envelope_geom()
    buildings_named = [
        (b.name, str(b.status), b.footprint_geometry()) for b in proposal.buildings
    ]

    # Terrain → earthworks risk per building (delta 1).
    terrain = site_context.terrain
    if terrain is not None:
        checks["earthworks_per_building"] = {
            name: terrain.earthworks_for_footprint(geom)
            for name, _status, geom in buildings_named
        }

    # Flood → per-stage envelope clipping + flags (delta 1).
    water = site_context.water
    if water is not None:
        checks["flood_stages"] = water.stage_flood_checks(
            envelope_geom,
            [(b.name, b.stage, b.footprint_geometry()) for b in proposal.buildings],
        )

    # Heritage → zabytek_do_remontu / new-in-zone warnings (delta 1).
    checks["heritage_interventions"] = heritage_interventions(
        site_context.environment, buildings_named
    )

    # Roads → KDW zjazd connection point (delta 1).
    access = site_context.access
    kdw = [
        (f"kdw-{i + 1}", r.centerline_geometry())
        for i, r in enumerate(proposal.roads)
        if str(r.function) == "kdw"
    ]
    checks["zjazd"] = check_zjazd_kdw(
        kdw,
        context.parcel_geom(),
        access._road_geom if access is not None else None,
    )

    # Utilities → technical-zone collisions vs proposal buildings + roads.
    if access is not None:
        elements: list[tuple[str, Any]] = [(n, g) for n, _s, g in buildings_named]
        elements += [
            (f"droga-{i + 1}", r.to_polygon()) for i, r in enumerate(proposal.roads)
        ]
        checks["utility_collisions"] = access.network_collisions(elements)

    # Neighbor shading impact (soft) — REUSES the §60 engine; window + minimum
    # come from the wt-60 ruleset (legal values never live in code).
    neighbors = list(site_context.neighbors)
    if neighbors:
        cfg = ValidatorConfig()
        rule = context.ruleset.get(RULE_WT60)
        if rule is not None:
            def _parts(*, new: bool) -> list[Any]:
                return [
                    ObstructorPart(
                        owner=b.name,
                        geometry=seg.geometry(),
                        height_m=int(seg.floors) * cfg.floor_height_m,
                        source="proposal",
                        is_new=new,
                    )
                    for b in proposal.buildings
                    if (b.status in NEW_STATUSES) is new
                    for seg in b.segments
                ]

            checks["neighbor_shading"] = neighbor_shading_impact(
                _parts(new=True),
                neighbors,
                config=cfg,
                window_start_h=rule_threshold(rule, "dwelling_window_start_h"),
                window_end_h=rule_threshold(rule, "dwelling_window_end_h"),
                min_required_hours=rule_threshold(rule, "min_hours_equinox"),
                # Review m3: the proposal's EXISTING on-parcel buildings are
                # obstructor context in BOTH runs — only the NEW parts may be
                # charged with insolation loss.
                existing_parts=_parts(new=False),
            )
        else:
            checks["neighbor_shading"] = [
                {"status": "unknown", "reason": "wt60_rule_missing"}
            ]
    return checks


def _propose_masterplan_render(
    proposal_payload: dict[str, Any],
    indicators: dict[str, Any] | list[dict[str, Any]] | None = None,
    rationale: str | None = None,
    analysis_id: str | None = None,
) -> dict[str, Any]:
    """Masterplan path of ``propose_layout`` (Phase 9 §9.1 + Phase 10 §10.1.7 +
    Phase 11 §11.1.3 + Phase 12 binding): one iteration of the masterplan loop v2.

    The :class:`plot_agent.drawing.DrawingLoop` runs the full pipeline —
    ingest-validate → capacity metrics → inter-building WT/ppoż validators →
    staging checks → score (hard-blocker dominance §14.2) → STRUCTURED critique
    (rule ids + subjects + capacity gap vs the base-scenario target) → renderer v2
    (violation overlay) → exemplar learn → audit (F-0446 with inputs hash +
    rationale). Audited expert overrides from
    :data:`plot_rules.DEFAULT_OVERRIDE_STORE` are consumed under the evaluation's
    analysis id: the BOUND ``analysis_id`` when given (Phase 12 delta 3) or the
    legacy ``"adhoc"`` id. Variant metrics are stored in
    :data:`plot_agent.drawing.DEFAULT_VARIANT_STORE` and served by the
    ``analysis://{analysis_id}/masterplan/{variant_id}/metrics.json`` resource;
    the audit entry (with the model's ``rationale``) is appended to
    :data:`plot_agent.drawing.DEFAULT_MASTERPLAN_AUDIT` for the koncepcja report.

    Phase 12 delta 1 (analysis-bound only): when the bound analysis has a stored
    site context, the loop receives the BDOT10k ``neighbors`` (so §13/§60 account
    for neighbor shadows) and the result carries ``site_checks`` — earthworks
    risk per building, per-stage flood envelope clipping, heritage-intervention
    warnings (``zabytek_do_remontu`` → konserwator) and the KDW zjazd check.

    Phase 13 review M2 (F-0443/0445): the bound analysis' stored source-
    freshness verdict (``planning['_freshness']``, stamped at full-DD time)
    drives the inter-building checks' rule-evaluation mode — a degraded verdict
    FORCES ``conservative`` and the result carries ``evaluation_mode`` plus a
    ``freshness`` block with a banner, so the model sees that stale sources
    degraded the evaluation basis. Freshness never relaxes the mode.
    """
    from plot_agent.analysis import DEFAULT_SITE_CONTEXT_STORE
    from plot_agent.drawing import (
        DEFAULT_MASTERPLAN_AUDIT,
        DEFAULT_VARIANT_STORE,
        DrawingLoop,
        MasterplanProposal,
    )
    from plot_domain import BuildingRecord, MasterplanVariant
    from plot_planning import CapacityConfig, building_storeys
    from plot_reports import RenderResult, get_artifact_store
    from plot_rules import DEFAULT_OVERRIDE_STORE
    from shapely.geometry import mapping

    aid = analysis_id if analysis_id is not None else ADHOC_ANALYSIS_ID
    context = _context_for(analysis_id)
    site_context = (
        DEFAULT_SITE_CONTEXT_STORE.get(analysis_id) if analysis_id is not None else None
    )
    # Ingest validation (make_valid / finite coords / EPSG:2180 plausibility) runs in
    # the Pydantic validators; buildings-within-parcel is a SCORING-time violation.
    proposal = MasterplanProposal.model_validate(proposal_payload)
    freshness_verdict: dict[str, Any] | None = None
    if analysis_id is not None:
        stored = DEFAULT_STORE.get(analysis_id)
        if indicators is None and stored is not None and isinstance(stored.planning, dict):
            # Phase 12 binding: indicators default to the STORED analysis' parsed
            # planning indicators; an explicit argument always wins.
            indicators = stored.planning.get("indicators")
        if stored is not None and isinstance(stored.planning, dict):
            # Review M2 (F-0443/0445): the analysis' stored source-freshness
            # verdict (stamped at full-DD time) drives the rule-evaluation mode
            # of the inter-building checks below.
            raw_verdict = stored.planning.get("_freshness")
            if isinstance(raw_verdict, dict):
                freshness_verdict = raw_verdict
    indicator_map = _indicator_map(indicators)
    # Freshness can only TIGHTEN the mode (§21): a degraded verdict forces
    # "conservative"; a fresh or absent verdict keeps the validators' own
    # conservative default — it is never relaxed to strict/optimistic here.
    evaluation_mode = "conservative"
    freshness_block: dict[str, Any] | None = None
    if freshness_verdict is not None:
        degraded_sources = bool(freshness_verdict.get("degraded"))
        freshness_block = {
            "degraded": degraded_sources,
            "stale_sources": list(freshness_verdict.get("stale_sources") or []),
            "reason": freshness_verdict.get("reason"),
            "checked_at": freshness_verdict.get("checked_at"),
        }
        if degraded_sources:
            freshness_block["banner"] = (
                "tryb konserwatywny: źródła nieaktualne — niewiadome na regułach "
                "twardych raportowane jako potencjalne blokery (F-0443/0445)"
            )
    # Seed the critique's "improved vs previous iteration" comparison from the last
    # masterplan audit entry OF THIS ANALYSIS — each propose_layout call builds a
    # fresh loop, so the cross-call session continuity lives in the chronological
    # audit log; entries from other analyses/sessions are never consulted (F1).
    # The seeding entry's variant id is also this iteration's lineage parent.
    previous_entries = DEFAULT_MASTERPLAN_AUDIT.entries(analysis_id=aid)
    previous_entry = previous_entries[-1] if previous_entries else None
    previous_components = (
        dict(previous_entry.get("components") or {}) if previous_entry else None
    )
    parent_variant_id = previous_entry.get("variant_id") if previous_entry else None
    loop = DrawingLoop(
        context=context,
        indicators=indicator_map,
        override_store=DEFAULT_OVERRIDE_STORE,
        analysis_id=aid,
        artifact_store=get_artifact_store(),  # same store as the variant artifact
        previous_components=previous_components,
        # Phase 12 §10.1.6: bound analyses contribute their BDOT10k neighbors so
        # §13/§60 see neighbor shadows (the validators' existing `neighbors` API).
        neighbors=tuple(site_context.neighbors) if site_context is not None else (),
        # Review M2: the freshness-derived mode reaches run_inter_building_checks.
        evaluation_mode=evaluation_mode,
    )
    result = loop.iterate_masterplan(proposal, rationale=rationale)
    metrics = result.metrics
    assert metrics is not None  # the masterplan path always computes metrics
    checks_json = [c.model_dump(mode="json") for c in result.inter_building_checks]
    staging_json = [c.model_dump(mode="json") for c in result.staging_checks]
    score = result.score
    crit = result.critique

    variant_id = f"mvar:{uuid.uuid4().hex[:8]}"
    # Persist the render under the variant key too (the loop already audited its own
    # iteration artifact) so the per-variant style sidecar stays addressable
    # (NFR-AUD-009: <artifact>.style.json next to masterplan/<variant>.png).
    render = RenderResult(
        data=result.render_image_bytes,
        mime_type=result.render_mime,
        style_metadata=result.style_metadata or {},
    )
    artifact_uri = get_artifact_store().put_render(f"masterplan/{variant_id}.png", render)
    unknowns_json = [u.model_dump(mode="json") for u in metrics.unknowns]
    # Phase 12 delta 1: site-context masterplan checks for BOUND analyses.
    site_checks = _masterplan_site_checks(site_context, context, proposal)
    variant = MasterplanVariant(
        id=variant_id,
        # Owning-analysis stamp (F1): the BOUND analysis id when given (Phase 12),
        # else the legacy adhoc id — the koncepcja report + latest() resolve
        # variants per analysis, never across sessions.
        analysis_id=aid,
        buildings=[
            BuildingRecord(
                id=f"bld:{variant_id}:{i + 1}",
                name=building.name,
                geometry=dict(mapping(building.footprint_geometry())),
                floors_by_segment=[s.floors for s in building.segments],
                uses=[s.use for s in building.segments],
                stage=building.stage,
                status=building.status,
                underground_floors=building.underground_floors,
                metrics=bm.to_dict(),
                # Phase 15 Task 1: storeys filled from the DSL floors at
                # variant-store time — SAME CapacityConfig the metrics used
                # (DrawingLoop.iterate_masterplan passes CapacityConfig()), so
                # the basis-marked storey heights match the metrics basis block.
                storeys=building_storeys(building, CapacityConfig()),
            )
            for i, (building, bm) in enumerate(
                zip(proposal.buildings, metrics.per_building, strict=True)
            )
        ],
        roads=[r.model_dump(mode="json") for r in proposal.roads],
        parking=[p.model_dump(mode="json") for p in proposal.parking],
        greenery=list(proposal.greenery_polygons),
        playgrounds=list(proposal.playgrounds),
        retention=list(proposal.retention),
        totals=metrics.totals,
        stage_table=metrics.stage_table,
        metadata={
            "config_basis": metrics.config_basis,
            "ruleset_version": context.ruleset.ruleset_version,
            "zabudowa_srodmiejska": proposal.zabudowa_srodmiejska,
            # Phase 12 binding marker (review m2): True only for an explicit
            # ``propose_layout(analysis_id=...)`` evaluation — the koncepcja
            # report hard-errors when a BOUND variant's analysis geometry is
            # gone instead of silently rendering on the sample geometry.
            "analysis_bound": analysis_id is not None,
            # Phase 10: the WT/ppoż rule outcomes travel with the stored variant
            # (served by the metrics.json resource — full trace + evidence).
            "inter_building_checks": checks_json,
            # Phase 11: etapowanie consistency outcomes (always soft).
            "staging_checks": staging_json,
            # Phase 11 §11.1.7: unknowns + critique travel with the variant so the
            # koncepcja report assembles without recomputation.
            "unknowns": unknowns_json,
            "critique": crit.to_dict(),
            # Phase 15: the capacity-engine zestawienie powierzchni travels with
            # the variant so the PZT opisowa renders THE SAME numbers (single
            # source of truth — anti-pattern §15.4, test-enforced equality).
            "zestawienie_powierzchni": metrics.zestawienie,
            # Lineage pointer (F1): the previous iteration of the SAME analysis
            # (the audit entry that seeded previous_components), None for the first.
            "parent_variant_id": parent_variant_id,
            # Phase 12 delta 1: site-context masterplan checks (None when unbound
            # or the analysis has no stored site context — honest absence).
            "site_checks": site_checks,
        },
    )
    DEFAULT_VARIANT_STORE.put(variant)

    accepted = result.accepted
    # Masterplan audit record (F-0446 + Phase 11 §11.1.6): the loop's entry
    # (inputs hash, scores, critique, rationale, iteration artifact) extended with
    # the MCP-level variant pointer; appended to the chronological audit log the
    # koncepcja report reads the design-rationale section from.
    audit = dict(loop.audit_log[-1].to_dict())  # carries analysis_id (loop stamp)
    audit["variant_id"] = variant_id
    audit["parent_variant_id"] = parent_variant_id  # lineage chain (F1)
    audit["buildings"] = len(proposal.buildings)
    audit["inter_building_checks"] = checks_json
    audit["variant_artifact_uri"] = artifact_uri
    DEFAULT_MASTERPLAN_AUDIT.append(audit)
    return {
        "png_bytes": result.render_image_bytes,
        "mime_type": result.render_mime,
        "accepted": accepted,
        "valid": score.valid,
        "score": score.to_dict(),
        "critique": crit.to_dict(),
        "violations": [v.to_dict() for v in score.violations],
        "artifact_uri": artifact_uri,
        "audit": audit,
        "schema_version": 2,
        "variant_id": variant_id,
        "rationale": rationale,
        "exemplar_id": result.exemplar.exemplar_id if result.exemplar else None,
        "metrics": metrics.to_dict(),
        "unknowns": unknowns_json,
        "analysis_id": aid,
        # Review M2 (F-0443/0445): the mode the inter-building checks actually
        # ran under + the bound analysis' freshness verdict (banner when the
        # sources behind the analysis are stale) — the model SEES the basis.
        "evaluation_mode": evaluation_mode,
        "freshness": freshness_block,
        # Phase 12 delta 1: site-context checks (earthworks / flood stages /
        # heritage interventions / zjazd) — None when no site context is bound.
        "site_checks": site_checks,
        "metrics_resource": f"analysis://{aid}/masterplan/{variant_id}/metrics.json",
        # Phase 10: full WT/ppoż rule outcomes (trace + geometry evidence) + the
        # render's style-metadata sidecar (the violation overlay layer is auditable
        # there too — NFR-AUD-009; persisted at <artifact>.style.json).
        "inter_building_checks": checks_json,
        # Phase 11: etapowanie consistency (soft warnings, never hard violations).
        "staging_checks": staging_json,
        "style_metadata": result.style_metadata,
        "note": (
            "Masterplan DSL v2: walidacja twardych ograniczeń PRZED punktacją (§14.2); "
            "metryki chłonności z basis-metadanymi; walidatory między-budynkowe WT/ppoż "
            "(Phase 10) ocenione z progami z rulesetów — FAIL na regule twardej = hard "
            "violation, geometria naruszeń w czerwonej warstwie overlay; krytyka "
            "strukturalna cytuje rule_id + podmiot + lukę chłonności (Phase 11)."
        ),
    }


def selfimprove_run(ruleset_dir: str | None = None) -> dict[str, Any]:
    """[dev] Run the golden scenarios and return the before/after Verdict (§4.1.C).

    Within one call no external edit happens between snapshots, so the verdict is
    all-``unchanged`` by construction; the value is the structured before/after scores +
    screenshot artifact uris the dev-loop produces (the regression signal is exercised
    when Claude Code edits code/rulesets between snapshots in a real session).
    """
    from plot_agent.selfimprove import DevLoop, sample_scenarios

    loop = DevLoop(scenarios=sample_scenarios(), ruleset_dir=ruleset_dir or "rulesets/PL")
    loop.snapshot_before()
    loop.reload()
    loop.snapshot_after()
    verdict = loop.verdict(persist_screenshots=True)
    return verdict.to_dict()


# Keep example builders importable for stubs that need fully-typed objects later.
def _example_source() -> SourceRecord:  # pragma: no cover - helper for future phases
    from plot_domain.enums import (
        Freshness,
        GeometryPrecision,
        LegalStatus,
        SourceType,
    )

    return SourceRecord(
        source_id=_new_id(),
        source_type=SourceType.OFFICIAL_REGISTER,
        publisher="GUGiK",
        url_or_origin="https://uldk.gugik.gov.pl/",
        retrieved_at=_now(),
        license="unknown",
        legal_status=LegalStatus.BINDING,
        geometry_precision=GeometryPrecision.CADASTRAL,
        freshness=Freshness.UNKNOWN,
        confidence=0.0,
    )


def _example_run() -> AnalysisRun:  # pragma: no cover - helper for future phases
    return AnalysisRun(
        id=_new_id(),
        input_hash="stub",
        status=AnalysisStatus.PARTIAL,
        mode=AnalysisMode.QUICK_SCREENING,
        ruleset_version="PL-empty",
    )


def _example_risk() -> RiskItem:  # pragma: no cover - helper for future phases
    from plot_domain.enums import ConfidenceLevel, RiskStatus, RiskType

    return RiskItem(
        id=_new_id(),
        risk_type=RiskType.DATA_QUALITY,
        severity=Severity.INFO,
        confidence=ConfidenceLevel.LOW,
        status=RiskStatus.UNKNOWN,
        summary="stub",
    )


def _example_recommendation() -> Recommendation:  # pragma: no cover - helper
    return Recommendation(id=_new_id(), title="stub", detail="stub")
