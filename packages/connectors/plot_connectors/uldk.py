"""ULDK (GUGiK) parcel resolver — fully implemented (Phase 6 §6.1.B.1, F-0041).

ULDK is the MVP-critical parcel resolver. This is a real text-API client per
IMPLEMENTATION_PLAN.md Phase 0.3:

* ``GetParcelById`` — ``?request=GetParcelById&id=<teryt-parcel-id>``
* ``GetParcelByXY`` — ``?request=GetParcelByXY&xy=<x>,<y>[,<srid>]`` (default EPSG:2180)
* ``&result=geom_wkt,teryt,parcel,region,commune,county,voivodeship``
* Response is plain text: **line 1 = status code** (``0`` = success, ``-1`` etc. = error),
  **line 2+ = data**; requested result fields are ``|``-separated on the data line.
  We request ``geom_wkt`` so the geometry comes back as WKT (not the default WKB).

The connector normalizes to a Parcel-shaped payload + a SourceRecord + EvidenceItems
(teryt + geometry), runs a cheap healthcheck, and shares the framework's egress guard,
retry, and circuit breaker. Tests use a recorded fixture — no live calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from plot_domain import EvidenceItem, GeometryPrecision, LegalStatus, SourceType

from plot_connectors.base.connector import Connector, SourceProfile
from plot_connectors.base.errors import SourceUnavailable
from plot_connectors.base.models import HealthStatus, NormalizedResult, RawResult, ResultStatus

#: The result fields we request, in order — the data line maps positionally to these.
ULDK_RESULT_FIELDS: tuple[str, ...] = (
    "geom_wkt",
    "teryt",
    "parcel",
    "region",
    "commune",
    "county",
    "voivodeship",
)

#: ULDK separates requested result fields on the data line with a pipe.
_FIELD_SEP = "|"


def uldk_profile(base_url: str = "https://uldk.gugik.gov.pl/") -> SourceProfile:
    """The ULDK source profile (§5: GUGiK official register, binding, cadastral)."""
    return SourceProfile(
        source_id="pl.gugik.uldk",
        publisher="GUGiK",
        base_url=base_url,
        service="text-api",
        # ULDK exposes EGiB cadastral data; cadastral records are authoritative for the
        # parcel identity/geometry (§5 rank 3, official register).
        license="Dane EGiB / GUGiK — see https://uldk.gugik.gov.pl/ regulamin",
        legal_status=LegalStatus.BINDING,
        source_type=SourceType.OFFICIAL_REGISTER,
        geometry_precision=GeometryPrecision.CADASTRAL,
        confidence=0.95,
        version="uldk-1",
        fallback_note=(
            "If ULDK is unavailable, fall back to a local EGiB/SIP service or a "
            "user-supplied wypis/wyrys; never assume a parcel does not exist."
        ),
    )


@dataclass(frozen=True)
class ParcelByIdQuery:
    """Resolve a parcel by its full TERYT parcel id (``GetParcelById``)."""

    parcel_id: str


@dataclass(frozen=True)
class ParcelByXYQuery:
    """Resolve a parcel by a point (``GetParcelByXY``); ``srid`` defaults to 2180."""

    x: float
    y: float
    srid: int = 2180


ULDKQuery = ParcelByIdQuery | ParcelByXYQuery


class ULDKConnector(Connector):
    """ULDK parcel resolver connector (F-0041)."""

    def __init__(self, profile: SourceProfile | None = None, **kwargs: Any) -> None:
        super().__init__(profile or uldk_profile(), **kwargs)

    # --- fetch -------------------------------------------------------------- #
    def _build_params(self, query: ULDKQuery) -> dict[str, str]:
        result = ",".join(ULDK_RESULT_FIELDS)
        if isinstance(query, ParcelByIdQuery):
            return {"request": "GetParcelById", "id": query.parcel_id, "result": result}
        if isinstance(query, ParcelByXYQuery):
            # xy = "x,y,srid"; default srid 2180 per Phase 0.3.
            xy = f"{query.x},{query.y},{query.srid}"
            return {"request": "GetParcelByXY", "xy": xy, "result": result}
        raise TypeError(f"Unsupported ULDK query: {type(query)!r}")

    async def fetch(self, query: ULDKQuery) -> RawResult:
        """Fetch the raw ULDK text response for ``query`` (egress-guarded + resilient)."""
        params = self._build_params(query)
        resp = await self._request(self.profile.base_url, params=params)
        return RawResult(
            source_id=self.profile.source_id,
            url=str(resp.request.url),
            status_code=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", "text/plain"),
            request_params=dict(params),
            fetched_at=datetime.now(UTC).isoformat(),
        )

    # --- normalize ---------------------------------------------------------- #
    def normalize(self, raw: RawResult) -> NormalizedResult:
        """Parse the ULDK text response → Parcel payload + SourceRecord + Evidence.

        Status semantics (NFR-REL-001):

        * status line ``0`` with a data line ⇒ ``ok``
        * status line ``0`` but no data ⇒ ``not_detected`` (source answered, no parcel)
        * any non-zero / unparseable status ⇒ ``not_detected`` is NOT assumed; an empty
          body is a parse warning. (A transport failure never reaches normalize — it is
          raised as ``SourceUnavailable`` in ``fetch``.)
        """
        text = raw.text().replace("\r\n", "\n").strip("\n")
        lines = text.split("\n") if text else []
        retrieved_at = datetime.fromisoformat(raw.fetched_at)
        source_record_id = f"{self.profile.source_id}:{uuid.uuid4().hex[:8]}"

        if not lines:
            record = self._make_source_record(
                source_record_id=source_record_id,
                url=raw.url,
                retrieved_at=retrieved_at,
                notes="ULDK returned an empty body.",
            )
            return NormalizedResult(
                status=ResultStatus.NOT_DETECTED,
                source_record=record,
                warnings=["empty ULDK response body"],
            )

        status_line = lines[0].strip()
        data_lines = [ln for ln in lines[1:] if ln.strip()]

        if status_line != "0":
            # ULDK reports an error status (e.g. "-1 ..."). The source answered, but did
            # not return a parcel — that is not_detected for this query, with the raw
            # status preserved in notes (never silently a "no constraint", §21).
            record = self._make_source_record(
                source_record_id=source_record_id,
                url=raw.url,
                retrieved_at=retrieved_at,
                confidence=0.0,
                notes=f"ULDK status line: {status_line!r}",
            )
            return NormalizedResult(
                status=ResultStatus.NOT_DETECTED,
                source_record=record,
                warnings=[f"ULDK non-zero status: {status_line!r}"],
            )

        if not data_lines:
            record = self._make_source_record(
                source_record_id=source_record_id,
                url=raw.url,
                retrieved_at=retrieved_at,
                notes="ULDK status 0 but no data line.",
            )
            return NormalizedResult(
                status=ResultStatus.NOT_DETECTED,
                source_record=record,
                warnings=["ULDK status 0 with no parcel data"],
            )

        fields = self._parse_data_line(data_lines[0])
        record = self._make_source_record(
            source_record_id=source_record_id,
            url=raw.url,
            retrieved_at=retrieved_at,
            notes=f"ULDK parcel {fields.get('teryt', '?')}.",
        )

        parcel_payload = self._to_parcel_payload(fields, source_id=source_record_id)
        evidence = self._build_evidence(
            fields, source_record_id=source_record_id, parcel_id=parcel_payload["id"]
        )
        return NormalizedResult(
            status=ResultStatus.OK,
            source_record=record,
            evidence=evidence,
            payload={"parcel": parcel_payload, "raw_fields": fields},
            snapshot_uri=raw.request_params.get("__snapshot_uri__"),
        )

    def _parse_data_line(self, line: str) -> dict[str, str]:
        """Map a ``|``-separated data line positionally onto :data:`ULDK_RESULT_FIELDS`."""
        parts = line.split(_FIELD_SEP)
        fields: dict[str, str] = {}
        for name, value in zip(ULDK_RESULT_FIELDS, parts, strict=False):
            fields[name] = value.strip()
        return fields

    def _to_parcel_payload(self, fields: dict[str, str], *, source_id: str) -> dict[str, Any]:
        """Build a Parcel-shaped dict (matches ``plot_domain.Parcel`` fields)."""
        teryt = fields.get("teryt") or fields.get("parcel") or ""
        return {
            "id": f"parcel:{teryt}" if teryt else f"parcel:{uuid.uuid4().hex[:8]}",
            "external_id": teryt or None,
            "teryt": teryt or None,
            "number": fields.get("parcel") or None,
            "geometry_wkt": fields.get("geom_wkt") or None,
            "input_crs": "EPSG:2180",
            "source_id": source_id,
            "administrative_context": {
                "teryt": teryt,
                "commune": fields.get("commune") or None,
                "county": fields.get("county") or None,
                "voivodeship": fields.get("voivodeship") or None,
                "district": fields.get("region") or None,
            },
        }

    def _build_evidence(
        self, fields: dict[str, str], *, source_record_id: str, parcel_id: str
    ) -> list[EvidenceItem]:
        """Emit evidence for the resolved teryt identity and the geometry (§11)."""
        now = datetime.now(UTC)
        evidence: list[EvidenceItem] = []
        teryt = fields.get("teryt")
        if teryt:
            evidence.append(
                EvidenceItem(
                    id=f"ev:{uuid.uuid4().hex[:8]}",
                    analysis_id="",  # populated by the orchestrator (Phase 7)
                    source_id=source_record_id,
                    subject_type="parcel",
                    subject_id=parcel_id,
                    claim="parcel_identity_resolved",
                    value_json={
                        "teryt": teryt,
                        "parcel": fields.get("parcel"),
                        "commune": fields.get("commune"),
                        "county": fields.get("county"),
                        "voivodeship": fields.get("voivodeship"),
                    },
                    confidence=self.profile.confidence,
                    created_at=now,
                )
            )
        geom_wkt = fields.get("geom_wkt")
        if geom_wkt:
            evidence.append(
                EvidenceItem(
                    id=f"ev:{uuid.uuid4().hex[:8]}",
                    analysis_id="",
                    source_id=source_record_id,
                    subject_type="parcel",
                    subject_id=parcel_id,
                    claim="parcel_geometry_wkt",
                    value_json={"crs": "EPSG:2180", "wkt": geom_wkt},
                    confidence=self.profile.confidence,
                    created_at=now,
                )
            )
        return evidence

    # --- resolve convenience ------------------------------------------------ #
    async def resolve(self, query: ULDKQuery, *, snapshot: bool = True) -> NormalizedResult:
        """Fetch + (optionally) snapshot + normalize — the typical entry point."""
        raw = await self.fetch(query)
        snapshot_uri: str | None = None
        if snapshot:
            snapshot_uri = await self.snapshot(raw)
            # carry the snapshot uri through normalize via request_params (audit trail)
            raw = raw.model_copy(
                update={"request_params": {**raw.request_params, "__snapshot_uri__": snapshot_uri}}
            )
        result = self.normalize(raw)
        if snapshot_uri and result.snapshot_uri is None:
            result = result.model_copy(update={"snapshot_uri": snapshot_uri})
        return result

    # --- healthcheck -------------------------------------------------------- #
    async def healthcheck(self) -> HealthStatus:
        """Cheap ULDK liveness probe via a tiny ``GetParcelById`` round-trip.

        Uses a representative parcel id; any data-level error still proves the service is
        *up* (healthy=True). Only a transport/circuit outage is ``source_unavailable``.
        """
        started = datetime.now(UTC)
        probe = ParcelByIdQuery(parcel_id="141201_1.0001.1867/2")
        try:
            await self.fetch(probe)
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
            detail="ULDK reachable",
        )
