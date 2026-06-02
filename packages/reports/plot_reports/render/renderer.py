"""Deterministic map renderer (IMPLEMENTATION_PLAN.md Phase 3 §3.1 / §3.4).

Renders a list of :class:`~plot_reports.render.layer.Layer` to PNG or SVG via
matplotlib (forced ``Agg`` backend) using GeoSeries plotting. The render is
**deterministic** for golden-image tests (Phase 3 §3.3):

* ``Agg`` backend forced *before* importing ``pyplot`` (no display, no GUI).
* Fixed ``figsize`` + ``dpi`` + ``z-order`` + per-role colours (no random styling).
* No network access unless ``basemap=True`` (tests always pass ``basemap=False``).

The render contract (§30 / §17): a legend distinguishes **hard vs soft** constraints,
and a caption area lists **which constraint removed which area** (m² / %). Style
metadata (CRS, ordered layers, style params, renderer version) is returned and meant
to be persisted as a sidecar (NFR-AUD-009).
"""

from __future__ import annotations

# Force the non-interactive Agg backend BEFORE importing pyplot so rendering is
# headless and deterministic across hosts (Phase 3 §3.4 determinism).
import matplotlib

matplotlib.use("Agg")

import io
from dataclasses import dataclass, field
from typing import Any, Literal

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import GeometryCollection

from plot_reports.render.layer import Layer, LayerRole

# Bump when the visual output changes intentionally; persisted in style metadata and
# used to invalidate golden references (NFR-AUD-009 / Phase 3 §3.3 UPDATE_GOLDEN).
RENDERER_VERSION = "1.0.0"

# Deterministic per-role style. z_order ascends so the parcel outline sits beneath
# constraints and the buildable envelope sits on top (fixed order — Phase 3 §3.4).
_ROLE_STYLE: dict[LayerRole, dict[str, Any]] = {
    LayerRole.PARCEL: {
        "facecolor": "#f5f5f0",
        "edgecolor": "#222222",
        "alpha": 1.0,
        "linewidth": 1.6,
        "z_order": 1,
    },
    LayerRole.NO_BUILD: {
        "facecolor": "#9e9e9e",
        "edgecolor": "#5a5a5a",
        "alpha": 0.55,
        "linewidth": 0.8,
        "z_order": 2,
    },
    LayerRole.CONSTRAINT_HARD: {
        "facecolor": "#d62728",  # red — hard blocker
        "edgecolor": "#7f0e0e",
        "alpha": 0.45,
        "linewidth": 1.0,
        "z_order": 3,
    },
    LayerRole.CONSTRAINT_SOFT: {
        "facecolor": "#ff7f0e",  # orange — soft constraint
        "edgecolor": "#a65a08",
        "alpha": 0.35,
        "linewidth": 0.9,
        "z_order": 4,
    },
    LayerRole.NETWORK: {
        "facecolor": "none",
        "edgecolor": "#1f77b4",  # blue lines — networks
        "alpha": 1.0,
        "linewidth": 1.4,
        "z_order": 5,
    },
    LayerRole.BUILDABLE_ENVELOPE: {
        "facecolor": "#2ca02c",  # green — what you can build on
        "edgecolor": "#176117",
        "alpha": 0.55,
        "linewidth": 1.4,
        "z_order": 6,
    },
    LayerRole.OTHER: {
        "facecolor": "#c7c7c7",
        "edgecolor": "#666666",
        "alpha": 0.5,
        "linewidth": 0.8,
        "z_order": 0,
    },
}

# Fixed canvas geometry for reproducible pixels (Phase 3 §3.4).
_FIGSIZE = (8.0, 8.0)
_DPI = 100

_LegendHardSoft = {
    LayerRole.CONSTRAINT_HARD: "Hard constraint",
    LayerRole.NO_BUILD: "No-build zone (hard)",
    LayerRole.CONSTRAINT_SOFT: "Soft constraint",
}

Fmt = Literal["png", "svg"]


@dataclass
class RenderResult:
    """Output of a render (Phase 3 §3.1): bytes + mime type + style metadata."""

    data: bytes
    mime_type: str
    style_metadata: dict[str, Any] = field(default_factory=dict)


