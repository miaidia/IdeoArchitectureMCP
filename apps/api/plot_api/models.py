"""Request models for the HTTP API (Phase 14B; §27).

Response payloads come straight from the shared use-cases (which return
``plot_domain`` model dumps) — the API adds no response shaping, so there is no
parallel response-model hierarchy to drift (v1 Phase 12 §12.4 anti-pattern).
``POST /v1/analyses`` reuses :class:`plot_domain.AnalysisInput` directly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    """``POST /v1/parcels/resolve`` body (id OR point — same as the MCP tool)."""

    parcel_id: str | None = None
    point: dict[str, Any] | None = Field(
        default=None, description="{x, y, crs} point (EPSG:2180 default)."
    )


class PortfolioRequest(BaseModel):
    """``POST /v1/portfolio/analyze`` body."""

    parcels: list[dict[str, Any]] = Field(default_factory=list)


class MonitoringRequest(BaseModel):
    """``POST /v1/monitoring`` body (§4.5; purpose = explicit write intent, §16)."""

    scope: str
    target_id: str
    purpose: str
    interval_hours: float | None = None
    webhook_url: str | None = None


class CacheWarmRequest(BaseModel):
    """``POST /v1/cache/warm`` body."""

    scope: str
    target_id: str | None = None


class OverrideRequest(BaseModel):
    """``POST /v1/overrides`` body (F-0137/0138; admin-only write)."""

    analysis_id: str
    target_type: str
    target_id: str
    reason: str
    after: dict[str, Any]
    user_id: str | None = Field(
        default=None,
        description="Override author for the audit trail; defaults to the API key id.",
    )


class DocumentIngestRequest(BaseModel):
    """``POST /v1/documents/ingest`` body (upload sandbox, F-0491–0493).

    Content travels base64-encoded in JSON (no multipart dependency); the
    decoded size is checked against the configured cap (F-0493) and the
    base64 text itself is pre-checked so an oversized body is rejected
    before decoding work.
    """

    filename: str
    content_base64: str
    content_type: str | None = None
    purpose: str
    analysis_id: str | None = None


class ReportRequest(BaseModel):
    """``POST /v1/analyses/{id}/reports`` body (§27 form of the report endpoint)."""

    format: str = "md"
    audience: str = "architect"
    variant_id: str | None = None
