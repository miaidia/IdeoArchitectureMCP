"""PZT część rysunkowa export (Phase 15 Task 3 — §15 rozporządzenia o projekcie
budowlanym, Dz.U. 2020 poz. 1609, t.j. Dz.U. 2022 poz. 1679).

A SCALED VECTOR drawing of the accepted masterplan variant:

* **declared scale** (default 1:500 — §16/§17 of the regulation name 1:500 /
  1:1000 as the PZT map scales): the figure size is COMPUTED from the parcel
  extent so that 1 real metre = ``1/scale`` paper metres exactly. The figure
  uses ``dpi = 72`` so one matplotlib SVG user unit == one PostScript point ==
  1/72 inch — a known building edge is therefore measurable in the SVG output
  (the §15.3 scale-correctness test): ``metres = units × 0.0254 × scale / 72``.
* **content** (per §15): granice działki (bold), obiekty z wymiarami
  zewnętrznymi (offset dimension lines + arrowheads + length text per exterior
  edge > 2 m) i liczbą kondygnacji, sieci/przyłącza (GESUT) where known, układ
  komunikacyjny z funkcjami dróg (w tym drogi pożarowe), zieleń, rzędne terenu
  (spot elevations from the Phase 12 NMT at building + parcel corners) — and
  HONEST omission notes when a data layer is absent (linia zabudowy / sieci /
  rzędne are never invented — anti-pattern §15.4).
* **output**: SVG (vector) AND PDF via matplotlib's PDF backend (a true vector
  backend — verified by the test suite: ``%PDF`` header, no ``/Subtype /Image``
  raster XObject, size ≪ the 150 MB e-form cap). weasyprint is NOT used here
  (unavailable on this host); the PDF comes straight from the SAME figure.
* **file naming** per the e-form convention (plan §0v2.2 reading of the
  załącznik to the rozporządzenie w sprawie projektu budowlanego): the file
  name starts with the part code (``PZT`` / ``PAB`` / ``PT``), followed by the
  date in ``rrrr.mm.dd``, followed by a distinguishing suffix — here the
  analysis id: ``PZT_{rrrr.mm.dd}_{analysis_id}.pdf``; each file ≤ 150 MB.

Determinism: Agg backend (inherited from the renderer package), fixed fonts,
explicit data limits, no randomness — same inputs, same bytes.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import matplotlib.pyplot as plt
from shapely.geometry import shape

from plot_reports.render.layer import Layer, LayerRole
from plot_reports.render.masterplan import (
    MasterplanRenderer,
    _building_items,
    masterplan_layers,
)

#: Declared PZT drawing scale (1:500 — a §16/§17 PZT map scale).
PZT_SCALE_DENOMINATOR = 500
#: Figure dpi pinned to 72 so SVG user units == points (scale measurability).
PZT_DPI = 72
#: e-form hard cap per file (plan §0v2.2): 150 MB.
PZT_MAX_PDF_BYTES = 150 * 1024 * 1024
#: PDF viewer MediaBox ceiling (Acrobat limit: 14 400 pt = 200 in) — review F4.
PZT_MAX_PAGE_IN = 200.0
#: Standard fallback scales when the sheet would exceed the viewer limit (§16/§17).
PZT_FALLBACK_SCALES = (1000, 2000)

_INCH_M = 0.0254  # metres per inch
_MARGIN_M = 12.0  # data-space margin around the drawn extent (metres)
_DIM_OFFSET_M = 4.0  # dimension-line offset from the building edge (metres)
_DIM_MIN_EDGE_M = 2.0  # edges shorter than this are not dimensioned (plan §15.1)
_COLLINEAR_EPS = 1e-6  # |cross| of unit edge directions below this = collinear (F3)

_ROAD_FUNCTION_LABEL = {
    "kdw": "KDW",
    "pozarowa": "DROGA POŻAROWA",
    "pieszojezdnia": "PJ",
    "dojscie": "dojście",
}

_NETWORK_COLORS = {
    "water": "#1f77b4",
    "sewer": "#8c564b",
    "gas": "#bcbd22",
    "power": "#d62728",
    "telecom": "#9467bd",
    "heat": "#e377c2",
}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(text)).strip("-") or "x"


def pzt_filename(generated_on: date, analysis_id: str, ext: str) -> str:
    """File name per the załącznik convention: ``PZT_{rrrr.mm.dd}_{suffix}.{ext}``.

    Convention reading (plan §0v2.2): part code first (PZT), then the date as
    ``rrrr.mm.dd``, then a distinguishing suffix (the analysis id, slugged).
    """
    return f"PZT_{generated_on.strftime('%Y.%m.%d')}_{_slug(analysis_id)}.{ext}"


@dataclass
class PztDrawing:
    """Output of :func:`render_pzt_rysunkowa`: SVG + PDF bytes + audit metadata."""

    svg: bytes
    pdf: bytes
    filename_stem: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _total_bounds(layers: list[Layer]) -> tuple[float, float, float, float]:
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for layer in layers:
        for geom in layer.shapely_geometries():
            if geom.is_empty:
                continue
            b = geom.bounds
            minx, miny = min(minx, b[0]), min(miny, b[1])
            maxx, maxy = max(maxx, b[2]), max(maxy, b[3])
    if minx > maxx:  # nothing drawn
        return 0.0, 0.0, 1.0, 1.0
    return minx, miny, maxx, maxy


def _merge_collinear_ring(
    ring: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Drop ring vertices whose adjacent edges are parallel (|cross| < eps) — F3.

    A footprint composed of several touching rectangles (DSL segments) keeps
    redundant vertices on straight elevations after the union; without merging,
    a 90 m wall would be dimensioned as 40 m + 50 m. Operates on a CLOSED ring
    (first == last coordinate), wrap-around included, and returns a closed ring.
    """
    pts = list(ring[:-1])  # open vertex list
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            d1x, d1y = x1 - x0, y1 - y0
            d2x, d2y = x2 - x1, y2 - y1
            l1, l2 = math.hypot(d1x, d1y), math.hypot(d2x, d2y)
            if (
                l1 <= 0.0
                or l2 <= 0.0
                or abs(d1x * d2y - d1y * d2x) < _COLLINEAR_EPS * l1 * l2
            ):
                pts.pop(i)
                changed = True
                break
    return pts + pts[:1]


