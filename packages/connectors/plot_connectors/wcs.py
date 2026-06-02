"""Generic WCS adapter (Phase 6 §6.1.B.2, F-0048/0051/0052/0081/0082/0083).

owslib-backed GetCapabilities introspection (injectable) + coverage discovery, then a
bbox-minimized GetCoverage. WCS returns raster *data* (e.g. NMT/NMPT elevation), so its
evidence is ``raster_derived`` precision — analytical for terrain, but not a vector. The
coverage bytes are snapshotted and stored via the ArtifactStore; deep raster sampling is
a later phase (this adapter fetches + records).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from plot_domain import EvidenceItem, GeometryPrecision

from plot_connectors.base.connector import Connector, SourceProfile
from plot_connectors.base.models import BBox, NormalizedResult, RawResult, ResultStatus
from plot_connectors.base.ogc import OGCCapabilities, introspect_capabilities

WCS_GEOMETRY_PRECISION = GeometryPrecision.RASTER_DERIVED


@dataclass(frozen=True)
class WCSQuery:
    """A bbox-minimized GetCoverage request (F-0083)."""

    coverage: str
    bbox: BBox
    image_format: str = "image/tiff"


class WCSConnector(Connector):
    """Generic WCS connector driven by a :class:`SourceProfile` (service='WCS')."""

    def __init__(
        self,
        profile: SourceProfile,
        *,
        capabilities_xml: bytes | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(profile, **kwargs)
        self._capabilities_xml = capabilities_xml
        self._caps: OGCCapabilities | None = None

    def capabilities(self, *, capabilities_xml: bytes | None = None) -> OGCCapabilities:
        """Parse + cache GetCapabilities (F-0081/0082); injectable for offline tests."""
        xml = capabilities_xml or self._capabilities_xml
        if self._caps is None or capabilities_xml is not None:
            self._caps = introspect_capabilities(
                "WCS", self.profile.base_url, version=self.profile.version, capabilities_xml=xml
            )
        return self._caps

    def discover_layers(self, *, capabilities_xml: bytes | None = None) -> tuple[str, ...]:
        return self.capabilities(capabilities_xml=capabilities_xml).layer_names()

    def _build_params(self, query: WCSQuery) -> dict[str, str]:
        version = self.profile.version or "2.0.1"
        return {
            "service": "WCS",
            "version": version,
            "request": "GetCoverage",
            "coverageId": query.coverage,
            "format": query.image_format,
            "subsettingCrs": query.bbox.crs,
            # bbox minimization (F-0083), expressed as axis subsets for WCS 2.0.x.
            "subset": f"x({query.bbox.minx},{query.bbox.maxx})",
            "subsetY": f"y({query.bbox.miny},{query.bbox.maxy})",
        }

    async def fetch(self, query: WCSQuery) -> RawResult:
        params = self._build_params(query)
        resp = await self._request(self.profile.base_url, params=params)
        return RawResult(
            source_id=self.profile.source_id,
            url=str(resp.request.url),
            status_code=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", query.image_format),
            request_params={**params, "__coverage__": query.coverage},
            fetched_at=datetime.now(UTC).isoformat(),
        )

    def normalize(self, raw: RawResult) -> NormalizedResult:
        """Record the retrieved coverage as raster-derived evidence (§5 precision)."""
        retrieved_at = datetime.fromisoformat(raw.fetched_at)
        source_record_id = f"{self.profile.source_id}:{uuid.uuid4().hex[:8]}"
        coverage = str(raw.request_params.get("__coverage__", ""))
        record = self._make_source_record(
            source_record_id=source_record_id,
            url=raw.url,
            retrieved_at=retrieved_at,
            notes=f"WCS GetCoverage on {coverage!r} (raster).",
        )
        record = record.model_copy(update={"geometry_precision": WCS_GEOMETRY_PRECISION})

        has_data = bool(raw.content)
        status = ResultStatus.OK if has_data else ResultStatus.NOT_DETECTED
        evidence = (
            [
                EvidenceItem(
                    id=f"ev:{uuid.uuid4().hex[:8]}",
                    analysis_id="",
                    source_id=source_record_id,
                    subject_type="wcs_coverage",
                    subject_id=coverage,
                    claim="wcs_coverage_raster",
                    value_json={
                        "coverage": coverage,
                        "content_type": raw.content_type,
                        "bytes": len(raw.content),
                        "geometry_precision": WCS_GEOMETRY_PRECISION.value,
                    },
                    confidence=self.profile.confidence,
                    created_at=datetime.now(UTC),
                )
            ]
            if has_data
            else []
        )
        return NormalizedResult(
            status=status,
            source_record=record,
            evidence=evidence,
            payload={"coverage": coverage},
            warnings=[] if has_data else ["WCS returned an empty coverage"],
        )