class MapRenderer:
    """Deterministic GIS map renderer (Phase 3 §3.1).

    Stateless aside from configuration; :meth:`render_map` does all the work.
    """

    def __init__(self, *, figsize: tuple[float, float] = _FIGSIZE, dpi: int = _DPI) -> None:
        self.figsize = figsize
        self.dpi = dpi

    def render_map(
        self,
        layers: list[Layer],
        *,
        crs: str = "EPSG:2180",
        fmt: Fmt = "png",
        basemap: bool = False,
        title: str | None = None,
        caption: str | None = None,
        simplify_tolerance: float | None = None,
    ) -> RenderResult:
        """Render ``layers`` to PNG or SVG with a legend + attribution caption.

        Parameters
        ----------
        layers:
            Layers to draw, in declaration order; z-order comes from each role.
        crs:
            CRS label recorded in style metadata (default analytical EPSG:2180).
        fmt:
            ``"png"`` or ``"svg"``.
        basemap:
            When ``True`` adds a ``contextily`` tile background (NETWORK — Phase 0.4);
            this requires network access and is NEVER used in deterministic tests.
        title / caption:
            Optional title and an extra caption line.
        simplify_tolerance:
            Optional preview simplification (F-0514 / §26.3 / NFR-PERF-012). Analysis
            keeps full precision; previews may simplify.
        """
        if fmt not in ("png", "svg"):
            raise ValueError(f"Unsupported format {fmt!r}; expected 'png' or 'svg'.")

        fig, ax = plt.subplots(figsize=self.figsize, dpi=self.dpi)
        try:
            ax.set_aspect("equal")
            ax.set_axis_off()

            # Draw layers in declaration order; matplotlib honours per-artist zorder.
            roles_drawn: list[LayerRole] = []
            for layer in layers:
                drew = self._draw_layer(ax, layer, simplify_tolerance=simplify_tolerance)
                if drew:
                    roles_drawn.append(layer.role)

            if basemap:
                # Optional tile background — network access; excluded from tests (§3.4).
                self._add_basemap(ax, crs)

            if title:
                ax.set_title(title, fontsize=12, fontweight="bold")

            self._add_legend(ax, layers)
            caption_text = self._build_caption(layers, caption)
            if caption_text:
                # Reserve room at the bottom for the attribution caption (§30).
                fig.subplots_adjust(bottom=0.18)
                fig.text(0.02, 0.02, caption_text, fontsize=8, va="bottom", ha="left")

            data, mime = self._save(fig, fmt)
        finally:
            plt.close(fig)

        return RenderResult(
            data=data,
            mime_type=mime,
            style_metadata=self._style_metadata(layers, crs=crs, fmt=fmt, basemap=basemap),
        )

    # ------------------------------------------------------------------ #
    # Drawing helpers
    # ------------------------------------------------------------------ #
    def _draw_layer(
        self, ax: Any, layer: Layer, *, simplify_tolerance: float | None
    ) -> bool:
        """Draw one layer's geometries; returns ``True`` if anything was drawn."""
        geoms = [g for g in layer.shapely_geometries(simplify_tolerance=simplify_tolerance)
                 if not g.is_empty]
        if not geoms:
            return False

        style = self._effective_style(layer)
        zorder = style["z_order"]

        # Use a GeoSeries so geopandas drives the matplotlib plotting (Phase 0.4
        # "geopandas .plot()"); set CRS-free (plain coordinates already in target CRS).
        import geopandas as gpd

        series = gpd.GeoSeries(geoms)
        is_line_only = all(g.geom_type in ("LineString", "MultiLineString") for g in geoms)

        if layer.role == LayerRole.NETWORK or is_line_only:
            series.plot(
                ax=ax,
                color=style["edgecolor"],
                linewidth=style["linewidth"],
                zorder=zorder,
            )
        else:
            series.plot(
                ax=ax,
                facecolor=style["facecolor"],
                edgecolor=style["edgecolor"],
                alpha=style["alpha"],
                linewidth=style["linewidth"],
                zorder=zorder,
            )
        return True

    def _effective_style(self, layer: Layer) -> dict[str, Any]:
        """Role defaults merged with per-layer overrides (deterministic)."""
        merged = dict(_ROLE_STYLE[layer.role])
        merged.update(layer.style or {})
        return merged

    def _add_basemap(self, ax: Any, crs: str) -> None:  # pragma: no cover - network path
        """Add a contextily tile basemap (Phase 0.4). Network; excluded from tests."""
        import contextily as cx

        cx.add_basemap(ax, crs=crs)

    def _add_legend(self, ax: Any, layers: list[Layer]) -> None:
        """Build a legend distinguishing hard vs soft constraints (§30)."""
        handles: list[Any] = []
        seen: set[str] = set()
        for layer in layers:
            style = self._effective_style(layer)
            # Deterministic legend label: hard/soft tag for constraints, else the name.
            tag = _LegendHardSoft.get(layer.role)
            label = f"{layer.name} ({tag})" if tag else layer.name
            if label in seen:
                continue
            seen.add(label)
            if layer.role == LayerRole.NETWORK:
                handles.append(
                    Line2D([0], [0], color=style["edgecolor"], lw=style["linewidth"], label=label)
                )
            else:
                handles.append(
                    Patch(
                        facecolor=style["facecolor"],
                        edgecolor=style["edgecolor"],
                        alpha=style["alpha"],
                        label=label,
                    )
                )
        if handles:
            ax.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.9)

    def _build_caption(self, layers: list[Layer], extra: str | None) -> str:
        """Caption listing which constraint removed which area (§30 / §17)."""
        lines: list[str] = []
        for layer in layers:
            if not layer.has_attribution():
                continue
            label = layer.attribution_label or layer.name
            parts: list[str] = []
            if layer.removed_area_m2 is not None:
                parts.append(f"{layer.removed_area_m2:,.1f} m²")
            if layer.removed_area_percent is not None:
                parts.append(f"{layer.removed_area_percent:.1f}%")
            tag = "hard" if layer.is_hard_constraint() else "soft"
            lines.append(f"− {label} [{tag}]: {' / '.join(parts)}")
        if lines:
            header = "Area removed by constraint:"
            lines = [header, *lines]
        if extra:
            lines.append(extra)
        return "\n".join(lines)

    def _save(self, fig: Any, fmt: Fmt) -> tuple[bytes, str]:
        """Serialise the figure to bytes for the requested format."""
        buf = io.BytesIO()
        if fmt == "png":
            fig.savefig(buf, format="png", dpi=self.dpi)
            return buf.getvalue(), "image/png"
        # SVG path — deterministic, valid XML starting with <?xml (Phase 3 §3.3).
        fig.savefig(buf, format="svg")
        return buf.getvalue(), "image/svg+xml"

    def _style_metadata(
        self, layers: list[Layer], *, crs: str, fmt: Fmt, basemap: bool
    ) -> dict[str, Any]:
        """Style metadata sidecar (NFR-AUD-009): CRS, ordered layers, params, version."""
        return {
            "renderer_version": RENDERER_VERSION,
            "crs": crs,
            "format": fmt,
            "figsize": list(self.figsize),
            "dpi": self.dpi,
            "basemap": basemap,
            "layers": [
                {
                    "name": layer.name,
                    "role": layer.role.value,
                    "style": self._effective_style(layer),
                    "removed_area_m2": layer.removed_area_m2,
                    "removed_area_percent": layer.removed_area_percent,
                    "hard": layer.is_hard_constraint(),
                }
                for layer in layers
            ],
        }


def render_map(
    layers: list[Layer],
    *,
    crs: str = "EPSG:2180",
    fmt: Fmt = "png",
    basemap: bool = False,
    title: str | None = None,
    caption: str | None = None,
    simplify_tolerance: float | None = None,
) -> RenderResult:
    """Module-level convenience wrapper around :class:`MapRenderer` (Phase 3 §3.1)."""
    return MapRenderer().render_map(
        layers,
        crs=crs,
        fmt=fmt,
        basemap=basemap,
        title=title,
        caption=caption,
        simplify_tolerance=simplify_tolerance,
    )


# Re-exported so callers can build a GeometryCollection sentinel without importing
# shapely directly (keeps the render package self-describing for stubs/tests).
__all__ = [
    "MapRenderer",
    "RenderResult",
    "render_map",
    "RENDERER_VERSION",
    "GeometryCollection",
]
