"""Design-brief generator (Phase 11 §11.1.1 — Target-workflow step 4).

:func:`generate_design_brief` turns the analysis outputs the previous phases computed
(parcel geometry, buildable envelope, MPZP indicators, ruleset registry, heritage +
context inputs) into a structured, model-readable :class:`DesignBrief`:

* **composition axes** — straight skeleton of the parcel polygon via
  ``py-straight-skeleton`` (BSD 3-Clause, verified at install — §0v2.3), with a
  medial-axis fallback via ``shapely.voronoi_polygons`` (the v1 Phase 0.4 approach,
  mirroring ``plot_geo.metrics``) whenever the skeleton library fails;
* **frontage & orientation** — every boundary edge with length/azimuth/outward normal,
  a south-exposure score (normal pointing 135°–225°) and a context tag from the
  caller-provided ``context_edges`` (noise = kolej/droga_publiczna, quiet =
  woda/zielen);
* **heritage** — footprints echoed with the fixed retention note (status
  ``zabytek_do_remontu`` in proposals);
* **buildable zones** — envelope area / largest inscribed rectangle / % of parcel;
* **indicators + hard rules in force** — indicator echo with the missing-list, and the
  registry's hard rules as id + title one-liners (titles come FROM the YAML — no legal
  value is restated in code);
* **recommended typologies** — ranked design-practice suggestions from
  :mod:`plot_planning.typologies` (suggestions, never validators — plan §11.4).

Location decision (Phase 11 part A, documented per the task): the brief lives in
``plot_planning`` (not ``plot_agent``) because every input is available at or below
this layer — envelope as the :class:`plot_domain.BuildableEnvelope` model, indicators
and typologies from this package, rules via ``plot_rules``, geometry metrics via the
new downward ``plot_geo`` dep — and both the MCP resource layer and (later) reports
can consume it without pulling in the agent orchestration layer. ``plot_agent``
already depends on ``plot_planning``, so part B's drawing-prompt assembly imports it
for free.

The module also hosts :class:`DesignBriefStore` (process-local, same pattern as
``plot_agent.drawing.variants`` / ``plot_planning.store``) backing the
``analysis://{analysis_id}/design-brief`` MCP resource.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import plot_geo
import shapely
from plot_domain import BuildableEnvelope
from plot_rules import RulesetRegistry
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import LineString, MultiLineString, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from plot_planning.parser import INDICATOR_NAMES
from plot_planning.typologies import (
    TYPOLOGY_CATEGORY,
    TypologyRecommendation,
    recommend_typologies,
)

#: Fixed retention note echoed for every heritage footprint (plan §11.1.1).
HERITAGE_NOTE = "do zachowania — status zabytek_do_remontu w propozycji"

#: Context kinds of ``context_edges`` entries and their acoustic character
#: (design-practice classification, not a legal value).
CONTEXT_CHARACTER: dict[str, str] = {
    "kolej": "noise",
    "droga_publiczna": "noise",
    "woda": "quiet",
    "zielen": "quiet",
    "zabudowa": "neutral",
}


@dataclass(frozen=True)
class BriefConfig:
    """Tunables of the brief generator (design-practice values, basis marker below).

    ``basis: design_practice`` — these are presentation/derivation knobs (south window
    width, axis grouping, matching tolerances), NOT legal thresholds; legal content
    enters the brief only as rule ids/titles read from the ruleset registry.
    """

    # South exposure: outward normal within 180° ± south_halfwidth_deg scores > 0
    # (the task's 135°–225° window), linearly peaking at due south.
    south_center_deg: float = 180.0
    south_halfwidth_deg: float = 45.0
    # Context-edge matching: a boundary edge is tagged with a context kind when at
    # least `context_min_share` of its length lies within `context_tolerance_m` of
    # the context geometry.
    context_tolerance_m: float = 10.0
    context_min_share: float = 0.5
    # Axis extraction: spine segments are grouped into orientation bins (mod 180°);
    # a non-dominant bin becomes a secondary axis when it carries at least
    # `secondary_axis_min_share` of the total spine length.
    axis_bin_deg: float = 15.0
    secondary_axis_min_share: float = 0.15
    # Medial-axis fallback densification (fraction of the bbox diagonal).
    medial_samples: int = 80
    basis: str = "design_practice"


# --------------------------------------------------------------------------- #
# Brief sub-models
# --------------------------------------------------------------------------- #
class ParcelSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_m2: float
    perimeter_m: float
    shape_class: str
    width_min_m: float
    width_median_m: float
    width_max_m: float
    srodmiejska: bool


class AxisInfo(BaseModel):
    """Composition axes from the parcel skeleton (plan §11.1.1)."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["straight_skeleton", "medial_axis_voronoi", "main_axis_fallback"]
    spine: dict[str, Any] | None = Field(
        default=None, description="GeoJSON (Multi)LineString of the interior spine."
    )
    spine_length_m: float = 0.0
    dominant_azimuth_deg: float = Field(
        description="Dominant axis orientation, degrees from north in [0, 180)."
    )
    secondary_azimuths_deg: list[float] = Field(default_factory=list)
    main_axis_azimuth_deg: float = Field(
        description="plot_geo.main_axis azimuth (minimum-rotated-rectangle cross-check)."
    )


