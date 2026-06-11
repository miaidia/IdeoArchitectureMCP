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

Phase 14 (reports & exports) adds:

* :class:`ReportModel` + builders/renderers — ONE report model, all formats
  (MD/HTML/JSON/PDF, §31 DoD; PDF guarded — ``pdf_available``).
* :func:`compare_reports` — report diff (F-0398/F-0413).
* :func:`export_masterplan_dxf` / :func:`export_masterplan_ifc` /
  :func:`export_geojson` / :func:`export_gpkg` — GIS/CAD/BIM exports (massing-only
  IFC; documented DXF layer convention).

This package depends only on ``plot_domain`` + ``plot_shared`` and MUST NOT import
``plot_connectors`` or ``plot_rules`` (Phase 3 §3.4 decoupling).
"""

from plot_reports.artifacts import (
    ArtifactStore,
    LocalArtifactStore,
    S3ArtifactStore,
    get_artifact_store,
)
from plot_reports.export import (
    DXF_LAYERS,
    ExportLayer,
    collect_analysis_layers,
    collect_variant_layers,
    export_geojson,
    export_gpkg,
    export_masterplan_dxf,
    export_masterplan_ifc,
)
from plot_reports.formats import (
    PdfUnavailableError,
    pdf_available,
    render_model_html,
    render_model_json,
    render_model_markdown,
    render_model_pdf,
)
from plot_reports.koncepcja import compliance_summary, render_koncepcja_markdown
from plot_reports.model import (
    REPORT_MODEL_VERSION,
    ReportModel,
    build_koncepcja_model,
    build_screening_model,
    compare_reports,
    snapshot_hash,
)
from plot_reports.preview import (
    preview_png_bytes,
    preview_style_metadata,
    render_preview,
    sample_preview_layers,
)
from plot_reports.pzt import (
    PZT_DISCLAIMER,
    PZT_SCALE_DENOMINATOR,
    PztDrawing,
    PztOpisowa,
    build_pzt_checklist,
    build_pzt_opisowa,
    pzt_filename,
    render_pzt_opisowa_json,
    render_pzt_opisowa_markdown,
    render_pzt_rysunkowa,
)
from plot_reports.redaction import (
    PII_FIELD_NAMES,
    REDACTION_PLACEHOLDER,
    redact_model_for_sharing,
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
    # koncepcja deliverable (Phase 11 §11.1.7)
    "render_koncepcja_markdown",
    "compliance_summary",
    # unified report model + format renderers (Phase 14 §31)
    "ReportModel",
    "REPORT_MODEL_VERSION",
    "build_screening_model",
    "build_koncepcja_model",
    "compare_reports",
    "snapshot_hash",
    "render_model_markdown",
    "render_model_html",
    "render_model_json",
    "render_model_pdf",
    "pdf_available",
    "PdfUnavailableError",
    # PII redaction for shared exports (Phase 14B, F-0483)
    "PII_FIELD_NAMES",
    "REDACTION_PLACEHOLDER",
    "redact_model_for_sharing",
    # PZT draft package (Phase 15 — Dz.U. 2020/1609 t.j. 2022/1679 §13–18)
    "PZT_DISCLAIMER",
    "PZT_SCALE_DENOMINATOR",
    "PztDrawing",
    "PztOpisowa",
    "build_pzt_checklist",
    "build_pzt_opisowa",
    "pzt_filename",
    "render_pzt_opisowa_json",
    "render_pzt_opisowa_markdown",
    "render_pzt_rysunkowa",
    # GIS/CAD/BIM exports (Phase 14)
    "DXF_LAYERS",
    "ExportLayer",
    "collect_analysis_layers",
    "collect_variant_layers",
    "export_geojson",
    "export_gpkg",
    "export_masterplan_dxf",
    "export_masterplan_ifc",
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
