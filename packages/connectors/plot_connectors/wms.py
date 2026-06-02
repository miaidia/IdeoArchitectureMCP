"""Generic WMS adapter (Phase 6 §6.1.B.2, F-0045/0081/0082/0083).

owslib-backed GetCapabilities introspection (injectable) + layer discovery, then a
bbox-minimized GetMap. WMS returns RENDERED PIXELS — a *preview*, not analytical truth
(§21, Phase 6 §6.4 anti-pattern). So WMS evidence is flagged ``approximate`` precision
and low confidence; analytical geometry must come from WFS / GML / WCS instead. The
GetMap bytes are snapshotted, and the evidence records only that an image was retrieved
(no false vector claims).
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

#: WMS-derived evidence is preview only — low geometric precision (§21).
WMS_GEOMETRY_PRECISION = GeometryPrecision.APPROXIMATE


@dataclass(frozen=True)
class WMSQuery:
    """A bbox-minimized GetMap request (F-0083)."""

    layer: str
    bbox: BBox
    width: int = 512
    height: int = 512
    image_format: str = "image/png"


class WMSConnector(Connector):
    """Generic WMS connector driven by a :class:`SourceProfile` (service='WMS')."""

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
                "WMS", self.profile.base_url, version=self.profile.version, capabilities_xml=xml
            )
        return self._caps

    def discover_layers(self, *, capabilities_xml: bytes | None = None) -> tuple[str, ...]:
        return self.capabilities(capabilities_xml=capabilities_xml).layer_names()

    def _build_params(self, query: WMSQuery) -> dict[str, str]:
        version = self.profile.version or "1.3.0"
        # WMS 1.3.0 uses CRS; 1.1.1 uses SRS.
        crs_key = "crs" if version.startswith("1.3") else "srs"
        return {
            "service": "WMS",
            "version": version,
            "request": "GetMap",
            "layers": query.layer,
            "styles": "",
            crs_key: query.bbox.crs,
            "bbox": query.bbox.to_param(),  # bbox minimization (F-0083)
            "width": str(query.width),
            "height": str(query.height),
            "format": query.image_format,
        }

    async def fetch(self, query: WMSQuery) -> RawResult:
        params = self._build_params(query)
        resp = await self._request(self.profile.base_url, params=params)
        return RawResult(
            source_id=self.profile.source_id,
            url=str(resp.request.url),
            status_code=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", query.image_format),
            request_params={**params, "__layer__": query.layer},
            fetched_at=datetime.now(UTC).isoformat(),
        )

    def normalize(self, raw: RawResult) -> NormalizedResult:
        """Record the preview image as low-precision evidence (NEVER analytical, §21)."""
        retrieved_at = datetime.fromisoformat(raw.fetched_at)
        source_record_id = f"{self.profile.source_id}:{uuid.uuid4().hex[:8]}"
        layer = str(raw.request_params.get("__layer__", ""))
        # Force preview-grade precision on the source record regardless of the profile.
        record = self._make_source_record(
            source_record_id=source_record_id,
            url=raw.url,
            retrieved_at=retrieved_at,
            confidence=min(self.profile.confidence, 0.3),
            notes=f"WMS GetMap preview on layer {layer!r} — PREVIEW pixels, not analytical (§21).",
        )
        record = record.model_copy(update={"geometry_precision": WMS_GEOMETRY_PRECISION})

        is_image = raw.content_type.startswith("image/") and bool(raw.content)
        status = ResultStatus.OK if is_image else ResultStatus.NOT_DETECTED
        evidence = (
            [
                EvidenceItem(
                    id=f"ev:{uuid.uuid4().hex[:8]}",
                    analysis_id="",
                    source_id=source_record_id,
                    subject_type="wms_preview",
                    subject_id=layer,
                    claim="wms_preview_image",
                    value_json={
                        "layer": layer,
                        "content_type": raw.content_type,
                        "bytes": len(raw.content),
                        "preview_only": True,
                        "geometry_precision": WMS_GEOMETRY_PRECISION.value,
                    },
                    confidence=min(self.profile.confidence, 0.3),
                    created_at=datetime.now(UTC),
                )
            ]
            if is_image
            else []
        )
        return NormalizedResult(
            status=status,
            source_record=record,
            evidence=evidence,
            payload={"layer": layer, "preview_only": True},
            warnings=[] if is_image else ["WMS did not return an image"],
        )