def _draw_dimensions(ax: Any, name: str, footprint: Any) -> int:
    """Wymiary zewnętrzne: offset line + arrowheads + length text per edge > 2 m.

    The exterior ring is normalised counter-clockwise so the outward normal of
    edge ``p1→p2`` is ``(dy, -dx)/len`` — the dimension line always sits OUTSIDE
    the building. Consecutive collinear edges (touching-rectangle segments) are
    MERGED first so each straight elevation carries ONE dimension (review F3).
    Each annotation carries a ``pzt-dim-*`` gid (an ``id`` in the SVG) so tests
    can assert presence.
    """
    from shapely.geometry.polygon import orient

    if footprint.geom_type != "Polygon":
        geoms = [g for g in getattr(footprint, "geoms", []) if g.geom_type == "Polygon"]
        if not geoms:
            return 0
        footprint = max(geoms, key=lambda g: g.area)
    ring = _merge_collinear_ring(list(orient(footprint, sign=1.0).exterior.coords))  # CCW
    count = 0
    for i in range(len(ring) - 1):
        (x1, y1), (x2, y2) = ring[i], ring[i + 1]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length <= _DIM_MIN_EDGE_M:
            continue
        nx, ny = dy / length, -dx / length  # outward normal (CCW ring)
        o1 = (x1 + nx * _DIM_OFFSET_M, y1 + ny * _DIM_OFFSET_M)
        o2 = (x2 + nx * _DIM_OFFSET_M, y2 + ny * _DIM_OFFSET_M)
        gid = f"pzt-dim-{_slug(name)}-{i}"
        # Extension lines from the corners to the dimension line.
        for (px, py), (ox, oy) in ((ring[i], o1), (ring[i + 1], o2)):
            ax.plot([px, ox], [py, oy], color="#444444", linewidth=0.4, zorder=18)
        arrow = ax.annotate(
            "",
            xy=o1,
            xytext=o2,
            arrowprops={
                "arrowstyle": "<->",
                "color": "#222222",
                "linewidth": 0.7,
                "shrinkA": 0.0,
                "shrinkB": 0.0,
            },
            zorder=18,
        )
        arrow.set_gid(gid)
        angle = math.degrees(math.atan2(dy, dx))
        if angle > 90.0 or angle <= -90.0:
            angle += 180.0 if angle <= -90.0 else -180.0
        text = ax.annotate(
            f"{length:.2f}",
            xy=((o1[0] + o2[0]) / 2.0 + nx * 1.6, (o1[1] + o2[1]) / 2.0 + ny * 1.6),
            ha="center",
            va="center",
            fontsize=5.5,
            color="#222222",
            rotation=angle,
            rotation_mode="anchor",
            zorder=18,
        )
        text.set_gid(f"{gid}-text")
        count += 1
    return count


