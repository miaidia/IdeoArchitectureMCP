"""Python SDK for the Plot Analyzer HTTP API (Phase 14B; F-0464).

:class:`PlotAnalyzerClient` is a THIN, hand-written (generated-free) wrapper over
the §27 HTTP endpoints — every method maps 1:1 onto one endpoint and returns the
parsed JSON body. There is deliberately NO client-side logic (no retries beyond
httpx defaults, no result reshaping): the API and MCP surfaces share the same
domain use-cases, and the SDK must not become a third place where logic lives.

A custom ``httpx.Client`` (e.g. one built on ``httpx.MockTransport`` or an ASGI
transport) can be injected for tests — the CLI and the test-suite use exactly
that, so no subprocess/socket is needed.

TypeScript SDK (F-0465): NOT provided — documented gap. The OpenAPI schema is
served at ``/openapi.json``, so a TS client can be generated from it at
deployment time; shipping and maintaining one is out of scope here.
"""

from __future__ import annotations

from typing import Any

import httpx

#: Header carrying the API key (F-0479; see ``plot_shared.Settings.api_keys``).
API_KEY_HEADER = "X-API-Key"

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_S = 60.0


class PlotAnalyzerClient:
    """Thin client for the Plot Analyzer HTTP API (§27 endpoints).

    Parameters
    ----------
    base_url:
        API root (the FastAPI app root — e.g. ``http://host:8000`` or the
        combined-process mount ``http://host:8000/api``).
    api_key:
        Value for the ``X-API-Key`` header. Optional only for ``/healthz``.
    client:
        Injected ``httpx.Client`` (tests: MockTransport/ASGITransport). When
        provided, ``base_url``/headers are still applied per request.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.Client(timeout=timeout_s)

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        return {API_KEY_HEADER: self.api_key} if self.api_key else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._client.request(
            method, f"{self.base_url}{path}", headers=self._headers(), **kwargs
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):  # defensive: every endpoint returns an object
            return {"value": body}
        return body

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PlotAnalyzerClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # §27 endpoints (1:1, no logic)
    # ------------------------------------------------------------------ #
    def healthz(self) -> dict[str, Any]:
        return self._request("GET", "/healthz")

    def resolve_parcel(
        self, parcel_id: str | None = None, point: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/parcels/resolve", json={"parcel_id": parcel_id, "point": point}
        )

    def analyze(
        self,
        input: dict[str, Any],
        analysis_mode: str = "quick_screening",
        investment_goal: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/analyses",
            json={
                "input": input,
                "analysis_mode": analysis_mode,
                "investment_goal": investment_goal or {},
                "options": options or {},
            },
        )

    def get_analysis(self, analysis_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/analyses/{analysis_id}")

    def get_status(self, analysis_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/analyses/{analysis_id}/status")

    def get_result(self, analysis_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/analyses/{analysis_id}/result")

    def get_risks(self, analysis_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/analyses/{analysis_id}/risks")

    def get_unknowns(self, analysis_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/analyses/{analysis_id}/unknowns")

    def get_evidence(self, analysis_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/analyses/{analysis_id}/evidence")

    def get_buildable_envelope(self, analysis_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/analyses/{analysis_id}/buildable-envelope")

    def report(
        self,
        analysis_id: str,
        format: str = "md",
        audience: str = "architect",
        variant_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"format": format, "audience": audience}
        if variant_id:
            params["variant_id"] = variant_id
        return self._request("GET", f"/v1/analyses/{analysis_id}/report", params=params)

    def export(
        self, analysis_id: str, format: str = "geojson", variant_id: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, str] = {"format": format}
        if variant_id:
            params["variant_id"] = variant_id
        return self._request("GET", f"/v1/analyses/{analysis_id}/export", params=params)

    def ingest_document(
        self,
        filename: str,
        content_base64: str,
        purpose: str,
        content_type: str | None = None,
        analysis_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/documents/ingest",
            json={
                "filename": filename,
                "content_base64": content_base64,
                "content_type": content_type,
                "purpose": purpose,
                "analysis_id": analysis_id,
            },
        )

    def portfolio_analyze(self, parcels: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/v1/portfolio/analyze", json={"parcels": parcels})

    def monitoring_create(
        self,
        scope: str,
        target_id: str,
        purpose: str,
        interval_hours: float | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/monitoring",
            json={
                "scope": scope,
                "target_id": target_id,
                "purpose": purpose,
                "interval_hours": interval_hours,
                "webhook_url": webhook_url,
            },
        )

    def sources_health(self, source_id: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if source_id:
            params["source_id"] = source_id
        return self._request("GET", "/v1/sources/health", params=params)

    def cache_warm(self, scope: str, target_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/cache/warm", json={"scope": scope, "target_id": target_id}
        )

    def rulesets(self, version: str | None = None) -> dict[str, Any]:
        path = f"/v1/rulesets/{version}" if version else "/v1/rulesets"
        return self._request("GET", path)

    def create_override(
        self,
        analysis_id: str,
        target_type: str,
        target_id: str,
        reason: str,
        after: dict[str, Any],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/overrides",
            json={
                "analysis_id": analysis_id,
                "target_type": target_type,
                "target_id": target_id,
                "reason": reason,
                "after": after,
                "user_id": user_id,
            },
        )