class FrontageEdge(BaseModel):
    """One classified boundary edge (frontage & orientation analysis)."""

    model_config = ConfigDict(extra="forbid")

    geometry: dict[str, Any]
    length_m: float
    azimuth_deg: float = Field(description="Edge direction, degrees from north.")
    normal_azimuth_deg: float = Field(description="OUTWARD normal, degrees from north.")
    south_exposure_score: float = Field(ge=0.0, le=1.0)
    context: str | None = Field(
        default=None, description="droga_publiczna|kolej|woda|zielen|zabudowa or None."
    )
    character: Literal["noise", "quiet", "neutral", "unknown"] = "unknown"


class HeritageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geometry: dict[str, Any]
    name: str | None = None
    note: str = HERITAGE_NOTE


class BuildableSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope_area_m2: float | None = None
    largest_rectangle_m2: float | None = None
    percent_of_parcel: float | None = None
    confidence: float | None = None


class RuleInForce(BaseModel):
    """One hard rule in force: id + YAML title as the one-line threshold summary."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    title: str
    category: str
    severity: str
    source_reference: str | None = None


class DesignBrief(BaseModel):
    """Structured design brief (Pydantic for structuredContent; see to_markdown())."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str | None = None
    parcel: ParcelSummary
    axes: AxisInfo
    frontages: list[FrontageEdge] = Field(default_factory=list)
    heritage: list[HeritageItem] = Field(default_factory=list)
    buildable: BuildableSummary
    indicators: dict[str, Any] = Field(default_factory=dict)
    missing_indicators: list[str] = Field(default_factory=list)
    rules_in_force: list[RuleInForce] = Field(default_factory=list)
    typologies: list[TypologyRecommendation] = Field(default_factory=list)
    ruleset_version: str = ""
    config_basis: str = "design_practice"

    # ------------------------------------------------------------------ #
    def to_markdown(self) -> str:
        """Render the model-readable Markdown brief (plan §11.1.1)."""
        L: list[str] = []
        suffix = f" — analiza {self.analysis_id}" if self.analysis_id else ""
        L.append(f"# Design brief{suffix}")
        L.append("")
        p = self.parcel
        L.append("## 1. Działka")
        L.append(f"- powierzchnia: {p.area_m2:.0f} m² | obwód: {p.perimeter_m:.0f} m")
        L.append(f"- klasa kształtu: **{p.shape_class}**")
        L.append(
            f"- szerokość (oś medialna): min {p.width_min_m:.1f} m / "
            f"mediana {p.width_median_m:.1f} m / max {p.width_max_m:.1f} m"
        )
        L.append(f"- zabudowa śródmiejska: {'tak' if p.srodmiejska else 'nie'}")
        L.append("")
        a = self.axes
        L.append(f"## 2. Osie kompozycyjne ({a.method})")
        L.append(f"- oś dominująca: azymut **{a.dominant_azimuth_deg:.0f}°** (mod 180°)")
        if a.secondary_azimuths_deg:
            secondary = ", ".join(f"{az:.0f}°" for az in a.secondary_azimuths_deg)
            L.append(f"- osie drugorzędne: {secondary}")
        L.append(
            f"- długość szkieletu: {a.spine_length_m:.0f} m; "
            f"oś główna obrysu (kontrola): {a.main_axis_azimuth_deg:.0f}°"
        )
        L.append("")
        L.append("## 3. Pierzeje i orientacja")
        L.append("| # | długość [m] | azymut | normalna | ekspozycja płd. | kontekst | charakter |")
        L.append("|---|---|---|---|---|---|---|")
        for i, e in enumerate(self.frontages, start=1):
            L.append(
                f"| {i} | {e.length_m:.0f} | {e.azimuth_deg:.0f}° | "
                f"{e.normal_azimuth_deg:.0f}° | {e.south_exposure_score:.2f} | "
                f"{e.context or '—'} | {e.character} |"
            )
        best_south = max(self.frontages, key=lambda e: e.south_exposure_score, default=None)
        if best_south is not None and best_south.south_exposure_score > 0:
            L.append(
                f"- najlepsza ekspozycja południowa: krawędź "
                f"{self.frontages.index(best_south) + 1} "
                f"(normalna {best_south.normal_azimuth_deg:.0f}°, "
                f"score {best_south.south_exposure_score:.2f})"
            )
        noisy = [i + 1 for i, e in enumerate(self.frontages) if e.character == "noise"]
        quiet = [i + 1 for i, e in enumerate(self.frontages) if e.character == "quiet"]
        if noisy:
            L.append(f"- krawędzie hałaśliwe (kolej/droga): {noisy}")
        if quiet:
            L.append(f"- krawędzie ciche (woda/zieleń): {quiet}")
        L.append("")
        L.append("## 4. Obiekty zabytkowe do zachowania")
        if self.heritage:
            for h in self.heritage:
                label = h.name or "obiekt"
                L.append(f"- {label}: {h.note}")
        else:
            L.append("- brak zgłoszonych obiektów zabytkowych")
        L.append("")
        b = self.buildable
        L.append("## 5. Strefa zabudowy (buildable envelope)")
        if b.envelope_area_m2 is not None:
            L.append(
                f"- powierzchnia: {b.envelope_area_m2:.0f} m² "
                f"({(b.percent_of_parcel or 0):.0f}% działki)"
            )
            if b.largest_rectangle_m2 is not None:
                L.append(f"- największy wpisany prostokąt: {b.largest_rectangle_m2:.0f} m²")
            if b.confidence is not None:
                L.append(f"- confidence: {b.confidence:.2f}")
        else:
            L.append("- brak policzonej strefy (uruchom constraints_compute)")
        L.append("")
        L.append("## 6. Wskaźniki planistyczne (MPZP/WZ)")
        known = {k: v for k, v in self.indicators.items() if v is not None}
        if known:
            for k, v in sorted(known.items()):
                L.append(f"- {k}: {v}")
        else:
            L.append("- brak wyekstrahowanych wskaźników")
        if self.missing_indicators:
            L.append(f"- **nieznane (do ustalenia, nigdy domyślne):** {self.missing_indicators}")
        L.append("")
        L.append("## 7. Twarde reguły w mocy")
        for r in self.rules_in_force:
            src = f" ({r.source_reference})" if r.source_reference else ""
            L.append(f"- `{r.rule_id}` — {r.title}{src}")
        if not self.rules_in_force:
            L.append("- brak załadowanych reguł twardych (sprawdź rulesets/)")
        L.append("")
        L.append("## 8. Rekomendowane typologie (sugestie projektowe, nie walidatory)")
        for i, t in enumerate(self.typologies, start=1):
            L.append(f"{i}. **{t.title}** (score {t.score:.2f}, basis: {t.basis})")
            for why in t.why:
                L.append(f"   - {why}")
            if t.parameters:
                L.append(f"   - parametry: {t.parameters}")
        if not self.typologies:
            L.append("- brak rekomendacji (brak dokumentów typologii w rulesets/PL/typologies)")
        L.append("")
        L.append(f"_ruleset_version: {self.ruleset_version}; config basis: {self.config_basis}_")
        return "\n".join(L)


