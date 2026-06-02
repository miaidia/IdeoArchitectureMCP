"""Renderer-backed map-preview function shared by the MCP server (Phase 3 §3.1.3).

Since analysis logic is still stubbed (Phase 7), the preview is driven from a
**sample/stub parcel + buildable-envelope geometry** with example constraint area
attribution, so the MCP image-content path is demonstrable and testable NOW. Phase 7/9
replace :func:`sample_preview_layers` with real geometry while the render/contract
stays identical.

Returned by:
  * the ``report_generate`` tool (``format="png"`` → resource_link by default),
  * the ``analysis://{id}/map-preview.png`` resource (PNG bytes),
  * the dedicated inline preview path (image content block).
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon

from plot_reports.render import Layer, LayerRole, MapRenderer, RenderResult


def sample_preview_layers() -> list[Layer]:
    """Build the sample parcel + envelope + constraint layers (Phase 3 stub geometry).

    Coordinates are plain metres in the EPSG:2180 analytical frame (their absolute
    location is irrelevant for a preview; only shape + relative layout matter). The
    constraint layers carry example area attribution so the caption demonstrates the
    §30 "which constraint removed which area" contract.
    """
    # A simple rectangular parcel, 50 m x 40 m = 2000 m².
    parcel = Polygon([(0, 0), (50, 0), (50, 40), (0, 40)])

    # A hard no-build strip along the bottom (e.g. road setback): 50 m x 6 m = 300 m².
    no_build = Polygon([(0, 0), (50, 0), (50, 6), (0, 6)])

    # A soft constraint patch (e.g. utility buffer): 12 m x 10 m = 120 m².
    soft = Polygon([(38, 28), (50, 28), (50, 38), (38, 38)])

    # Buildable envelope = parcel minus the hard/soft areas (illustrative rectangle).
    envelope = Polygon([(4, 8), (46, 8), (46, 26), (4, 26)])

    return [
        Layer(name="Parcel", geometries=parcel, role=LayerRole.PARCEL),
        Layer(
            name="Road setback",
            geometries=no_build,
            role=LayerRole.NO_BUILD,
            removed_area_m2=300.0,
            removed_area_percent=15.0,
        ),
        Layer(
            name="Utility buffer",
            geometries=soft,
            role=LayerRole.CONSTRAINT_SOFT,
            removed_area_m2=120.0,
            removed_area_percent=6.0,
        ),
        Layer(name="Buildable envelope", geometries=envelope, role=LayerRole.BUILDABLE_ENVELOPE),
    ]


def render_preview(
    *,
    analysis_id: str | None = None,
    fmt: str = "png",
    crs: str = "EPSG:2180",
    layers: list[Layer] | None = None,
    simplify_tolerance: float | None = None,
) -> RenderResult:
    """Render a map preview (sample geometry by default) to PNG or SVG.

    Parameters
    ----------
    analysis_id:
        Used only for the title; the geometry is the Phase 3 sample stub.
    fmt:
        ``"png"`` or ``"svg"``.
    crs:
        CRS label for style metadata (default analytical EPSG:2180).
    layers:
        Override the sample layers (Phase 7/9 will pass real ones).
    simplify_tolerance:
        Optional preview simplification (NFR-PERF-012); analysis keeps full precision.
    """
    use_layers = layers if layers is not None else sample_preview_layers()
    title = f"Map preview — analysis {analysis_id}" if analysis_id else "Map preview (sample)"
    return MapRenderer().render_map(
        use_layers,
        crs=crs,
        fmt="svg" if fmt == "svg" else "png",
        basemap=False,  # never network in the default preview (deterministic)
        title=title,
        simplify_tolerance=simplify_tolerance,
    )


def preview_png_bytes(analysis_id: str | None = None) -> bytes:
    """Convenience: PNG bytes of the sample preview (for the resource path)."""
    return render_preview(analysis_id=analysis_id, fmt="png").data


def preview_style_metadata(analysis_id: str | None = None) -> dict[str, Any]:
    """Convenience: style metadata of the sample preview (NFR-AUD-009)."""
    return render_preview(analysis_id=analysis_id, fmt="png").style_metadata
