"""plot_reports — deterministic map render + artifact storage + web screenshots.

Phase 3 surface (IMPLEMENTATION_PLAN.md §3.1):

* :class:`Layer` / :class:`LayerRole` / :class:`MapRenderer` / :func:`render_map`
  / :class:`RenderResult` — deterministic matplotlib (Agg) map render (§3.1.1, §3.4).
* :class:`ArtifactStore` / :class:`LocalArtifactStore` / :class:`S3ArtifactStore`
  / :func:`get_artifact_store` — PNG/SVG bytes + ``.style.json`` sidecar (§3.1.2).
* :func:`render_preview` / :func:`preview_png_bytes` — renderer-backed sample preview
  used by the MCP image-content path (§3.1.3).
* :func:`screenshot_web_map` / :class:`BrowserUnavailableError` — Playwright headless
  web-map screenshot, best-effort with graceful SKIP (§3.1.4).

This package depends only on ``plot_domain`` + ``plot_shared`` and MUST NOT import
``plot_connectors`` or ``plot_rules`` (Phase 3 §3.4 decoupling).
"""

from plot_reports.artifacts import (
    ArtifactStore,
    LocalArtifactStore,
    S3ArtifactStore,
    get_artifact_store,
)
from plot_reports.preview import (
    preview_png_bytes,
    preview_style_metadata,
    render_preview,
    sample_preview_layers,
)
from plot_reports.render import (
    MASTERPLAN_RENDERER_VERSION,
    RENDERER_VERSION,
    Layer,
    LayerRole,
    MapAnnotation,
    MapRenderer,
    MasterplanRenderer,
    RenderResult,
    masterplan_layers,
    render_map,
    render_masterplan,
)
from plot_reports.report import (
    headline_numbers,
    render_envelope_map,
    render_json,
    render_markdown,
)
from plot_reports.screenshot import (
    BrowserUnavailableError,
    leaflet_preview_html,
    screenshot_web_map,
)

__version__ = "0.1.0"

__all__ = [
    # render
    "Layer",
    "LayerRole",
    "MapAnnotation",
    "MapRenderer",
    "MasterplanRenderer",
    "RenderResult",
    "masterplan_layers",
    "render_map",
    "render_masterplan",
    "RENDERER_VERSION",
    "MASTERPLAN_RENDERER_VERSION",
    # report (Phase 7 §7.1.5 / §22)
    "render_markdown",
    "render_json",
    "render_envelope_map",
    "headline_numbers",
    # artifacts
    "ArtifactStore",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "get_artifact_store",
    # preview (MCP image path)
    "render_preview",
    "preview_png_bytes",
    "preview_style_metadata",
    "sample_preview_layers",
    # web screenshot
    "screenshot_web_map",
    "leaflet_preview_html",
    "BrowserUnavailableError",
]