# --------------------------------------------------------------------------- #
# Composition axes — straight skeleton with medial-axis fallback
# --------------------------------------------------------------------------- #
def _largest_polygon(geom: BaseGeometry) -> Polygon | None:
    if isinstance(geom, Polygon):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
    return max(parts, key=lambda g: g.area) if parts else None


def _skeleton_spine(poly: Polygon) -> list[LineString]:
    """Interior straight-skeleton arcs of *poly* via ``py-straight-skeleton``.

    API verified against the installed package source
    (.venv/.../py_straight_skeleton/__init__.py + skeleton.py):
    ``compute_skeleton(exterior, holes)`` takes the exterior ring COUNTER-CLOCKWISE
    and holes CLOCKWISE (both without the closing vertex) and returns a ``Skeleton``
    whose ``arc_iterator()`` yields ``(SkeletonNode, SkeletonNode)`` pairs; each node
    carries ``.position`` (x/y) and ``.time`` = its offset distance from the original
    boundary, so arcs whose BOTH endpoints have time > 0 are the interior spine
    (ridge), while time-0 endpoints are the original polygon vertices.

    Robustness (verified experimentally in part A): collinear boundary vertices crash
    the library with ``DegenerateLAVError`` ("All vertices are overlapping"), so the
    polygon is pre-cleaned with ``simplify(0)`` (removes collinear points only);
    ``shapely.geometry.polygon.orient(sign=1.0)`` yields exactly the CCW-exterior /
    CW-interior orientation the library expects. Any remaining failure raises and the
    caller falls back to the medial axis.
    """
    from py_straight_skeleton import compute_skeleton

    cleaned = poly.simplify(0)
    if not isinstance(cleaned, Polygon) or cleaned.is_empty:
        cleaned = poly
    oriented = orient(cleaned, sign=1.0)
    exterior = [(float(x), float(y)) for x, y in oriented.exterior.coords[:-1]]
    holes = [
        [(float(x), float(y)) for x, y in ring.coords[:-1]] for ring in oriented.interiors
    ]
    skeleton = compute_skeleton(exterior, holes)
    eps = 1e-9
    spine: list[LineString] = []
    for a, b in skeleton.arc_iterator():
        if a.time > eps and b.time > eps:
            seg = LineString(
                [(float(a.position.x), float(a.position.y)),
                 (float(b.position.x), float(b.position.y))]
            )
            if seg.length > eps:
                spine.append(seg)
    return spine


