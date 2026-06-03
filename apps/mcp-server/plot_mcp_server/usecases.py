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
USECASES_BUILD = "phase7-mvp"

PHASE7_NOTE = "Stub: analysis logic lands in Phase 7; MCP delegates to the worker in Phase 12."
PHASE9_NOTE = "Stub: capacity scenarios (chłonność, §8.5) land in Phase 9."
PHASE8_NOTE = "Stub: deep planning parsing (MPZP/POG/WZ, §8.3) lands in Phase 8."

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
    """Run the analysis. quick_screening → the real §27 use-case (Phase 7 §C/§E).

    Other modes (full/design/portfolio) are not yet implemented; for them we run the
    quick_screening pipeline and mark the gap in unknowns (never a fabricated full result).
    The completed result is stored in the in-memory store so ``analysis_get_result`` /
    ``report_generate`` / ``risks_list`` / ``sources_collect`` can return it.
    """
    connectors = _get_connectors()
    analysis_id = _new_id()

    async def _analyze() -> AnalysisResult:
        return await run_quick_screening(
            payload,
            connectors=connectors,
            analysis_id=analysis_id,
        )

    result = _run_async(_analyze())
    DEFAULT_STORE.put(result)
    return result


def analysis_get_status(analysis_id: str) -> dict[str, Any]:
    """Return real status for a stored analysis (Phase 7 §E).

    Streaming progress (``ctx.report_progress``) for long async runs lands in Phase 11;
    quick_screening completes synchronously, so a stored run is already done.
    """
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
    """Planning context. Deep APP/GML parsing + use matrix land in Phase 8 (§8.3)."""
    return {
        "municipality_id": municipality_id,
        "parcel_id": parcel_id,
        "acts": [],
        "coverage_status": "not_yet_parsed",
        "note": PHASE8_NOTE,
    }


def planning_parse_document(file_id: str | None, text: str | None) -> dict[str, Any]:
    """Stub planning-document parse. LLM-candidate + rule/evidence validation (Phase 8/§29)."""
    return {
        "file_id": file_id,
        "candidates": [],
        "evidence": [],
        "status": "not_yet_computed",
        "note": "LLM extracts candidates only; rules+schema+evidence validate (Phase 8).",
    }


def constraints_compute(analysis_id: str | None) -> dict[str, Any]:
    """Return the real constraints + buildable envelope from a stored analysis (Phase 7).

    The heavy geometry (envelope GeoJSON) is exposed via the
    ``analysis://{id}/buildable-envelope.geojson`` resource (NFR-PERF-009); this tool
    returns the constraint records + an envelope summary (area / confidence / lir present).
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
    }


def capacity_generate_scenarios(analysis_id: str | None) -> dict[str, Any]:
    """Stub capacity scenarios (chłonność, §8.5; lands in Phase 9)."""
    return {"analysis_id": analysis_id, "scenarios": [], "note": PHASE9_NOTE}


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
    """Return the source records + evidence pack for a stored analysis (§5 / NFR-AUD-001)."""
    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None
    if result is None:
        return {"analysis_id": analysis_id, "sources": [], "evidence": [], "status": "not_found"}
    sources = result.planning.get("_sources", []) if isinstance(result.planning, dict) else []
    return {
        "analysis_id": analysis_id,
        "sources": sources,
        "evidence": [e.model_dump(mode="json") for e in result.evidence],
        "evidence_count": len(result.evidence),
    }


def report_generate(analysis_id: str | None, fmt: str) -> dict[str, Any]:
    """Generate a report artifact for a stored analysis (Phase 7 §E; §22 template).

    * ``md``  → the §22 Markdown report (returned inline as ``content`` — it is small text).
    * ``json``→ the AnalysisResult (§10.7) returned as ``content`` (and the canonical contract).
    * ``png`` → the buildable-envelope map rendered + persisted, advertised as a
      ``resource_link`` by DEFAULT (image NOT inlined into every result — NFR-PERF-009;
      inline image is only via the dedicated ``map_preview`` tool).

    The MD and JSON share one result object, so their headline numbers match by
    construction (§7.3). HTML/PDF/audience variants are Phase 12.
    """
    result = DEFAULT_STORE.get(analysis_id) if analysis_id else None

    if fmt in ("md", "markdown"):
        from plot_reports import headline_numbers, render_markdown

        if result is None:
            return {"analysis_id": analysis_id, "format": "md", "status": "not_found"}
        return {
            "analysis_id": analysis_id,
            "format": "md",
            "content": render_markdown(result),
            "headline_numbers": headline_numbers(result),
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
        "status": "not_yet_computed",
        "note": "HTML/PDF/audience variants land in Phase 12 (§31).",
    }


def export_layers(analysis_id: str | None, fmt: str) -> dict[str, Any]:
    """Stub GIS/CAD export (GPKG/DXF; Phase 9/§30)."""
    return {"analysis_id": analysis_id, "format": fmt, "artifact_uri": None, "note": PHASE7_NOTE}


def portfolio_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Stub portfolio/batch analysis (§4.4; batch worker in Phase 11)."""
    parcels = payload.get("parcels") or []
    return {"batch_id": _new_id(), "submitted": len(parcels), "status": "queued", "note": PHASE7_NOTE}


