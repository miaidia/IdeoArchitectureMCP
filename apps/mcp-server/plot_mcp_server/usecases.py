"""Reloadable use-case layer for the MCP server (Phase 2 §2.1.2 / §2.4).

The thin tool functions in ``server.py`` delegate all domain work to the functions
in this module. This module holds NO MCP/transport state, so it can be safely
``importlib.reload``-ed by the ``dev_reload`` tool / file watcher without touching
the live stdio pipe to Claude Code (Phase 2 anti-pattern: never reload the transport
from within itself — IMPLEMENTATION_PLAN.md Phase 0.5 / §2.4).

Everything here returns schema-valid STUBS. The real analysis logic (and the
thin-proxy-to-worker delegation, §27 shared use-cases) lands in Phase 7 / Phase 12;
each stub records that in its ``unknowns``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

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
USECASES_BUILD = "phase2-stub"

PHASE7_NOTE = "Stub: analysis logic lands in Phase 7; MCP delegates to the worker in Phase 12."


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Read-only analysis surface (§10.3)
# --------------------------------------------------------------------------- #
def parcel_resolve(payload: dict[str, Any]) -> dict[str, Any]:
    """Stub parcel resolution. Real ULDK resolution lands in Phase 6 (Phase 0.3)."""
    return Parcel(
        id=_new_id(),
        external_id=payload.get("parcel_id"),
        teryt=None,
        number=payload.get("parcel_id"),
        input_crs="EPSG:4326",
    ).model_dump(mode="json")


def parcel_analyze(payload: AnalysisInput, ruleset_version: str) -> AnalysisResult:
    """Stub full analysis: schema-valid PARTIAL result needing manual review (Phase 2 §2.1)."""
    analysis_id = _new_id()
    return AnalysisResult(
        analysis_id=analysis_id,
        status=AnalysisStatus.PARTIAL,
        decision=Decision.NEEDS_MANUAL_REVIEW,
        parcel=None,
        planning={},
        constraints=[],
        buildable_envelope=None,
        capacity_scenarios=[],
        risks=[],
        unknowns=[
            UnknownItem(
                id=_new_id(),
                analysis_id=analysis_id,
                topic="analysis_logic",
                severity=Severity.INFO,
                reason="no_data",
                suggested_action=PHASE7_NOTE,
            )
        ],
        next_actions=[],
        evidence=[],
        artifacts=[],
    )


def analysis_get_status(analysis_id: str) -> dict[str, Any]:
    """Stub status. Streaming progress (ctx.report_progress) wired in Phase 11."""
    return {
        "analysis_id": analysis_id,
        "status": AnalysisStatus.PARTIAL.value,
        "progress": 0.0,
        "partial_available": False,
        "note": PHASE7_NOTE,
    }


def analysis_get_result(analysis_id: str) -> AnalysisResult:
    """Stub stored result lookup."""
    return AnalysisResult(
        analysis_id=analysis_id,
        status=AnalysisStatus.PARTIAL,
        decision=Decision.NEEDS_MANUAL_REVIEW,
        unknowns=[
            UnknownItem(
                id=_new_id(),
                analysis_id=analysis_id,
                topic="result_persistence",
                severity=Severity.INFO,
                reason="no_data",
                suggested_action=PHASE7_NOTE,
            )
        ],
    )


def planning_fetch(municipality_id: str | None, parcel_id: str | None) -> dict[str, Any]:
    """Stub planning context. Real connectors (APP/GML, BIP/SIP) land in Phase 6."""
    return {
        "municipality_id": municipality_id,
        "parcel_id": parcel_id,
        "acts": [],
        "status": "not_yet_computed",
        "note": PHASE7_NOTE,
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
    """Stub constraint + buildable-envelope computation (geometry lands in Phase 5)."""
    envelope = BuildableEnvelope(id=_new_id(), analysis_id=analysis_id, area_m2=None)
    return {
        "constraints": [],
        "buildable_envelope": envelope.model_dump(mode="json"),
        "note": PHASE7_NOTE,
    }


def capacity_generate_scenarios(analysis_id: str | None) -> dict[str, Any]:
    """Stub capacity scenarios (chlonnosc, §8.5; lands in Phase 9)."""
    return {"analysis_id": analysis_id, "scenarios": [], "note": PHASE7_NOTE}


def risks_list(analysis_id: str | None) -> dict[str, Any]:
    """Stub red flags / risk register / unknowns (§11.2)."""
    return {"analysis_id": analysis_id, "risks": [], "unknowns": [], "note": PHASE7_NOTE}


def sources_collect(analysis_id: str | None) -> dict[str, Any]:
    """Stub source records + evidence pack (§5). Real collection lands in Phase 6."""
    return {"analysis_id": analysis_id, "sources": [], "evidence": [], "note": PHASE7_NOTE}


def report_generate(analysis_id: str | None, fmt: str) -> dict[str, Any]:
    """Report artifact metadata. For ``png`` the map preview is rendered (Phase 3) and
    stored, returning a ``resource_link``-shaped descriptor by DEFAULT — the image is
    NOT inlined into every result (NFR-PERF-009 / Phase 3 §3.4). Inline image content
    is only returned by the dedicated ``map_preview`` tool / resource preview path.

    Other formats (md/html/pdf/json) remain stubs until Phase 10.
    """
    if fmt == "png":
        # Render the Phase 3 sample preview + persist it (+ style.json sidecar) to the
        # default filesystem artifact store; advertise it as a resource_link target.
        from plot_reports import get_artifact_store, render_preview

        aid = analysis_id or _new_id()
        result = render_preview(analysis_id=aid, fmt="png")
        store = get_artifact_store()
        key = f"analysis/{aid}/map-preview.png"
        uri = store.put_render(key, result)
        return {
            "analysis_id": aid,
            "format": "png",
            # resource_link descriptor — Claude Code fetches the bytes on demand from
            # the analysis://{id}/map-preview.png resource (NFR-PERF-009 default path).
            "artifact_uri": uri,
            "resource_link": f"analysis://{aid}/map-preview.png",
            "mime_type": result.mime_type,
            "byte_size": len(result.data),
            "style_metadata": result.style_metadata,  # CRS + layers + style (NFR-AUD-009)
            "status": "rendered",
            "note": "Image returned as resource_link by default; inline via map_preview (NFR-PERF-009).",
        }
    return {
        "analysis_id": analysis_id,
        "format": fmt,
        "artifact_uri": None,
        "status": "not_yet_computed",
        "note": "Large artifacts are returned as MCP resources, never inlined (NFR-PERF-009).",
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
