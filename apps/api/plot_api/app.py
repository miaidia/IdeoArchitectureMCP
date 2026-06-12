"""FastAPI app — the §27 HTTP surface over the SHARED use-cases (Phase 14B).

Every handler is a THIN delegation into ``plot_mcp_server.usecases`` (module
attribute access at call time, so a dev hot-reload of the use-case module is
picked up here exactly like in the MCP server). NO domain logic lives in this
file — the API⇄MCP parity test asserts both surfaces return the same domain
numbers for the same input (v1 Phase 12 §12.3/§12.4).

Auth: ``X-API-Key`` → role ladder (read/analyst/admin) + tenant tagging — see
``plot_api.auth`` / ``plot_api.tenancy``. Writes are audited (``plot_api.audit``,
F-0481/0494). ``/healthz`` and ``/metrics`` are unauthenticated (liveness +
scrape). Rate limiting (F-0487) and TLS termination are DEPLOYMENT concerns
(reverse proxy) — documented in the README, not faked in-process.
"""

from __future__ import annotations

import base64
import binascii
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from plot_domain import AnalysisInput
from plot_mcp_server import usecases
from plot_shared import Settings, get_settings

from plot_api import __version__
from plot_api.audit import DEFAULT_API_AUDIT, ApiAuditLog
from plot_api.auth import ApiKeyAuth, ApiKeyRecord
from plot_api.metrics import metrics_payload, observe_request
from plot_api.models import (
    CacheWarmRequest,
    DocumentIngestRequest,
    MonitoringRequest,
    OverrideRequest,
    PortfolioRequest,
    ReportRequest,
    ResolveRequest,
)
from plot_api.tenancy import DEFAULT_TENANT_INDEX, TenantIndex

RULESET_DIR = "rulesets/PL"


def _ruleset_version() -> str:
    from plot_rules import load_rulesets

    return load_rulesets(RULESET_DIR).ruleset_version