def _medial_spine(poly: Polygon, *, samples: int) -> list[LineString]:
    """Medial-axis fallback via Voronoi of the densified boundary (v1 Phase 0.4).

    Mirrors ``plot_geo.metrics._medial_points`` (shapely.voronoi_polygons of the
    segmentized boundary) but keeps the Voronoi EDGES fully inside the polygon as
    spine segments instead of sampling midpoint widths.
    """
    boundary = poly.boundary
    minx, miny, maxx, maxy = poly.bounds
    diag = math.hypot(maxx - minx, maxy - miny)
    seg_len = max(diag / max(samples, 1), 1e-3)
    dense = shapely.segmentize(boundary, seg_len)
    edges = shapely.voronoi_polygons(dense, only_edges=True)
    lines: list[LineString]
    if isinstance(edges, MultiLineString):
        lines = list(edges.geoms)
    elif isinstance(edges, LineString):
        lines = [edges]
    else:
        lines = [g for g in getattr(edges, "geoms", []) if isinstance(g, LineString)]
    shrunk = poly.buffer(-seg_len * 0.25)
    region = shrunk if not shrunk.is_empty else poly
    return [ln for ln in lines if ln.length > 0 and region.contains(ln)]


def _axial_mean_deg(segments: Sequence[tuple[float, float]]) -> float:
    """Length-weighted axial mean orientation (degrees in [0, 180))."""
    sx = sum(length * math.cos(2.0 * math.radians(az)) for az, length in segments)
    sy = sum(length * math.sin(2.0 * math.radians(az)) for az, length in segments)
    if sx == 0.0 and sy == 0.0:
        return segments[0][0] if segments else 0.0
    return (math.degrees(math.atan2(sy, sx)) / 2.0) % 180.0


