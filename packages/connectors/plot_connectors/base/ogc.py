"""OGC GetCapabilities introspection + layer discovery (Phase 6 §6.1.A.6; F-0081/0082).

A thin wrapper over ``owslib`` ``WebMapService`` / ``WebFeatureService`` /
``WebCoverageService``. Capabilities are **injectable**: pass pre-fetched capabilities
XML (``capabilities_xml=...``) and owslib parses it WITHOUT any network call, so tests
use recorded fixtures and never hit the network (verified against owslib 0.35.0 — the
``xml=`` constructor argument feeds the parser directly).

Returns framework-neutral :class:`OGCCapabilities` / :class:`OGCLayer` value objects so
the rest of the connector code never depends on owslib's object shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ServiceKind = Literal["WMS", "WFS", "WCS"]


@dataclass(frozen=True)
class OGCLayer:
    """A discovered OGC layer / feature type / coverage (F-0082)."""

    name: str
    title: str = ""
    crs_options: tuple[str, ...] = ()
    bbox_wgs84: tuple[float, float, float, float] | None = None
    queryable: bool = False


@dataclass(frozen=True)
class OGCCapabilities:
    """Parsed GetCapabilities summary (F-0081)."""

    service: ServiceKind
    title: str
    version: str
    operations: tuple[str, ...]
    layers: dict[str, OGCLayer] = field(default_factory=dict)

    def layer_names(self) -> tuple[str, ...]:
        return tuple(self.layers.keys())

    def find_layer(self, name: str) -> OGCLayer | None:
        return self.layers.get(name)


def _build_owslib_service(
    service: ServiceKind,
    url: str,
    *,
    version: str,
    capabilities_xml: bytes | None,
) -> Any:
    """Construct the owslib service object (capabilities injectable via ``xml=``)."""
    if service == "WMS":
        from owslib.wms import WebMapService

        return WebMapService(url, version=version, xml=capabilities_xml)
    if service == "WFS":
        from owslib.wfs import WebFeatureService

        return WebFeatureService(url, version=version, xml=capabilities_xml)
    if service == "WCS":
        from owslib.wcs import WebCoverageService

        # WCS owslib accepts version=None to autodetect; pass through what we get.
        return WebCoverageService(url, version=version or None, xml=capabilities_xml)
    raise ValueError(f"Unknown OGC service kind: {service!r}")


def _layer_to_value(name: str, layer: Any) -> OGCLayer:
    """Map an owslib content entry to an :class:`OGCLayer` (defensive on attrs)."""
    crs_opts = getattr(layer, "crsOptions", None) or getattr(layer, "crs_list", None) or []
    crs_strings = tuple(str(c) for c in crs_opts)
    bbox = getattr(layer, "boundingBoxWGS84", None)
    bbox_tuple: tuple[float, float, float, float] | None = None
    if bbox and len(bbox) >= 4:
        bbox_tuple = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    queryable = bool(getattr(layer, "queryable", 0))
    return OGCLayer(
        name=name,
        title=str(getattr(layer, "title", "") or ""),
        crs_options=crs_strings,
        bbox_wgs84=bbox_tuple,
        queryable=queryable,
    )


def introspect_capabilities(
    service: ServiceKind,
    url: str,
    *,
    version: str = "",
    capabilities_xml: bytes | None = None,
) -> OGCCapabilities:
    """Parse GetCapabilities and discover layers (F-0081/0082).

    Pass ``capabilities_xml`` to parse recorded fixtures offline (no network). If it is
    omitted, owslib will fetch the live capabilities document from ``url`` — that path is
    only exercised by opt-in ``live`` tests, never the default suite.
    """
    # Sensible default versions per service when the caller does not pin one.
    default_versions: dict[ServiceKind, str] = {"WMS": "1.3.0", "WFS": "2.0.0", "WCS": "2.0.1"}
    svc_version = version or default_versions[service]
    svc = _build_owslib_service(service, url, version=svc_version, capabilities_xml=capabilities_xml)

    contents = dict(getattr(svc, "contents", {}) or {})
    layers = {name: _layer_to_value(name, layer) for name, layer in contents.items()}

    identification = getattr(svc, "identification", None)
    title = str(getattr(identification, "title", "") or "")
    parsed_version = str(getattr(svc, "version", svc_version) or svc_version)
    operations = tuple(str(op.name) for op in getattr(svc, "operations", []) if getattr(op, "name", None))

    return OGCCapabilities(
        service=service,
        title=title,
        version=parsed_version,
        operations=operations,
        layers=layers,
    )
