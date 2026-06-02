"""Generic WFS adapter (Phase 6 §6.1.B.2, F-0047/0081/0082/0083).

owslib-backed: GetCapabilities introspection (capabilities injectable for offline
tests) + layer discovery, then a bbox-minimized GetFeature normalized to a SourceRecord
+ EvidenceItems. WFS returns analytical vectors, so its evidence is high-precision
(``geometry_precision`` from the profile, typically topographic/cadastral) — unlike WMS.

The GetFeature response is parsed as GeoJSON (``outputFormat=application/json``); each
returned feature becomes one EvidenceItem carrying its geometry (§11, §21: vectors are
analytical truth, not preview pixels).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from plot_domain import EvidenceItem

from plot_connectors.base.connector import Connector, SourceProfile
from plot_connectors.base.models import BBox, NormalizedResult, RawResult, ResultStatus
from plot_connectors.base.ogc import OGCCapabilities, introspect_capabilities


@dataclass(frozen=True)
class WFSQuery:
    """A bbox-minimized GetFeature query against one feature type (F-0083)."""

    layer: str
    bbox: BBox
    max_features: int = 100


class WFSConnector(Connector):
    """Generic WFS connector driven by a :class:`SourceProfile` (service='WFS')."""

    def __init__(
        self,
        profile: SourceProfile,
        *,
        capabilities_xml: bytes | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(profile, **kwargs)
        # Pre-fetched capabilities (recorded fixture in tests) → no network on init.
        self._capabilities_xml = capabilities_xml
        self._caps: OGCCapabilities | None = None

    # --- introspection ------------------------------------------------------ #
    def capabilities(self, *, capabilities_xml: bytes | None = None) -> OGCCapabilities:
        """Parse + cache GetCapabilities; layer discovery (F-0081/0082).

        ``capabilities_xml`` (or the constructor-supplied one) is parsed offline by
        owslib. Passing neither triggers a live owslib fetch (opt-in ``live`` tests only).
        """
        xml = capabilities_xml or self._capabilities_xml
        if self._caps is None or capabilities_xml is not None:
            self._caps = introspect_capabilities(
                "WFS", self.profile.base_url, version=self.profile.version, capabilities_xml=xml
            )
        return self._caps

    def discover_layers(self, *, capabilities_xml: bytes | None = None) -> tuple[str, ...]:
        """Return discovered feature-type names (F-0082)."""
        return self.capabilities(capabilities_xml=capabilities_xml).layer_names()

    # --- fetch -------------------------------------------------------------- #
    def _build_params(self, query: WFSQuery) -> dict[str, str]:
        version = self.profile.version or "2.0.0"
        # WFS 2.0.0 uses TYPENAMES / COUNT; 1.x uses TYPENAME / MAXFEATURES.
        is_v2 = version.startswith("2")
        params: dict[str, str] = {
            "service": "WFS",
            "version": version,
            "request": "GetFeature",
            "outputFormat": "application/json",
            "srsName": query.bbox.crs,
            # bbox minimization (F-0083): minx,miny,maxx,maxy,crs
            "bbox": f"{query.bbox.to_param()},{query.bbox.crs}",
        }
        if is_v2:
            params["typeNames"] = query.layer
            params["count"] = str(query.max_features)
        else:
            params["typeName"] = query.layer
            params["maxFeatures"] = str(query.max_features)
        return params

    async def fetch(self, query: WFSQuery) -> RawResult:
        params = self._build_params(query)
        resp = await self._request(self.profile.base_url, params=params)
        return RawResult(
            source_id=self.profile.source_id,
            url=str(resp.request.url),
            status_code=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", "application/json"),
            request_params={**params, "__layer__": query.layer},
            fetched_at=datetime.now(UTC).isoformat(),
        )

    # --- normalize ---------------------------------------------------------- #
    def normalize(self, raw: RawResult) -> NormalizedResult:
        """Parse a GeoJSON FeatureCollection → EvidenceItems (one per feature).

        Status: features present ⇒ ``ok``; a valid but empty FeatureCollection ⇒
        ``not_detected`` (the source answered, nothing in the bbox — NOT a guarantee of
        absence, §21). Unparseable JSON ⇒ ``manual_review_required``.
        """
        retrieved_at = datetime.fromisoformat(raw.fetched_at)
        source_record_id = f"{self.profile.source_id}:{uuid.uuid4().hex[:8]}"
        layer = str(raw.request_params.get("__layer__", ""))
        record = self._make_source_record(
            source_record_id=source_record_id,
            url=raw.url,
            retrieved_at=retrieved_at,
            notes=f"WFS GetFeature on layer {layer!r}.",
        )

        try:
            doc = json.loads(raw.text())
        except (ValueError, UnicodeDecodeError) as exc:
            return NormalizedResult(
                status=ResultStatus.MANUAL_REVIEW_REQUIRED,
                source_record=record,
                warnings=[f"WFS response is not valid GeoJSON: {exc!r}"],
            )

        features = doc.get("features", []) if isinstance(doc, dict) else []
        if not features:
            return NormalizedResult(
                status=ResultStatus.NOT_DETECTED,
                source_record=record,
                payload={"layer": layer, "feature_count": 0},
            )

        now = datetime.now(UTC)
        evidence: list[EvidenceItem] = []
        for i, feat in enumerate(features):
            geom = feat.get("geometry") if isinstance(feat, dict) else None
            props = feat.get("properties", {}) if isinstance(feat, dict) else {}
            evidence.append(
                EvidenceItem(
                    id=f"ev:{uuid.uuid4().hex[:8]}",
                    analysis_id="",
                    source_id=source_record_id,
                    subject_type="wfs_feature",
                    subject_id=f"{layer}#{i}",
                    claim="wfs_feature",
                    value_json={"layer": layer, "properties": props},
                    confidence=self.profile.confidence,
                    geometry=geom,
                    created_at=now,
                )
            )
        return NormalizedResult(
            status=ResultStatus.OK,
            source_record=record,
            evidence=evidence,
            payload={"layer": layer, "feature_count": len(features)},
        )