def _road_midpoint(road: dict[str, Any]) -> tuple[float, float] | None:
    centerline = road.get("centerline")
    if not centerline:
        return None
    geom = shape(centerline)
    if geom.is_empty or geom.length <= 0:
        return None
    p = geom.interpolate(0.5, normalized=True)
    return (float(p.x), float(p.y))


def render_pzt_rysunkowa(
    variant: Any,
    parcel: Any,
    *,
    analysis_id: str,
    generated_on: date,
    investor: str | None = None,
    networks: list[dict[str, Any]] | None = None,
    spot_elevations: list[dict[str, float]] | None = None,
    building_lines: list[dict[str, Any]] | None = None,
    scale: int = PZT_SCALE_DENOMINATOR,
) -> PztDrawing:
    """Render the §15 część rysunkowa at a declared scale → SVG + vector PDF.

    ``networks`` are GESUT utility dumps (``{"network_type", "geometry"}``);
    ``spot_elevations`` are NMT samples (``{"x", "y", "z"}``); ``building_lines``
    are linia-zabudowy geometries — each ``None``/empty input produces an HONEST
    omission note in the drawing and metadata instead of invented content.
    """
    notes: list[str] = []

    # --- layer stack: the masterplan layers + bold parcel boundary ------------ #
    layers, annotations = masterplan_layers(variant, parcel)
    layers[0].style = {"linewidth": 2.6, "edgecolor": "#000000"}  # granice: bold (§15)

    networks = list(networks or [])
    for net in networks:
        net_type = str(net.get("network_type", "siec"))
        geom = net.get("geometry")
        if geom is None:
            continue
        layers.append(
            Layer(
                name=f"Sieć: {net_type}",
                geometries=[geom],
                role=LayerRole.NETWORK,
                style={"edgecolor": _NETWORK_COLORS.get(net_type, "#1f77b4")},
            )
        )
    if not networks:
        notes.append(
            "sieci/przyłącza: brak danych GESUT dla tej analizy — nie naniesiono "
            "(uczciwe pominięcie, §21)"
        )

    building_lines = list(building_lines or [])
    for i, line in enumerate(building_lines):
        layers.append(
            Layer(
                name="Linia zabudowy",
                geometries=[line],
                role=LayerRole.OTHER,
                style={"facecolor": "none", "edgecolor": "#7b1fa2", "linewidth": 1.0},
            )
        )
        _ = i
    if not building_lines:
        notes.append(
            "linia zabudowy: brak danych geometrycznych ze wskaźników planistycznych "
            "— nie naniesiono"
        )

    # --- exact-scale figure geometry ------------------------------------------ #
    minx, miny, maxx, maxy = _total_bounds(layers)
    data_w_m = (maxx - minx) + 2.0 * _MARGIN_M
    data_h_m = (maxy - miny) + 2.0 * _MARGIN_M
    pad_lr_in, pad_bottom_in, pad_top_in = 0.4, 1.0, 1.5

    def _sheet_inches(denominator: int) -> tuple[float, float, float, float]:
        aw = data_w_m / (denominator * _INCH_M)
        ah = data_h_m / (denominator * _INCH_M)
        return aw, ah, aw + 2.0 * pad_lr_in, ah + pad_bottom_in + pad_top_in

    # Review F4: a multi-kilometre parcel at 1:500 would yield a page above the
    # PDF viewer MediaBox ceiling (Acrobat: 14 400 pt = 200 in) — written
    # silently, the PDF opens broken. Fall back to the next standard PZT map
    # scale (1:1000, then 1:2000 — §16/§17 name 1:500/1:1000; 1:2000 is the
    # documented oversize fallback) and record the adjustment honestly.
    requested_scale = scale
    scale_adjusted = False
    axes_w_in, axes_h_in, fig_w_in, fig_h_in = _sheet_inches(scale)
    for fallback in PZT_FALLBACK_SCALES:
        if max(fig_w_in, fig_h_in) <= PZT_MAX_PAGE_IN or fallback <= scale:
            continue
        scale = fallback
        scale_adjusted = True
        axes_w_in, axes_h_in, fig_w_in, fig_h_in = _sheet_inches(scale)
    if max(fig_w_in, fig_h_in) > PZT_MAX_PAGE_IN:  # beyond even 1:2000 — honest error
        raise ValueError(
            f"PZT: arkusz {fig_w_in:.0f}×{fig_h_in:.0f} cali przekracza limit "
            f"MediaBox przeglądarek PDF ({PZT_MAX_PAGE_IN:.0f} cali = 14 400 pt) "
            f"nawet w skali 1:{scale} — rysunek musi zostać podzielony na arkusze."
        )
    scale_adjustment_reason: str | None = None
    if scale_adjusted:
        scale_adjustment_reason = (
            f"arkusz w żądanej skali 1:{requested_scale} przekroczyłby limit "
            f"MediaBox przeglądarek PDF (14 400 pt = {PZT_MAX_PAGE_IN:.0f} cali) — "
            f"rysunek wykonano w następnej skali normowej 1:{scale}"
        )
        notes.append(f"skala: {scale_adjustment_reason}")

    fig = plt.figure(figsize=(fig_w_in, fig_h_in), dpi=PZT_DPI)
    try:
        ax = fig.add_axes(
            (
                pad_lr_in / fig_w_in,
                pad_bottom_in / fig_h_in,
                axes_w_in / fig_w_in,
                axes_h_in / fig_h_in,
            )
        )
        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.set_xlim(minx - _MARGIN_M, maxx + _MARGIN_M)
        ax.set_ylim(miny - _MARGIN_M, maxy + _MARGIN_M)

        renderer = MasterplanRenderer(figsize=(fig_w_in, fig_h_in), dpi=PZT_DPI)
        for layer in layers:
            renderer._draw_layer(ax, layer, simplify_tolerance=None)

        # Measurable building outlines (gid → SVG id) + wymiary zewnętrzne.
        dimension_count = 0
        building_count = 0
        for name, _status, footprint, _segs in _building_items(variant):
            building_count += 1
            boundary = footprint.exterior if footprint.geom_type == "Polygon" else None
            if boundary is not None:
                xs, ys = boundary.xy
                ax.plot(
                    list(xs),
                    list(ys),
                    color="#111111",
                    linewidth=1.1,
                    zorder=15,
                    gid=f"pzt-building-{_slug(name)}",
                )
            dimension_count += _draw_dimensions(ax, name, footprint)

        # Liczba kondygnacji + building names (renderer v2 annotations reused).
        renderer._draw_annotations(ax, annotations)

        # Układ komunikacyjny: function tags (incl. drogi pożarowe) at midpoints.
        road_functions: set[str] = set()
        for road in getattr(variant, "roads", []) or []:
            if not isinstance(road, dict):
                continue
            function = str(road.get("function", "?"))
            road_functions.add(function)
            mid = _road_midpoint(road)
            if mid is not None:
                ax.annotate(
                    _ROAD_FUNCTION_LABEL.get(function, function),
                    xy=mid,
                    ha="center",
                    va="center",
                    fontsize=4.5,
                    color="#555555",
                    zorder=16,
                )

        # Rzędne terenu (NMT spot elevations) — honest omission when absent.
        spot_elevations = list(spot_elevations or [])
        for i, spot in enumerate(spot_elevations):
            x, y, z = float(spot["x"]), float(spot["y"]), float(spot["z"])
            ax.plot([x], [y], marker="+", markersize=3.0, color="#005500", zorder=17)
            label = ax.annotate(
                f"{z:.2f}",
                xy=(x + 1.2, y + 1.2),
                fontsize=4.5,
                color="#005500",
                zorder=17,
            )
            label.set_gid(f"pzt-rzedna-{i}")
        if not spot_elevations:
            notes.append(
                "rzędne terenu: brak danych NMT dla tej analizy — nie naniesiono "
                "(uczciwe pominięcie, §21; nie zakładać terenu płaskiego)"
            )

        renderer._add_masterplan_legend(ax, layers)
        renderer._draw_north_arrow(ax)
        renderer._draw_scale_bar(
            ax, minx - _MARGIN_M, miny - _MARGIN_M, data_w_m, data_h_m
        )

        # --- title block (inwestor / data / skala / analysis id) -------------- #
        sheet_cm = (fig_w_in * 2.54, fig_h_in * 2.54)
        fig.text(
            0.02, 1.0 - 0.22 / fig_h_in,
            "PROJEKT ZAGOSPODAROWANIA TERENU — CZĘŚĆ RYSUNKOWA (SZKIC ROBOCZY)",
            fontsize=11, fontweight="bold", va="top",
        )
        fig.text(
            0.02, 1.0 - 0.50 / fig_h_in,
            f"Inwestor: {investor or '— (nie podano)'}   |   Data: "
            f"{generated_on.isoformat()}   |   Skala: 1:{scale} "
            f"(arkusz {sheet_cm[0]:.0f}×{sheet_cm[1]:.0f} cm)",
            fontsize=8, va="top",
        )
        fig.text(
            0.02, 1.0 - 0.72 / fig_h_in,
            f"Analiza: {analysis_id}   |   Wariant: {getattr(variant, 'id', '?')}   |   "
            f"CRS: EPSG:2180 (jednostki: metry)",
            fontsize=8, va="top",
        )
        from plot_reports.pzt.opisowa import PZT_DISCLAIMER

        fig.text(
            0.02, 1.0 - 0.95 / fig_h_in,
            PZT_DISCLAIMER,
            fontsize=6.5, color="#8a1010", va="top",
            wrap=True,
        )
        if notes:
            fig.text(
                0.02, 0.12 / fig_h_in,
                "Uwagi (uczciwe pominięcia): " + " • ".join(notes),
                fontsize=6, color="#555555", va="bottom",
            )

        svg_buf = io.BytesIO()
        fig.savefig(svg_buf, format="svg", dpi=PZT_DPI)
        pdf_buf = io.BytesIO()
        # matplotlib's PDF backend IS a vector backend (verified by tests:
        # %PDF header, no /Subtype /Image raster XObject) — weasyprint is NOT
        # involved (unavailable on this host; Phase 15 build-env note).
        fig.savefig(pdf_buf, format="pdf", dpi=PZT_DPI)
    finally:
        plt.close(fig)

    svg_bytes = svg_buf.getvalue()
    pdf_bytes = pdf_buf.getvalue()
    if len(pdf_bytes) > PZT_MAX_PDF_BYTES:  # pragma: no cover - never for parcels
        raise ValueError(
            f"PZT PDF przekracza limit e-formy 150 MB ({len(pdf_bytes)} B) — "
            "rysunek musi zostać podzielony."
        )

    stem = pzt_filename(generated_on, analysis_id, "pdf").removesuffix(".pdf")
    return PztDrawing(
        svg=svg_bytes,
        pdf=pdf_bytes,
        filename_stem=stem,
        metadata={
            "scale_denominator": scale,
            # Review F4: honest record when the sheet was rescaled to stay under
            # the PDF viewer MediaBox ceiling (None reason when not adjusted).
            "requested_scale_denominator": requested_scale,
            "scale_adjusted": scale_adjusted,
            "scale_adjustment_reason": scale_adjustment_reason,
            "dpi": PZT_DPI,
            # 1 real metre on paper, expressed in SVG user units (== points at 72 dpi).
            "svg_units_per_metre": PZT_DPI / (scale * _INCH_M),
            "figsize_in": [round(fig_w_in, 3), round(fig_h_in, 3)],
            "data_bounds": [minx, miny, maxx, maxy],
            "margin_m": _MARGIN_M,
            "building_count": building_count,
            "dimension_count": dimension_count,
            "spot_elevation_count": len(spot_elevations),
            "network_count": len(networks),
            "building_lines_count": len(building_lines),
            "road_functions": sorted(road_functions),
            "fire_road_tagged": "pozarowa" in road_functions,
            "greenery_drawn": any(layer.role == LayerRole.GREENERY for layer in layers),
            "notes": notes,
            "crs": "EPSG:2180",
            "pdf_bytes": len(pdf_bytes),
            "pdf_max_bytes": PZT_MAX_PDF_BYTES,
        },
    )
