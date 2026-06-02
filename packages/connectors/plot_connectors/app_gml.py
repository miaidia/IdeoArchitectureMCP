"""APP/GML fetch + snapshot + record adapter (Phase 6 §6.1.B.3, F-0057/0086).

APP (Akt Planowania Przestrzennego) is published as GML by the commune / register. Deep
GML parsing of zones + indicators is **Phase 8**; Phase 6 only *fetches*, *snapshots*,
and *records* the document so the rest of the pipeline has a binding source artifact to
work from. The emitted result is ``manual_review_required`` — the document is present but
not yet interpreted — so it can never be mistaken for "no plan / no constraint" (§21).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from plot_domain import EvidenceItem

from plot_connectors.base.connector import Connector
from plot_connectors.base.models import NormalizedResult, RawResult, ResultStatus


@dataclass(frozen=True)
class AppGmlQuery:
    """Fetch an APP/GML document by its URL (must be allowlisted)."""

    url: str


class AppGmlConnector(Connector):
    """APP/GML fetch+snapshot+record adapter (service='GML'). No deep parse (Phase 8)."""

    async def fetch(self, query: AppGmlQuery) -> RawResult:
        resp = await self._request(query.url)
        return RawResult(
            source_id=self.profile.source_id,
            url=str(resp.request.url),
            status_code=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", "application/gml+xml"),
            request_params={"url": query.url},
            fetched_at=datetime.now(UTC).isoformat(),
        )

    def normalize(self, raw: RawResult) -> NormalizedResult:
        """Record the fetched GML as a binding source pending parse (Phase 8).

        Status is ``manual_review_required``: a planning act exists and was retrieved, but
        its zones/indicators are not interpreted yet — surfacing it as a normal status
        (§20.12), never as ``not_detected``.
        """
        retrieved_at = datetime.fromisoformat(raw.fetched_at)
        source_record_id = f"{self.profile.source_id}:{uuid.uuid4().hex[:8]}"
        record = self._make_source_record(
            source_record_id=source_record_id,
            url=raw.url,
            retrieved_at=retrieved_at,
            notes="APP/GML fetched + snapshotted; deep parse deferred to Phase 8.",
        )
        has_doc = bool(raw.content)
        evidence: list[EvidenceItem] = []
        if has_doc:
            evidence.append(
                EvidenceItem(
                    id=f"ev:{uuid.uuid4().hex[:8]}",
                    analysis_id="",
                    source_id=source_record_id,
                    subject_type="planning_act",
                    subject_id=raw.url,
                    claim="app_gml_document_retrieved",
                    value_json={
                        "content_type": raw.content_type,
                        "bytes": len(raw.content),
                        "parsed": False,
                        "snapshot_uri": raw.request_params.get("__snapshot_uri__"),
                    },
                    confidence=self.profile.confidence,
                    created_at=datetime.now(UTC),
                )
            )
        return NormalizedResult(
            status=ResultStatus.MANUAL_REVIEW_REQUIRED if has_doc else ResultStatus.NOT_DETECTED,
            source_record=record,
            evidence=evidence,
            payload={"parsed": False, "phase": "fetch-and-record (Phase 6)"},
            warnings=[] if has_doc else ["APP/GML document was empty"],
        )

    async def fetch_record(self, query: AppGmlQuery) -> NormalizedResult:
        """Fetch + snapshot + normalize the APP/GML document (the typical entry point)."""
        raw = await self.fetch(query)
        snapshot_uri = await self.snapshot(raw)
        raw = raw.model_copy(
            update={"request_params": {**raw.request_params, "__snapshot_uri__": snapshot_uri}}
        )
        result = self.normalize(raw)
        return result.model_copy(update={"snapshot_uri": snapshot_uri})

    def _supported_query(self, query: Any) -> AppGmlQuery:
        if isinstance(query, AppGmlQuery):
            return query
        raise TypeError(f"Unsupported APP/GML query: {type(query)!r}")
