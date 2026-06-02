"""Deterministic map rendering for the Plot Analyzer (IMPLEMENTATION_PLAN.md Phase 3 §3.1).

Public surface:

* :class:`~plot_reports.render.layer.Layer` / :class:`LayerRole` — a styled layer of
  shapely geometries or GeoJSON dicts (does NOT depend on ``plot_geo`` — Phase 3 §3.1).
* :class:`~plot_reports.render.renderer.MapRenderer` / :func:`render_map` — deterministic
  matplotlib (Agg) render to PNG/SVG + style metadata sidecar (NFR-AUD-009).
* :class:`~plot_reports.render.renderer.RenderResult` — ``data`` bytes, ``mime_type``,
  ``style_metadata`` dict.

Determinism contract (Phase 3 §3.4): fixed figsize/dpi, fixed per-role z-order and
colours, Agg backend, no network in tests (``basemap=False``).
"""

from plot_reports.render.layer import Layer, LayerRole
from plot_reports.render.renderer import (
    RENDERER_VERSION,
    MapRenderer,
    RenderResult,
    render_map,
)

__all__ = [
    "Layer",
    "LayerRole",
    "MapRenderer",
    "RenderResult",
    "render_map",
    "RENDERER_VERSION",
]