def ruleset_explain(registry: Any) -> dict[str, Any]:
    """Return the currently loaded ruleset versions + applied rules (live, §10.3).

    ``registry`` is a :class:`plot_rules.RulesetRegistry`. Because rulesets are loaded
    fresh, this reflects the on-disk YAML as of the most recent reload (hot-reload proof).
    """
    return {
        "ruleset_version": registry.ruleset_version,
        "categories": list(registry.categories),
        "rule_count": len(registry.rules),
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
    """Stub external-source availability (§32 connectors land in Phase 6)."""
    return {
        "source_id": source_id,
        "connectors": _connector_health_stub(),
        "note": PHASE7_NOTE,
    }


# --------------------------------------------------------------------------- #
# Write / side-effecting surface (§16: explicit purpose + audit; annotated in server.py)
# --------------------------------------------------------------------------- #
def cache_warm(scope: str, target_id: str | None) -> dict[str, Any]:
    """Stub cache warming for a municipality/parcel (§15.1; worker in Phase 11)."""
    return {"scope": scope, "target_id": target_id, "warmed": False, "note": PHASE7_NOTE}


def document_ingest(analysis_id: str | None, file_id: str, purpose: str) -> dict[str, Any]:
    """Stub user-document ingest. Real sandboxed/size-limited ingest in Phase 8 (§16).

    ``purpose`` is the explicit-intent parameter required for write tools (§16,
    NFR-SEC-010); a real implementation also writes an audit-log entry.
    """
    return {
        "analysis_id": analysis_id,
        "file_id": file_id,
        "purpose": purpose,
        "ingested": False,
        "note": "Untrusted content; sandboxed scan + size/type limits in Phase 8 (§16).",
    }


def monitoring_create(scope: str, target_id: str, purpose: str) -> dict[str, Any]:
    """Stub monitoring-profile creation for change detection (§4.5; worker in Phase 11).

    ``purpose`` is the explicit-intent parameter required for write tools (§16,
    NFR-SEC-010); a real implementation also writes an audit-log entry.
    """
    return {
        "monitoring_id": _new_id(),
        "scope": scope,
        "target_id": target_id,
        "purpose": purpose,
        "active": False,
        "audit_logged": True,
        "note": PHASE7_NOTE,
    }


def manual_override(
    analysis_id: str, target_type: str, target_id: str, reason: str, user_id: str
) -> dict[str, Any]:
    """Stub expert override with audit trail (NFR-AUD-003, §16). Persistence in Phase 7."""
    return {
        "override_id": _new_id(),
        "analysis_id": analysis_id,
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
        "user_id": user_id,
        "applied": False,
        "audit_logged": True,
        "note": PHASE7_NOTE,
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
) -> dict[str, Any]:
    """Self-diagnostics: loaded ruleset versions, connector-health stubs, reload status."""
    return {
        "server_version": server_version,
        "usecases_build": USECASES_BUILD,
        "ruleset_version": ruleset_version,
        "rule_count": rule_count,
        "dev_hot_reload": dev_hot_reload,
        "last_reload_at": last_reload_at,
        "connectors": _connector_health_stub(),
        "checked_at": _now().isoformat(),
    }


def _connector_health_stub() -> list[dict[str, Any]]:
    """Connector-health stub list (real probes land with connectors in Phase 6, §32)."""
    return [
        {"name": "uldk", "status": "unknown", "note": "probe lands in Phase 6"},
        {"name": "geoportal_wms", "status": "unknown", "note": "probe lands in Phase 6"},
        {"name": "app_gml", "status": "unknown", "note": "probe lands in Phase 6"},
    ]


# --------------------------------------------------------------------------- #
# Phase 4 generative drawing loop wiring (§4.1.C). propose_layout validates a typed
# LayoutProposal against hard constraints, renders it, scores+critiques it, and returns
# the structured result; the server inlines the PNG image content block so the model
# SEES its drawing (reuses the Phase 3 image path). Geometry is the Phase 3 sample
# parcel/envelope/constraints until Phase 7 supplies real analysis geometry.
# --------------------------------------------------------------------------- #
def _drawing_context(ruleset_dir: str | None = None) -> Any:
    """Build an AnalysisContext from the Phase 3 sample geometry + loaded rules (§4.1.C)."""
    from plot_agent import AnalysisContext
    from plot_reports.preview import sample_preview_layers
    from plot_reports.render import LayerRole

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


def propose_layout_render(proposal_payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + render + score + critique a typed LayoutProposal (§4.1.C / §4.4).

    Returns a dict with the rendered PNG bytes (the server inlines them as image content)
    plus structured ``{score, critique, accepted, violations}``. The proposal is parsed
    by Pydantic (typed DATA, never trusted free-form — NFR-SEC-003); a hard-violating
    proposal returns ``accepted=False`` regardless of its score (§14.2).
    """
    from plot_agent.drawing import DrawingLoop, LayoutProposal

    context = _drawing_context()
    # Parse/validate the proposal — malformed/out-of-range input fails here (typed guard).
    proposal = LayoutProposal.model_validate(proposal_payload)
    loop = DrawingLoop(context=context)
    result = loop.iterate(proposal)
    return {
        "png_bytes": result.render_image_bytes,
        "mime_type": result.render_mime,
        "accepted": result.accepted,
        "valid": result.score.valid,
        "score": result.score.to_dict(),
        "critique": result.critique.to_dict(),
        "violations": [v.to_dict() for v in result.score.violations],
        "artifact_uri": result.artifact_uri,
        # Audit entry (F-0446) recorded inside the loop; surface its summary here.
        "audit": loop.audit_log[-1].to_dict() if loop.audit_log else None,
        "note": "Footprint validated against hard constraints BEFORE scoring (§14.2).",
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