def create_app(
    settings: Settings | None = None,
    *,
    audit: ApiAuditLog | None = None,
    tenants: TenantIndex | None = None,
) -> FastAPI:
    """Build the API app (auth parsed once from settings; stores injectable for tests)."""
    settings = settings if settings is not None else get_settings()
    auth = ApiKeyAuth(settings)
    # `is None` (not truthiness): an EMPTY ApiAuditLog is falsy via __len__.
    audit = audit if audit is not None else DEFAULT_API_AUDIT
    tenants = tenants if tenants is not None else DEFAULT_TENANT_INDEX

    app = FastAPI(
        title="Plot Analyzer API",
        version=__version__,
        description=(
            "HTTP surface over the SAME domain use-cases as the MCP server (§27). "
            "Auth: X-API-Key header (PLOT_API_KEYS env; roles read/analyst/admin; "
            "tenant-tagged analyses). Rate limiting + TLS = deployment concerns "
            "(reverse proxy)."
        ),
    )

    # ------------------------------------------------------------------ #
    # metrics middleware (F-0470): counts + latency per route template
    # ------------------------------------------------------------------ #
    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        response: Response = await call_next(request)
        route = request.scope.get("route")
        # Label with the ROUTE TEMPLATE only; unmatched URLs fold into one
        # "__unmatched__" label — raw request paths would let any client grow
        # Prometheus series unboundedly (/metrics is unauthenticated).
        endpoint = route.path if route is not None else "__unmatched__"
        observe_request(endpoint, response.status_code, time.perf_counter() - started)
        return response

    # ------------------------------------------------------------------ #
    # auth dependencies (role ladder)
    # ------------------------------------------------------------------ #
    def read_key(request: Request) -> ApiKeyRecord:
        return auth.require(request, "read")

    def analyst_key(request: Request) -> ApiKeyRecord:
        return auth.require(request, "analyst")

    def admin_key(request: Request) -> ApiKeyRecord:
        return auth.require(request, "admin")

    def _check_analysis_access(analysis_id: str, key: ApiKeyRecord) -> None:
        """Cross-tenant analyses 404 (existence must not leak, F-0480)."""
        tenants.check_access(analysis_id, key.tenant_id)

    def _require_stored_analysis(analysis_id: str, key: ApiKeyRecord) -> None:
        """REST 404 for unknown ids (the MCP surface reports honest partials
        instead — same use-case underneath, different envelope per §27)."""
        from plot_agent.analysis import DEFAULT_STORE

        _check_analysis_access(analysis_id, key)
        if not DEFAULT_STORE.has(analysis_id):
            raise HTTPException(status_code=404, detail="Nie znaleziono analizy.")

    # ------------------------------------------------------------------ #
    # liveness + metrics (unauthenticated)
    # ------------------------------------------------------------------ #
    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, Any]:
        from plot_agent.analysis import DEFAULT_STORE

        return {
            "status": "ok",
            "service": "plot-analyzer-api",
            "version": __version__,
            "auth_configured": auth.configured,
            "stores": {
                "analyses": len(DEFAULT_STORE.all()),
                "artifact_backend": "local_fs",
            },
            "broker": {
                "queue_enabled": settings.queue_enabled,
                "kind": "dramatiq-redis" if settings.queue_enabled else "in-process",
            },
        }

    @app.get("/metrics", tags=["ops"])
    def metrics() -> Response:
        payload, content_type = metrics_payload()
        return Response(content=payload, media_type=content_type)

    # ------------------------------------------------------------------ #
    # §27 read surface
    # ------------------------------------------------------------------ #
    @app.post("/v1/parcels/resolve", tags=["parcels"])
    def parcels_resolve(
        body: ResolveRequest, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        return usecases.parcel_resolve({"parcel_id": body.parcel_id, "point": body.point})

    @app.post("/v1/analyses", tags=["analyses"], status_code=201)
    def analyses_create(
        body: AnalysisInput, key: ApiKeyRecord = Depends(analyst_key)
    ) -> dict[str, Any]:
        result = usecases.parcel_analyze(body, _ruleset_version())
        tenants.tag(result.analysis_id, key.tenant_id)
        audit.record(
            action="analysis_created",
            key_id=key.key_id,
            tenant_id=key.tenant_id,
            target_id=result.analysis_id,
            detail={"analysis_mode": body.analysis_mode.value},
        )
        return result.model_dump(mode="json")

    @app.get("/v1/analyses/{analysis_id}", tags=["analyses"])
    def analyses_get(
        analysis_id: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        return usecases.analysis_get_result(analysis_id).model_dump(mode="json")

    @app.get("/v1/analyses/{analysis_id}/status", tags=["analyses"])
    def analyses_status(
        analysis_id: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        # status also serves batch/task-graph ids → no store-presence 404 here;
        # cross-tenant ids still 404 (F-0480).
        _check_analysis_access(analysis_id, key)
        return usecases.analysis_get_status(analysis_id)

    @app.get("/v1/analyses/{analysis_id}/result", tags=["analyses"])
    def analyses_result(
        analysis_id: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        return usecases.analysis_get_result(analysis_id).model_dump(mode="json")

    @app.get("/v1/analyses/{analysis_id}/risks", tags=["analyses"])
    def analyses_risks(
        analysis_id: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        return usecases.risks_list(analysis_id)

    @app.get("/v1/analyses/{analysis_id}/unknowns", tags=["analyses"])
    def analyses_unknowns(
        analysis_id: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        result = usecases.analysis_get_result(analysis_id)
        return {
            "analysis_id": analysis_id,
            "unknowns": [u.model_dump(mode="json") for u in result.unknowns],
        }

    @app.get("/v1/analyses/{analysis_id}/evidence", tags=["analyses"])
    def analyses_evidence(
        analysis_id: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        return usecases.sources_collect(analysis_id)

    @app.get("/v1/analyses/{analysis_id}/buildable-envelope", tags=["analyses"])
    def analyses_envelope(
        analysis_id: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        return usecases.buildable_envelope_geojson(analysis_id)

    @app.get("/v1/analyses/{analysis_id}/report", tags=["reports"])
    def analyses_report(
        analysis_id: str,
        format: str = Query(default="md"),
        audience: str = Query(default="architect"),
        variant_id: str | None = Query(default=None),
        key: ApiKeyRecord = Depends(read_key),
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        return usecases.report_generate(analysis_id, format, variant_id, audience)

    @app.post("/v1/analyses/{analysis_id}/reports", tags=["reports"])
    def analyses_report_post(
        analysis_id: str, body: ReportRequest, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        # §27 POST form — same handler semantics as the GET (rendering is
        # read-only per the MCP annotation; artifacts land in the store).
        _require_stored_analysis(analysis_id, key)
        return usecases.report_generate(
            analysis_id, body.format, body.variant_id, body.audience
        )

    @app.get("/v1/analyses/{analysis_id}/export", tags=["reports"])
    def analyses_export(
        analysis_id: str,
        format: str = Query(default="geojson"),
        variant_id: str | None = Query(default=None),
        key: ApiKeyRecord = Depends(read_key),
    ) -> dict[str, Any]:
        _require_stored_analysis(analysis_id, key)
        return usecases.export_layers(analysis_id, format, variant_id)

    @app.get("/v1/artifacts/{artifact_path:path}", tags=["reports"])
    def artifacts_get(
        artifact_path: str, key: ApiKeyRecord = Depends(read_key)
    ) -> Response:
        from plot_reports import get_artifact_store

        # Tenant guard covers BOTH owner-scoped prefixes: rendered artifacts
        # under analysis/{analysis_id}/... and quarantined uploads under
        # quarantine/{analysis_id}/... (plot_security.uploads). The
        # quarantine/unattached/ segment has no owner — readable by any
        # authenticated key, same documented behaviour as untagged analyses
        # (see plot_api.tenancy). Cross-tenant hits re-raise with the SAME
        # body as a true artifact 404 so existence never leaks (F-0480).
        parts = artifact_path.split("/")
        if len(parts) >= 2 and parts[0] in ("analysis", "quarantine"):
            try:
                _check_analysis_access(parts[1], key)
            except HTTPException:
                raise HTTPException(
                    status_code=404, detail="Nie znaleziono artefaktu."
                ) from None
        try:
            data = get_artifact_store().get(artifact_path)
        except ValueError as exc:  # ArtifactStore traversal guard (F-0490)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Nie znaleziono artefaktu.") from exc
        return Response(content=data, media_type="application/octet-stream")

    @app.get("/v1/sources/health", tags=["sources"])
    def sources_health(
        source_id: str | None = Query(default=None),
        key: ApiKeyRecord = Depends(read_key),
    ) -> dict[str, Any]:
        return usecases.source_healthcheck(source_id)

    @app.get("/v1/rulesets", tags=["rulesets"])
    def rulesets(key: ApiKeyRecord = Depends(read_key)) -> dict[str, Any]:
        from plot_rules import load_rulesets

        return usecases.ruleset_explain(load_rulesets(RULESET_DIR))

    @app.get("/v1/rulesets/{version}", tags=["rulesets"])
    def rulesets_version(
        version: str, key: ApiKeyRecord = Depends(read_key)
    ) -> dict[str, Any]:
        from plot_rules import load_rulesets

        explained = usecases.ruleset_explain(load_rulesets(RULESET_DIR))
        return {
            "requested_version": version,
            "loaded_matches_requested": explained["ruleset_version"] == version,
            **explained,
        }

    # ------------------------------------------------------------------ #
    # write surface (analyst+; every write audited — F-0481/0494)
    # ------------------------------------------------------------------ #
    @app.post("/v1/documents/ingest", tags=["documents"], status_code=201)
    def documents_ingest(
        body: DocumentIngestRequest, key: ApiKeyRecord = Depends(analyst_key)
    ) -> dict[str, Any]:
        from plot_security import (
            UploadFilenameRejected,
            UploadPageCapExceeded,
            UploadTooLarge,
            UploadTypeForbidden,
        )

        # Early oversize rejection BEFORE base64 decode (4/3 expansion + slack).
        if len(body.content_base64) > int(settings.upload_max_bytes * 1.4) + 1024:
            raise HTTPException(
                status_code=413,
                detail=f"Upload przekracza limit {settings.upload_max_bytes} B (F-0493).",
            )
        try:
            content = base64.b64decode(body.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="content_base64 nie jest poprawnym base64."
            ) from exc
        if body.analysis_id:
            _check_analysis_access(body.analysis_id, key)
        try:
            out = usecases.document_upload(
                body.filename,
                content,
                declared_type=body.content_type,
                purpose=body.purpose,
                analysis_id=body.analysis_id,
            )
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except UploadTypeForbidden as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except UploadPageCapExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except UploadFilenameRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(
            action="document_ingested",
            key_id=key.key_id,
            tenant_id=key.tenant_id,
            target_id=out.get("file_id"),
            purpose=body.purpose,
            detail={"filename": body.filename, "size_bytes": out.get("size_bytes")},
        )
        return out

    @app.post("/v1/portfolio/analyze", tags=["portfolio"], status_code=202)
    def portfolio_analyze(
        body: PortfolioRequest, key: ApiKeyRecord = Depends(analyst_key)
    ) -> dict[str, Any]:
        out = usecases.portfolio_analyze({"parcels": body.parcels})
        batch_id = out.get("batch_id")
        if isinstance(batch_id, str):
            tenants.tag(batch_id, key.tenant_id)
        audit.record(
            action="portfolio_submitted",
            key_id=key.key_id,
            tenant_id=key.tenant_id,
            target_id=batch_id,
            detail={"parcels": len(body.parcels)},
        )
        return out

    @app.post("/v1/monitoring", tags=["monitoring"], status_code=201)
    def monitoring_create(
        body: MonitoringRequest, key: ApiKeyRecord = Depends(analyst_key)
    ) -> dict[str, Any]:
        out = usecases.monitoring_create(
            body.scope,
            body.target_id,
            body.purpose,
            body.interval_hours,
            body.webhook_url,
        )
        audit.record(
            action="monitoring_created",
            key_id=key.key_id,
            tenant_id=key.tenant_id,
            target_id=out.get("monitoring_id") or body.target_id,
            purpose=body.purpose,
            detail={"scope": body.scope, "webhook": bool(body.webhook_url)},
        )
        return out

    @app.post("/v1/cache/warm", tags=["sources"], status_code=202)
    def cache_warm(
        body: CacheWarmRequest, key: ApiKeyRecord = Depends(analyst_key)
    ) -> dict[str, Any]:
        out = usecases.cache_warm(body.scope, body.target_id)
        audit.record(
            action="cache_warm",
            key_id=key.key_id,
            tenant_id=key.tenant_id,
            target_id=body.target_id,
            detail={"scope": body.scope},
        )
        return out

    @app.post("/v1/overrides", tags=["overrides"], status_code=201)
    def overrides_create(
        body: OverrideRequest, key: ApiKeyRecord = Depends(admin_key)
    ) -> dict[str, Any]:
        _check_analysis_access(body.analysis_id, key)
        out = usecases.manual_override(
            body.analysis_id,
            body.target_type,
            body.target_id,
            body.reason,
            body.user_id or f"api-key:{key.key_id}",
            body.after,
        )
        audit.record(
            action="override_created",
            key_id=key.key_id,
            tenant_id=key.tenant_id,
            target_id=body.target_id,
            purpose=body.reason,
            detail={"analysis_id": body.analysis_id, "applied": out.get("applied")},
        )
        return out

    # ------------------------------------------------------------------ #
    # OpenAPI: declare the X-API-Key scheme honestly (Phase 16, F-0558).
    # Enforcement lives in ApiKeyAuth (above); this only DOCUMENTS it so the
    # served schema matches reality: every /v1 operation requires the key,
    # healthz/metrics are the explicit public exceptions.
    # ------------------------------------------------------------------ #
    def _openapi_with_security() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi
        from plot_shared import API_KEY_HEADER

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})[
            "ApiKeyHeader"
        ] = {"type": "apiKey", "in": "header", "name": API_KEY_HEADER}
        schema["security"] = [{"ApiKeyHeader": []}]
        for public in ("/healthz", "/metrics"):
            for operation in schema["paths"].get(public, {}).values():
                if isinstance(operation, dict):
                    operation["security"] = []  # documented public endpoints
        app.openapi_schema = schema
        return schema

    app.openapi = _openapi_with_security  # type: ignore[method-assign]

    return app
