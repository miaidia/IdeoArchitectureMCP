"""Framework value objects for the connector adapter contract (Phase 6 §6.1.A).

* :class:`ResultStatus` — explicit status semantics (NFR-REL-001): a timeout / 5xx /
  open circuit yields ``source_unavailable``; an empty-but-successful response yields
  ``not_detected`` / ``confirmed_absent`` — the two are NEVER conflated (§21).
* :class:`RawResult` — the un-normalized payload returned by ``fetch`` (what gets
  snapshotted, §20.13).
* :class:`NormalizedResult` — the canonical output of ``normalize``: a
  :class:`~plot_domain.SourceRecord` + zero-or-more :class:`~plot_domain.EvidenceItem`
  plus the status and an optional parsed payload.
* :class:`HealthStatus` — output of ``healthcheck`` (NFR-REL-005).
* :class:`BBox` — a minimal bbox in the analytical CRS (EPSG:2180), used for bbox
  minimization (F-0083) and as part of the cache key (F-0085).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from plot_domain import EvidenceItem, SourceRecord
from pydantic import BaseModel, ConfigDict, Field


class ResultStatus(str, Enum):
    """Status of a connector result — keeps the spec distinctions (NFR-REL-001, §2.1).

    * ``ok`` — the source answered and data was found.
    * ``not_detected`` — the source answered, no matching feature in the queried extent
      (absence of evidence, not evidence of absence).
    * ``confirmed_absent`` — the source authoritatively confirms there is nothing here.
    * ``source_unavailable`` — no usable answer (timeout / 5xx / circuit open). NEVER a
      false "no constraint" (NFR-REL-001).
    * ``manual_review_required`` — answered, but the result needs a human (§20.12).
    """

    OK = "ok"
    NOT_DETECTED = "not_detected"
    CONFIRMED_ABSENT = "confirmed_absent"
    SOURCE_UNAVAILABLE = "source_unavailable"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class BBox(BaseModel):
    """An axis-aligned bounding box ``(minx, miny, maxx, maxy)`` in a given CRS.

    Defaults to the analytical CRS EPSG:2180 (PUWG1992); WMS/WFS/WCS requests are
    minimized to this extent (F-0083).
    """

    model_config = ConfigDict(frozen=True)

    minx: float = Field(description="Minimum easting / x.")
    miny: float = Field(description="Minimum northing / y.")
    maxx: float = Field(description="Maximum easting / x.")
    maxy: float = Field(description="Maximum northing / y.")
    crs: str = Field(default="EPSG:2180", description="CRS of the bbox (default EPSG:2180).")

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return ``(minx, miny, maxx, maxy)``."""
        return (self.minx, self.miny, self.maxx, self.maxy)

    def to_param(self) -> str:
        """Render as a comma-separated ``minx,miny,maxx,maxy`` OGC bbox parameter."""
        return f"{self.minx},{self.miny},{self.maxx},{self.maxy}"


class RawResult(BaseModel):
    """The raw, un-normalized result of a ``fetch`` (snapshotted verbatim, §20.13).

    ``content`` holds the response body bytes so the snapshot is byte-exact and the
    connector can work offline from it later (§20.14).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_id: str = Field(description="Source profile id that produced this result.")
    url: str = Field(description="Final request URL (after redirects).")
    status_code: int | None = Field(
        default=None, description="HTTP status code, or null for non-HTTP fetches."
    )
    content: bytes = Field(description="Raw response body bytes (snapshotted verbatim).")
    content_type: str = Field(
        default="application/octet-stream", description="MIME type of the content."
    )
    request_params: dict[str, Any] = Field(
        default_factory=dict, description="Effective request parameters (for the audit trail)."
    )
    fetched_at: str = Field(description="ISO-8601 timestamp of the fetch.")

    def text(self, encoding: str = "utf-8") -> str:
        """Decode the raw content as text."""
        return self.content.decode(encoding)


class NormalizedResult(BaseModel):
    """Canonical normalized output of ``normalize`` (§20.3, §28).

    Always carries exactly one :class:`SourceRecord` (every fetch records a source,
    §5) and the evidence it produced. ``payload`` is the connector-specific parsed
    object (e.g. a ``Parcel`` dict), kept loose so the framework stays generic.
    """

    status: ResultStatus = Field(description="Result status (NFR-REL-001).")
    source_record: SourceRecord = Field(description="The source record for this fetch (§5).")
    evidence: list[EvidenceItem] = Field(
        default_factory=list, description="Evidence items emitted (§11, NFR-AUD-001)."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Connector-specific normalized payload."
    )
    snapshot_uri: str | None = Field(
        default=None, description="URI of the stored raw snapshot, if snapshotted."
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal normalization warnings."
    )


class HealthStatus(BaseModel):
    """Result of a connector ``healthcheck`` (NFR-REL-005, §28)."""

    source_id: str = Field(description="Source profile id.")
    healthy: bool = Field(description="Whether the source answered the healthcheck.")
    status: ResultStatus = Field(description="Mapped result status (ok / source_unavailable).")
    latency_ms: float | None = Field(
        default=None, description="Round-trip latency in milliseconds, if measured."
    )
    circuit_state: str = Field(
        default="closed", description="Circuit-breaker state: closed | open | half-open."
    )
    detail: str = Field(default="", description="Human-readable diagnostic detail.")
