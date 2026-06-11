"""Masterplan renderer v2 (Phase 9 §9.1.4) — extends the Phase 3 deterministic renderer.

``render_masterplan(proposal_or_variant, parcel, metrics=..., fmt=...)`` renders a
ROBYG-class plan zagospodarowania terenu:

* building footprints hatched BY STATUS (exemplar legend: istniejące / zrealizowane /
  w budowie / projektowane; ``zabytek_do_remontu`` = istniejące + distinct edge),
* **floor-count labels** at each building-segment centroid ("4", "7") + building name,
* roads (buffered centerlines), parking, playgrounds, greenery, retention,
* legend (4 status entries + layer kinds), north arrow, scale bar (data units are
  metres), and an optional side **table panel** (per-stage Liczba mieszkań / PUM / PUU /
  PU rows + SUMA) fed from the Phase 9 capacity metrics.

Determinism (golden-image testable, Phase 3 §3.4): Agg backend (inherited), fixed
figsize/dpi/fonts/colours/z-order, explicit data limits — no randomness. Style metadata
is returned per NFR-AUD-009 like every other render.

Decoupling: this module accepts DUCK-TYPED proposals/variants (it reads attributes
only) so ``plot_reports`` keeps its Phase 3 rule of not importing ``plot_agent`` /
``plot_planning`` / ``plot_rules``.
"""

from __future__ import annotations

# Agg is forced by plot_reports.render.renderer BEFORE pyplot is imported there; this
# module imports pyplot after importing the renderer, so the backend is already set.
from dataclasses import dataclass
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from shapely.geometry.base import BaseGeometry

from plot_reports.render.layer import GeometryLike, Layer, LayerRole, _to_shapely
from plot_reports.render.renderer import Fmt, MapRenderer, RenderResult

# Bump when the masterplan visual output changes intentionally (golden references).
MASTERPLAN_RENDERER_VERSION = "2.0.0"

# Building status → layer role (presentation semantics, not legal values).
_STATUS_ROLE: dict[str, LayerRole] = {
    "istniejacy": LayerRole.BUILDING_EXISTING,
    "zabytek_do_remontu": LayerRole.BUILDING_EXISTING,
    "zrealizowany": LayerRole.BUILDING_COMPLETED,
    "w_budowie": LayerRole.BUILDING_UNDER_CONSTRUCTION,
    "projektowany": LayerRole.BUILDING_PLANNED,
}
_STATUS_LAYER_NAME: dict[LayerRole, str] = {
    LayerRole.BUILDING_EXISTING: "Budynki istniejące",
    LayerRole.BUILDING_COMPLETED: "Budynki zrealizowane",
    LayerRole.BUILDING_UNDER_CONSTRUCTION: "Budynki w budowie",
    LayerRole.BUILDING_PLANNED: "Budynki projektowane",
}
# Distinct edge for heritage buildings kept for renovation (mapped onto EXISTING).
_ZABYTEK_STYLE: dict[str, Any] = {"edgecolor": "#7b1fa2", "linewidth": 2.2}

# Fixed canvas geometry for the masterplan render (reproducible pixels).
_FIGSIZE_MAP_ONLY = (8.0, 8.0)
_FIGSIZE_WITH_TABLE = (12.0, 8.0)
_TABLE_COL_LABELS = ["Etap", "Liczba mieszkań", "PUM [m²]", "PUU [m²]", "PU [m²]"]


@dataclass(frozen=True)
class MapAnnotation:
    """A deterministic text annotation on the map (floor counts, building names)."""

    text: str
    x: float
    y: float
    kind: str = "floors"  # "floors" | "name"

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "x": self.x, "y": self.y, "kind": self.kind}


def _nice_scale_length(span_m: float) -> float:
    """Largest 1/2/5×10^k length not exceeding ~span/4 (deterministic scale bar)."""
    target = max(span_m / 4.0, 1.0)
    best = 1.0
    for exp in range(0, 6):
        for mantissa in (1.0, 2.0, 5.0):
            candidate = mantissa * (10.0**exp)
            if candidate <= target:
                best = candidate
    return best


