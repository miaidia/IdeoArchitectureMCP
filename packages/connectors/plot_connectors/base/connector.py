"""The connector adapter contract + the pluggable source-profile (Phase 6 §6.1.A.1/B).

:class:`SourceProfile` is the config that turns a generic adapter into a concrete source
(id, publisher, base_url, license, ``legal_status`` per §5, layer/dataset names, timeout
& retry budget). :class:`Connector` is the ABC implementing the §20.3 contract:

* ``async fetch(query) -> RawResult`` — egress-guarded, resilient (retry + breaker) HTTP.
* ``normalize(raw) -> NormalizedResult`` — emits a SourceRecord + EvidenceItems (§5, §11).
* ``async snapshot(raw) -> uri`` — stores the raw blob via the ArtifactStore (§20.13).
* ``async healthcheck() -> HealthStatus`` — source liveness + circuit state (NFR-REL-005).

This module never imports ``plot_rules`` (§9.4).
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import httpx
from plot_domain import GeometryPrecision, LegalStatus, SourceType
from plot_reports import ArtifactStore, LocalArtifactStore
from pydantic import BaseModel, ConfigDict, Field

from plot_connectors.base.cache import Cache, InMemoryCache
from plot_connectors.base.egress import EgressAllowlist, default_allowlist
from plot_connectors.base.models import HealthStatus, NormalizedResult, RawResult, ResultStatus
from plot_connectors.base.resilience import (
    ResilienceConfig,
    build_async_client,
    guarded_call,
    make_breaker,
    make_retryer,
)


class SourceProfile(BaseModel):
    """Pluggable configuration for one source (Phase 6 §6.1.B, §5 source schema).

    Carries everything the generic adapters need to behave like a concrete connector
    without bespoke code: identity, endpoints, layer/dataset names, the source's
    ``legal_status`` / ``license`` / ``source_type`` (for the emitted SourceRecord), the
    default geometry precision, a fallback note, and the resilience budget.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(description="Stable source id (e.g. 'pl.gugik.uldk').")
    publisher: str = Field(description="Publishing authority (e.g. 'GUGiK').")
    base_url: str = Field(description="Base endpoint URL (must be allowlisted, §6).")
    service: str = Field(description="Adapter kind: 'text-api' | 'WMS' | 'WFS' | 'WCS' | 'GML'.")
    license: str = Field(default="unknown", description="License string or 'unknown' (F-0092).")
    legal_status: LegalStatus = Field(
        default=LegalStatus.INFORMATIVE,
        description="binding | informative | auxiliary | unknown (§5 ranking).",
    )
    source_type: SourceType = Field(
        default=SourceType.OFFICIAL_REGISTER, description="§5 source_type."
    )
    geometry_precision: GeometryPrecision = Field(
        default=GeometryPrecision.UNKNOWN,
        description="Default geometry precision class for this source's data (§5).",
    )
    confidence: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Baseline source confidence (§5)."
    )
    layers: tuple[str, ...] = Field(
        default=(), description="Layer / feature-type / coverage / dataset names (F-0082)."
    )
    version: str = Field(default="", description="OGC service version or source version.")
    fallback_note: str = Field(
        default="", description="What to do when this source is unavailable (§28 fallback doc)."
    )
    resilience: ResilienceConfig = Field(
        default_factory=ResilienceConfig, description="Timeout / retry / breaker budget."
    )