def _segment_orientation_deg(seg: LineString) -> float:
    (x0, y0), (x1, y1) = seg.coords[0], seg.coords[-1]
    return (math.degrees(math.atan2(x1 - x0, y1 - y0))) % 180.0


def _axes_from_spine(
    spine: Sequence[LineString], config: BriefConfig
) -> tuple[float, list[float]]:
    """Dominant + secondary axis azimuths from spine segments (orientation bins).

    Deterministic binning: orientations (mod 180°) are bucketed into
    ``axis_bin_deg`` bins weighted by segment length; the heaviest bin's axial mean
    is the dominant axis, and every other bin carrying at least
    ``secondary_axis_min_share`` of the total length contributes a secondary axis.
    """
    weighted = [(_segment_orientation_deg(s), float(s.length)) for s in spine if s.length > 0]
    if not weighted:
        return 0.0, []
    nbins = max(1, int(round(180.0 / config.axis_bin_deg)))
    bins: dict[int, list[tuple[float, float]]] = {}
    for az, length in weighted:
        bins.setdefault(int(az // config.axis_bin_deg) % nbins, []).append((az, length))
    total = sum(length for _, length in weighted)
    bin_weight = {k: sum(length for _, length in v) for k, v in bins.items()}
    dominant_bin = max(bin_weight, key=lambda k: (bin_weight[k], -k))
    dominant = _axial_mean_deg(bins[dominant_bin])
    secondary = [
        _axial_mean_deg(bins[k])
        for k in sorted(bin_weight, key=lambda k: -bin_weight[k])
        if k != dominant_bin and bin_weight[k] / total >= config.secondary_axis_min_share
    ]
    return dominant, [round(az, 2) for az in secondary]


def composition_axes(parcel_geom: BaseGeometry, *, config: BriefConfig | None = None) -> AxisInfo:
    """Composition axes of a parcel: skeleton spine + dominant/secondary azimuths.

    Tries the straight skeleton first; on ANY library failure (or an empty interior
    spine, e.g. a perfect square collapsing to one centre node) falls back to the
    medial axis (documented above) and finally to ``plot_geo.main_axis``.
    """
    cfg = config or BriefConfig()
    main = plot_geo.main_axis(parcel_geom)
    poly = _largest_polygon(parcel_geom)
    if poly is None or poly.is_empty:
        return AxisInfo(
            method="main_axis_fallback",
            spine=dict(mapping(main.line)),
            spine_length_m=round(main.length_m, 2),
            dominant_azimuth_deg=round(main.azimuth_deg % 180.0, 2),
            secondary_azimuths_deg=[],
            main_axis_azimuth_deg=round(main.azimuth_deg % 180.0, 2),
        )

    method: Literal["straight_skeleton", "medial_axis_voronoi", "main_axis_fallback"] = (
        "straight_skeleton"
    )
    try:
        spine = _skeleton_spine(poly)
    except Exception:  # noqa: BLE001 — documented fallback: the skeleton lib throws
        # DegenerateLAVError and kin on degenerate inputs; the medial axis (shapely
        # voronoi, §0v2.3 fallback) must keep the brief available for ANY polygon.
        spine = []
        method = "medial_axis_voronoi"
    if not spine:
        if method == "straight_skeleton":
            method = "medial_axis_voronoi"
        spine = _medial_spine(poly, samples=cfg.medial_samples)
    if not spine:
        method = "main_axis_fallback"
        spine = [main.line]

    dominant, secondary = _axes_from_spine(spine, cfg)
    merged = shapely.line_merge(MultiLineString([list(s.coords) for s in spine]))
    return AxisInfo(
        method=method,
        spine=dict(mapping(merged)),
        spine_length_m=round(sum(s.length for s in spine), 2),
        dominant_azimuth_deg=round(dominant, 2),
        secondary_azimuths_deg=secondary,
        main_axis_azimuth_deg=round(main.azimuth_deg % 180.0, 2),
    )


# --------------------------------------------------------------------------- #
# Frontage / orientation analysis
# --------------------------------------------------------------------------- #
def _to_geom(obj: Any) -> BaseGeometry:
    if isinstance(obj, BaseGeometry):
        return obj
    if isinstance(obj, Mapping):
        if obj.get("type") == "Feature":
            return shape(obj["geometry"])
        return shape(obj)
    raise TypeError(f"Unsupported geometry: {type(obj)!r}")


def _south_score(normal_az: float, config: BriefConfig) -> float:
    gap = abs((normal_az - config.south_center_deg + 180.0) % 360.0 - 180.0)
    if gap >= config.south_halfwidth_deg:
        return 0.0
    return round(1.0 - gap / config.south_halfwidth_deg, 4)


def analyze_frontages(
    parcel_geom: BaseGeometry,
    context_edges: Sequence[Mapping[str, Any]] = (),
    *,
    config: BriefConfig | None = None,
) -> list[FrontageEdge]:
    """Classify every boundary edge of the parcel (plan §11.1.1).

    Edges come from the collinear-simplified exterior ring oriented CCW, so the
    OUTWARD normal of edge vector ``(dx, dy)`` is ``(dy, -dx)``. Context kinds are
    matched by length share within ``context_tolerance_m`` (same buffer idiom as
    ``plot_geo.classify_boundary_edges``, but per-edge and with the Phase 11 kind
    vocabulary droga_publiczna/kolej/woda/zielen/zabudowa).
    """
    cfg = config or BriefConfig()
    poly = _largest_polygon(parcel_geom)
    if poly is None or poly.is_empty:
        return []
    cleaned = poly.simplify(0)
    if not isinstance(cleaned, Polygon) or cleaned.is_empty:
        cleaned = poly
    ring = orient(cleaned, sign=1.0).exterior
    coords = list(ring.coords)

    # Pre-buffer the context geometries once.
    zones: list[tuple[str, BaseGeometry]] = []
    for entry in context_edges:
        kind = str(entry.get("kind", "")).strip()
        if kind not in CONTEXT_CHARACTER:
            continue
        geom = _to_geom(entry["geometry"])
        if not geom.is_empty:
            zones.append((kind, geom.buffer(cfg.context_tolerance_m)))

    edges: list[FrontageEdge] = []
    for (x0, y0), (x1, y1) in zip(coords, coords[1:], strict=False):
        seg = LineString([(x0, y0), (x1, y1)])
        if seg.length <= 1e-9:
            continue
        dx, dy = x1 - x0, y1 - y0
        azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
        normal_az = math.degrees(math.atan2(dy, -dx)) % 360.0  # outward for CCW ring
        best_kind: str | None = None
        best_share = 0.0
        for kind, zone in zones:
            share = float(seg.intersection(zone).length) / float(seg.length)
            if share >= cfg.context_min_share and share > best_share:
                best_kind, best_share = kind, share
        edges.append(
            FrontageEdge(
                geometry=dict(mapping(seg)),
                length_m=round(float(seg.length), 2),
                azimuth_deg=round(azimuth, 2),
                normal_azimuth_deg=round(normal_az, 2),
                south_exposure_score=_south_score(normal_az, cfg),
                context=best_kind,
                character=CONTEXT_CHARACTER.get(best_kind or "", "unknown"),
            )
        )
    return edges


# --------------------------------------------------------------------------- #
# Brief generation
# --------------------------------------------------------------------------- #
def _rules_in_force(registry: RulesetRegistry) -> list[RuleInForce]:
    """Hard rules currently in force: id + YAML title (values stay in the YAML).

    Excludes the ``typologies`` category (suggestions, never rules — plan §11.4) and
    rules already closed by ``valid_to`` (e.g. the retired Phase 2 exemplar).
    """
    out = [
        RuleInForce(
            rule_id=r.id,
            title=r.title,
            category=r.category,
            severity=r.severity,
            source_reference=r.source_reference,
        )
        for r in registry.rules
        if r.severity == "hard" and r.category != TYPOLOGY_CATEGORY and r.valid_to is None
    ]
    out.sort(key=lambda r: r.rule_id)
    return out


def generate_design_brief(
    parcel_geom: BaseGeometry,
    *,
    registry: RulesetRegistry,
    envelope: BuildableEnvelope | None = None,
    indicators: Mapping[str, Any] | None = None,
    heritage_footprints: Sequence[Mapping[str, Any]] = (),
    context_edges: Sequence[Mapping[str, Any]] = (),
    srodmiejska: bool = False,
    config: BriefConfig | None = None,
    analysis_id: str | None = None,
) -> DesignBrief:
    """Generate the structured design brief (Phase 11 §11.1.1; pure, no I/O).

    ``heritage_footprints`` entries are GeoJSON geometries or Features (an optional
    ``properties.name`` is echoed); ``context_edges`` entries are
    ``{"geometry": <GeoJSON>, "kind": droga_publiczna|kolej|woda|zielen|zabudowa}``.
    """
    cfg = config or BriefConfig()
    ind = dict(indicators or {})

    width = plot_geo.width_profile(parcel_geom)
    parcel = ParcelSummary(
        area_m2=round(plot_geo.area_m2(parcel_geom), 2),
        perimeter_m=round(plot_geo.perimeter_m(parcel_geom), 2),
        shape_class=plot_geo.shape_class(parcel_geom),
        width_min_m=round(width.min_width_m, 2),
        width_median_m=round(width.median_width_m, 2),
        width_max_m=round(width.max_width_m, 2),
        srodmiejska=bool(srodmiejska or ind.get("zabudowa_srodmiejska") is True),
    )

    axes = composition_axes(parcel_geom, config=cfg)
    frontages = analyze_frontages(parcel_geom, context_edges, config=cfg)

    heritage: list[HeritageItem] = []
    for item in heritage_footprints:
        geom = _to_geom(item)
        name = None
        if isinstance(item, Mapping) and item.get("type") == "Feature":
            name = (item.get("properties") or {}).get("name")
        heritage.append(HeritageItem(geometry=dict(mapping(geom)), name=name))

    buildable = BuildableSummary()
    if envelope is not None and envelope.area_m2 is not None:
        lir_m2: float | None = None
        if envelope.largest_inscribed_rectangle:
            lir_m2 = round(float(shape(envelope.largest_inscribed_rectangle).area), 2)
        buildable = BuildableSummary(
            envelope_area_m2=envelope.area_m2,
            largest_rectangle_m2=lir_m2,
            percent_of_parcel=(
                round(envelope.area_m2 / parcel.area_m2 * 100.0, 2)
                if parcel.area_m2 > 0
                else None
            ),
            confidence=envelope.confidence,
        )

    missing = [name for name in INDICATOR_NAMES if ind.get(name) is None]

    road_frontage_m = sum(e.length_m for e in frontages if e.context == "droga_publiczna")
    typologies = recommend_typologies(
        parcel.shape_class,
        ind,
        parcel.srodmiejska,
        {
            "width_m": parcel.width_median_m,
            "area_m2": parcel.area_m2,
            "frontage_m": road_frontage_m or None,
        },
        registry=registry,
    )

    return DesignBrief(
        analysis_id=analysis_id,
        parcel=parcel,
        axes=axes,
        frontages=frontages,
        heritage=heritage,
        buildable=buildable,
        indicators=ind,
        missing_indicators=missing,
        rules_in_force=_rules_in_force(registry),
        typologies=typologies,
        ruleset_version=registry.ruleset_version,
        config_basis=cfg.basis,
    )


# --------------------------------------------------------------------------- #
# Process-local brief store (MCP resource backing — variants-store pattern)
# --------------------------------------------------------------------------- #
class DesignBriefStore:
    """In-memory ``analysis_id -> DesignBrief`` store (process-local).

    Same pattern as ``plot_agent.drawing.variants.MasterplanVariantStore`` /
    ``plot_planning.store.PlanningStore`` — in-memory until the Phase 12/13
    persistence layer; backs ``analysis://{analysis_id}/design-brief``.
    """

    def __init__(self) -> None:
        self._briefs: dict[str, DesignBrief] = {}

    def put(self, analysis_id: str, brief: DesignBrief) -> None:
        self._briefs[analysis_id] = brief

    def get(self, analysis_id: str) -> DesignBrief | None:
        return self._briefs.get(analysis_id)

    def __len__(self) -> int:
        return len(self._briefs)


#: Default process-local store used by the MCP use-case layer + resource.
DEFAULT_BRIEF_STORE = DesignBriefStore()