# --------------------------------------------------------------------------- #
# Layer/annotation builders (duck-typed proposal OR domain MasterplanVariant)
# --------------------------------------------------------------------------- #
def _building_items(obj: Any) -> list[tuple[str, str, BaseGeometry, list[tuple[BaseGeometry, int]]]]:
    """Normalise buildings to ``(name, status, footprint, [(segment_geom, floors)])``.

    Accepts a DSL-v2 ``MasterplanProposal`` (buildings with ``segments`` exposing
    ``geometry()``/``floors``) or a domain ``MasterplanVariant`` (``BuildingRecord``
    with a GeoJSON ``geometry`` + ``floors_by_segment``).
    """
    items: list[tuple[str, str, BaseGeometry, list[tuple[BaseGeometry, int]]]] = []
    for building in getattr(obj, "buildings", []):
        name = str(getattr(building, "name", ""))
        status = str(getattr(building, "status", "projektowany"))
        segments = getattr(building, "segments", None)
        if segments:  # DSL v2 proposal path — per-segment geometry + floors
            seg_pairs = [(s.geometry(), int(s.floors)) for s in segments]
            footprint = building.footprint_geometry()
        else:  # MasterplanVariant path — one geometry + floors_by_segment labels
            geometry = getattr(building, "geometry", None)
            if geometry is None:
                continue
            footprint = _to_shapely(geometry)
            floors_list = list(getattr(building, "floors_by_segment", []) or [])
            floors = int(floors_list[0]) if floors_list else 0
            seg_pairs = [(footprint, floors)]
        items.append((name, status, footprint, seg_pairs))
    return items