class Connector(ABC):
    """Adapter base implementing the §20.3 contract for every connector.

    Subclasses implement :meth:`fetch` and :meth:`normalize`. The base provides the
    egress-guarded resilient HTTP path (:meth:`_request`), snapshotting, source-record
    construction, and a default healthcheck.
    """

    def __init__(
        self,
        profile: SourceProfile,
        *,
        allowlist: EgressAllowlist | None = None,
        cache: Cache | None = None,
        artifact_store: ArtifactStore | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.profile = profile
        self._allowlist = allowlist or default_allowlist()
        self._cache = cache or InMemoryCache()
        self._artifacts = artifact_store or LocalArtifactStore()
        self._client = client  # injectable for tests (respx transport)
        self._breaker = make_breaker(profile.resilience, name=profile.source_id)
        self._retryer = make_retryer(profile.resilience)

    # --- properties --------------------------------------------------------- #
    @property
    def source_id(self) -> str:
        return self.profile.source_id

    @property
    def allowlist(self) -> EgressAllowlist:
        return self._allowlist

    @property
    def cache(self) -> Cache:
        return self._cache

    @property
    def circuit_state(self) -> str:
        return self._breaker.current_state

    # --- shared HTTP path --------------------------------------------------- #
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = build_async_client(self.profile.resilience)
        return self._client

    async def _request(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        method: str = "GET",
    ) -> httpx.Response:
        """Egress-guarded, resilient single request (retry + breaker, redirect-safe).

        The egress guard runs BEFORE any socket is opened (F-0488/0489); redirects are
        validated against the allowlist before being followed (the client does not
        auto-follow). Transient failures / 5xx surface as ``SourceUnavailable`` via
        :func:`guarded_call`; ``EgressBlocked`` propagates unchanged.
        """
        self._allowlist.check_url(url)
        client = self._get_client()

        async def _do() -> httpx.Response:
            resp = await client.request(method, url, params=params)
            # Manually validate + follow redirects so off-allowlist hops are blocked.
            hops = 0
            while resp.is_redirect and hops < 5:
                location = resp.headers.get("location", "")
                target = str(resp.next_request.url) if resp.next_request else location
                self._allowlist.check_redirect(target)
                resp = await client.request(method, target, params=None)
                hops += 1
            resp.raise_for_status()
            return resp

        return await guarded_call(
            self._breaker, self._retryer, _do, source_id=self.source_id
        )

    # --- snapshot / source-record helpers ----------------------------------- #
    async def snapshot(self, raw: RawResult) -> str:
        """Store the raw blob via the ArtifactStore and return its URI (§20.13/14).

        The key is content-addressed under ``snapshots/<source_id>/<sha256>`` so the same
        bytes are stored once and a historical report can be rebuilt from it (NFR-REL-004).
        """
        digest = hashlib.sha256(raw.content).hexdigest()
        key = f"snapshots/{self.profile.source_id}/{digest}"
        return self._artifacts.put(key, raw.content, raw.content_type)

    def _make_source_record(
        self,
        *,
        source_record_id: str,
        url: str,
        retrieved_at: datetime | None = None,
        confidence: float | None = None,
        notes: str = "",
    ):
        """Construct a :class:`~plot_domain.SourceRecord` from the profile (§5).

        Imported lazily-by-reference: returns a fully-populated SourceRecord using the
        profile's publisher / license / legal_status / geometry_precision so every fetch
        records a complete §5 source record.
        """
        from plot_domain import Freshness, SourceRecord

        return SourceRecord(
            source_id=source_record_id,
            source_type=self.profile.source_type,
            publisher=self.profile.publisher,
            url_or_origin=url,
            retrieved_at=retrieved_at or datetime.now(UTC),
            valid_from=None,
            valid_to=None,
            license=self.profile.license,
            legal_status=self.profile.legal_status,
            geometry_precision=self.profile.geometry_precision,
            freshness=Freshness.CURRENT,
            confidence=confidence if confidence is not None else self.profile.confidence,
            notes=notes,
        )

    # --- contract ----------------------------------------------------------- #
    @abstractmethod
    async def fetch(self, query: Any) -> RawResult:
        """Fetch raw data for ``query`` (§20.3)."""

    @abstractmethod
    def normalize(self, raw: RawResult) -> NormalizedResult:
        """Normalize a :class:`RawResult` to a SourceRecord + EvidenceItems (§20.3)."""

    async def healthcheck(self) -> HealthStatus:
        """Default healthcheck: a guarded GET of the base URL (NFR-REL-005, §28).

        Subclasses with a cheaper liveness probe (e.g. ULDK's tiny status query) may
        override. Any source-unavailable outcome is reported as ``healthy=False`` with
        ``ResultStatus.SOURCE_UNAVAILABLE`` — it never masquerades as ``ok``.
        """
        from plot_connectors.base.errors import SourceUnavailable

        started = datetime.now(UTC)
        try:
            await self._request(self.profile.base_url)
        except SourceUnavailable as exc:
            return HealthStatus(
                source_id=self.source_id,
                healthy=False,
                status=ResultStatus.SOURCE_UNAVAILABLE,
                circuit_state=self.circuit_state,
                detail=str(exc),
            )
        latency_ms = (datetime.now(UTC) - started).total_seconds() * 1000.0
        return HealthStatus(
            source_id=self.source_id,
            healthy=True,
            status=ResultStatus.OK,
            latency_ms=latency_ms,
            circuit_state=self.circuit_state,
            detail="ok",
        )