def masterplan_layers(
    proposal_or_variant: Any,
    parcel: GeometryLike,
    *,
    envelope: GeometryLike | None = None,
    violations: list[GeometryLike] | None = None,
) -> tuple[list[Layer], list[MapAnnotation]]:
    """Build the deterministic layer stack + annotations for a masterplan render.

    ``violations`` (Phase 10) are the ``geometry_evidence`` geometries of FAILING
    inter-building rule checks (WT/ppoż); they are appended as a red
    :attr:`LayerRole.VIOLATION` layer drawn on top of everything (plan §10.1.7).
    """
    layers: list[Layer] = [Layer(name="Działka", geometries=[parcel], role=LayerRole.PARCEL)]
    if envelope is not None:
        layers.append(
            Layer(
                name="Buildable envelope",
                geometries=[envelope],
                role=LayerRole.BUILDABLE_ENVELOPE,
                style={"alpha": 0.25},  # keep the plan readable under the buildings
            )
        )

    greenery = list(getattr(proposal_or_variant, "greenery_polygons", None)
                    or getattr(proposal_or_variant, "greenery", []) or [])
    if greenery:
        layers.append(Layer(name="Zieleń (PBC)", geometries=list(greenery), role=LayerRole.GREENERY))
    retention = list(getattr(proposal_or_variant, "retention", []) or [])
    if retention:
        layers.append(Layer(name="Retencja", geometries=list(retention), role=LayerRole.RETENTION))
    playgrounds = list(getattr(proposal_or_variant, "playgrounds", []) or [])
    if playgrounds:
        layers.append(
            Layer(name="Plac zabaw", geometries=list(playgrounds), role=LayerRole.PLAYGROUND)
        )

    road_polys: list[GeometryLike] = []
    for road in getattr(proposal_or_variant, "roads", []) or []:
        if hasattr(road, "to_polygon"):
            road_polys.append(road.to_polygon())
        elif isinstance(road, dict) and road.get("centerline"):
            # Stored-variant path: RoadElement.model_dump() keeps the `centerline`
            # LineString + `width_m` — buffer by half the width (flat caps), exactly
            # like RoadElement.to_polygon() on the live-proposal path.
            width_m = float(road.get("width_m") or 0.0)
            centerline = _to_shapely(road["centerline"])
            road_polys.append(
                centerline.buffer(width_m / 2.0, cap_style="flat")
                if width_m > 0
                else centerline
            )
        elif isinstance(road, dict) and road.get("geometry"):
            road_polys.append(road["geometry"])
    if road_polys:
        layers.append(Layer(name="Drogi wewnętrzne", geometries=road_polys, role=LayerRole.ROAD))

    parking_polys: list[GeometryLike] = []
    underground_polys: list[GeometryLike] = []
    for p in getattr(proposal_or_variant, "parking", []) or []:
        geom: GeometryLike | None
        if hasattr(p, "geometry") and callable(p.geometry):
            geom = p.geometry()
            kind = str(p.kind)
        elif isinstance(p, dict):
            geom = p.get("polygon") or p.get("geometry")
            kind = str(p.get("kind", "naziemny"))
        else:  # pragma: no cover - defensive
            continue
        if geom is None:
            continue
        (underground_polys if kind == "hala_podziemna" else parking_polys).append(geom)
    if parking_polys:
        layers.append(Layer(name="Parking naziemny", geometries=parking_polys, role=LayerRole.PARKING))
    if underground_polys:
        layers.append(
            Layer(
                name="Hala garażowa (podziemna)",
                geometries=underground_polys,
                role=LayerRole.PARKING,
                # Underground: outline-only dashed look (deterministic override).
                style={"facecolor": "none", "alpha": 0.9, "hatch": None, "linewidth": 1.4},
            )
        )

    # Buildings grouped by status role; zabytek gets its own layer (distinct edge).
    by_role: dict[LayerRole, list[GeometryLike]] = {}
    zabytek_geoms: list[GeometryLike] = []
    annotations: list[MapAnnotation] = []
    parcel_geom = _to_shapely(parcel)
    pminx, pminy, pmaxx, pmaxy = parcel_geom.bounds
    name_dy = -(pmaxy - pminy) * 0.03  # deterministic name offset below the centroid

    for name, status, footprint, seg_pairs in _building_items(proposal_or_variant):
        role = _STATUS_ROLE.get(status, LayerRole.BUILDING_PLANNED)
        if status == "zabytek_do_remontu":
            zabytek_geoms.append(footprint)
        else:
            by_role.setdefault(role, []).append(footprint)
        for seg_geom, floors in seg_pairs:
            c = seg_geom.centroid
            if floors > 0:
                annotations.append(MapAnnotation(text=str(floors), x=c.x, y=c.y, kind="floors"))
        fc = footprint.centroid
        annotations.append(MapAnnotation(text=name, x=fc.x, y=fc.y + name_dy, kind="name"))

    for role in (
        LayerRole.BUILDING_EXISTING,
        LayerRole.BUILDING_COMPLETED,
        LayerRole.BUILDING_UNDER_CONSTRUCTION,
        LayerRole.BUILDING_PLANNED,
    ):
        if role in by_role:
            layers.append(Layer(name=_STATUS_LAYER_NAME[role], geometries=by_role[role], role=role))
    if zabytek_geoms:
        layers.append(
            Layer(
                name="Zabytek do remontu",
                geometries=zabytek_geoms,
                role=LayerRole.BUILDING_EXISTING,
                style=dict(_ZABYTEK_STYLE),
            )
        )
    if violations:
        # Phase 10 violation overlay: red, top z-order (style from _ROLE_STYLE).
        layers.append(
            Layer(
                name="Naruszenia reguł (WT/ppoż)",
                geometries=list(violations),
                role=LayerRole.VIOLATION,
            )
        )
    return layers, annotations


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
class MasterplanRenderer(MapRenderer):
    """Masterplan map renderer (extends :class:`MapRenderer`, Phase 9 §9.1.4)."""

    def render(
        self,
        layers: list[Layer],
        annotations: list[MapAnnotation],
        *,
        table_rows: list[dict[str, Any]] | None = None,
        crs: str = "EPSG:2180",
        fmt: Fmt = "png",
        title: str | None = None,
        caption: str | None = None,
    ) -> RenderResult:
        """Render layers + annotations (+ optional per-stage table panel) to PNG/SVG."""
        if fmt not in ("png", "svg"):
            raise ValueError(f"Unsupported format {fmt!r}; expected 'png' or 'svg'.")

        has_table = bool(table_rows)
        figsize = _FIGSIZE_WITH_TABLE if has_table else _FIGSIZE_MAP_ONLY
        fig = plt.figure(figsize=figsize, dpi=self.dpi)
        try:
            if has_table:
                gs = fig.add_gridspec(1, 2, width_ratios=[2.0, 1.0], wspace=0.05)
                ax = fig.add_subplot(gs[0, 0])
                ax_table = fig.add_subplot(gs[0, 1])
                ax_table.set_axis_off()
            else:
                ax = fig.add_subplot(1, 1, 1)
                ax_table = None
            ax.set_aspect("equal")
            ax.set_axis_off()

            for layer in layers:
                self._draw_layer(ax, layer, simplify_tolerance=None)

            # Deterministic data limits (5% margin around all layer geometry).
            minx, miny, maxx, maxy = self._total_bounds(layers)
            mx = (maxx - minx) * 0.05 or 1.0
            my = (maxy - miny) * 0.05 or 1.0
            ax.set_xlim(minx - mx, maxx + mx)
            ax.set_ylim(miny - my, maxy + my)

            self._draw_annotations(ax, annotations)
            self._add_masterplan_legend(ax, layers)
            self._draw_north_arrow(ax)
            self._draw_scale_bar(ax, minx, miny, maxx - minx, maxy - miny)

            if title:
                ax.set_title(title, fontsize=12, fontweight="bold")
            if ax_table is not None and table_rows:
                self._draw_stage_table(ax_table, table_rows)

            caption_text = self._build_caption(layers, caption)
            if caption_text:
                fig.subplots_adjust(bottom=0.16)
                fig.text(0.02, 0.02, caption_text, fontsize=8, va="bottom", ha="left")

            data, mime = self._save(fig, fmt)
        finally:
            plt.close(fig)

        metadata = self._style_metadata(layers, crs=crs, fmt=fmt, basemap=False)
        metadata["masterplan_renderer_version"] = MASTERPLAN_RENDERER_VERSION
        metadata["annotations"] = [a.to_dict() for a in annotations]
        metadata["table_rows"] = len(table_rows or [])
        metadata["legend"] = "status:istniejące/zrealizowane/w budowie/projektowane + layer kinds"
        return RenderResult(data=data, mime_type=mime, style_metadata=metadata)

    # ------------------------------------------------------------------ #
    def _total_bounds(self, layers: list[Layer]) -> tuple[float, float, float, float]:
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

    def _draw_annotations(self, ax: Any, annotations: list[MapAnnotation]) -> None:
        """Floor counts (bold, centred at segment centroid) + building names."""
        for a in annotations:
            if a.kind == "floors":
                ax.annotate(
                    a.text, xy=(a.x, a.y), ha="center", va="center",
                    fontsize=9, fontweight="bold", color="#111111", zorder=20,
                )
            else:
                ax.annotate(
                    a.text, xy=(a.x, a.y), ha="center", va="top",
                    fontsize=6.5, color="#333333", zorder=20,
                )

    def _add_masterplan_legend(self, ax: Any, layers: list[Layer]) -> None:
        """Legend: ALWAYS the 4 status entries (exemplar contract) + present layer kinds."""
        handles: list[Any] = []
        for role, label in (
            (LayerRole.BUILDING_EXISTING, "istniejące"),
            (LayerRole.BUILDING_COMPLETED, "zrealizowane"),
            (LayerRole.BUILDING_UNDER_CONSTRUCTION, "w budowie"),
            (LayerRole.BUILDING_PLANNED, "projektowane"),
        ):
            style = self._effective_style(Layer(name=label, role=role))
            handles.append(
                Patch(
                    facecolor=style["facecolor"], edgecolor=style["edgecolor"],
                    alpha=style["alpha"], hatch=style.get("hatch"), label=label,
                )
            )
        seen: set[str] = set()
        for layer in layers:
            if layer.role in _STATUS_LAYER_NAME and layer.name in _STATUS_LAYER_NAME.values():
                continue  # already covered by the four status entries
            if layer.name in seen:
                continue
            seen.add(layer.name)
            style = self._effective_style(layer)
            handles.append(
                Patch(
                    facecolor=style["facecolor"], edgecolor=style["edgecolor"],
                    alpha=style["alpha"], hatch=style.get("hatch"), label=layer.name,
                )
            )
        ax.legend(handles=handles, loc="upper right", fontsize=7, framealpha=0.9)

    def _draw_north_arrow(self, ax: Any) -> None:
        """North arrow in fixed axes-fraction coordinates (deterministic)."""
        ax.annotate(
            "N", xy=(0.04, 0.97), xytext=(0.04, 0.88),
            xycoords="axes fraction", textcoords="axes fraction",
            ha="center", va="center", fontsize=10, fontweight="bold",
            arrowprops={"arrowstyle": "-|>", "color": "#111111", "linewidth": 1.2},
            zorder=30,
        )

    def _draw_scale_bar(self, ax: Any, minx: float, miny: float, w: float, h: float) -> None:
        """Simple metric scale bar — data units ARE metres (EPSG:2180)."""
        length = _nice_scale_length(w)
        x0 = minx + w * 0.04
        y0 = miny + h * 0.03
        tick = h * 0.012
        ax.plot([x0, x0 + length], [y0, y0], color="#111111", linewidth=2.0, zorder=30)
        for xt in (x0, x0 + length):
            ax.plot([xt, xt], [y0 - tick, y0 + tick], color="#111111", linewidth=1.2, zorder=30)
        ax.annotate(
            f"{length:g} m", xy=(x0 + length / 2.0, y0 + tick * 1.5),
            ha="center", va="bottom", fontsize=7, color="#111111", zorder=30,
        )

    def _draw_stage_table(self, ax_table: Any, rows: list[dict[str, Any]]) -> None:
        """Side panel: per-stage Liczba mieszkań / PUM / PUU / PU rows + SUMA."""
        cell_text = [
            [
                str(r.get("etap", "")),
                str(r.get("liczba_mieszkan", "")),
                f"{r.get('pum_m2', 0):,.0f}".replace(",", " "),
                f"{r.get('puu_m2', 0):,.0f}".replace(",", " "),
                f"{r.get('pu_m2', 0):,.0f}".replace(",", " "),
            ]
            for r in rows
        ]
        table = ax_table.table(
            cellText=cell_text, colLabels=_TABLE_COL_LABELS, loc="upper center", cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(7)
        table.scale(1.0, 1.4)
        ax_table.set_title("Zestawienie etapów", fontsize=9, fontweight="bold")


def render_masterplan(
    proposal_or_variant: Any,
    parcel: GeometryLike,
    *,
    metrics: Any | None = None,
    envelope: GeometryLike | None = None,
    violations: list[GeometryLike] | None = None,
    fmt: Fmt = "png",
    title: str | None = None,
    crs: str = "EPSG:2180",
) -> RenderResult:
    """Render a masterplan proposal/variant to PNG or SVG (Phase 9 §9.1.4).

    ``metrics`` is the Phase 9 capacity metrics (a ``MasterplanMetrics`` object or its
    ``to_dict()`` form); when present its ``stage_table`` feeds the side table panel.
    ``violations`` (Phase 10) are failing rule-check evidence geometries rendered as
    the red top-z-order :attr:`LayerRole.VIOLATION` overlay.
    """
    layers, annotations = masterplan_layers(
        proposal_or_variant, parcel, envelope=envelope, violations=violations
    )
    table_rows: list[dict[str, Any]] | None = None
    if metrics is not None:
        metrics_dict = metrics.to_dict() if hasattr(metrics, "to_dict") else metrics
        rows = metrics_dict.get("stage_table") if isinstance(metrics_dict, dict) else None
        if rows:
            table_rows = list(rows)
    return MasterplanRenderer().render(
        layers,
        annotations,
        table_rows=table_rows,
        crs=crs,
        fmt=fmt,
        title=title or "Plan zagospodarowania terenu (koncepcja)",
    )
