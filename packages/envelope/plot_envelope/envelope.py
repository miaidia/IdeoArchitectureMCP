"""Buildable envelope v1 (Phase 7 §7.1.3 / §7.5).

``buildable_envelope_v1(parcel, no_build, soft_setbacks) -> BuildableEnvelope``:

* buildable polygon = ``parcel − (union of no-build zones) − (union of soft setbacks)``;
* adds the **largest inscribed rectangle** (``plot_geo.largest_inscribed_rectangle``,
  Phase 5);
* ranks **confidence** by the source ``legal_status`` / ``geometry_precision`` of the
  constraints that shaped it (§7.5 "rank buildable envelope confidence by source type");
* records a **trace** in ``metadata`` of which constraint removed which area (m² / %),
  so the Phase 3 renderer caption can show it (§30).

Pure geometry + already-computed constraints; no network, no ``plot_connectors`` (§9.4).
"""

from __future__ import annotations

import uuid

import plot_geo
from plot_domain import (
    BuildableEnvelope,
    Constraint,
    GeometryPrecision,
    LegalStatus,
    NoBuildZone,
    confidence_components,
)
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

# Confidence weights for the envelope (§7.5: rank by source type). A binding, survey-grade
# constraint set yields a high-confidence envelope; informative/approximate sources lower it.
_LEGAL_WEIGHT: dict[LegalStatus, float] = {
    LegalStatus.BINDING: 1.0,
    LegalStatus.INFORMATIVE: 0.75,
    LegalStatus.AUXILIARY: 0.5,
    LegalStatus.UNKNOWN: 0.4,
}
_PRECISION_WEIGHT: dict[GeometryPrecision, float] = {
    GeometryPrecision.SURVEY: 1.0,
    GeometryPrecision.CADASTRAL: 0.95,
    GeometryPrecision.TOPOGRAPHIC: 0.8,
    GeometryPrecision.RASTER_DERIVED: 0.65,
    GeometryPrecision.APPROXIMATE: 0.5,
    GeometryPrecision.UNKNOWN: 0.45,
}


def _geom(obj: NoBuildZone | Constraint) -> BaseGeometry | None:
    if obj.geometry is None:
        return None
    g = shape(obj.geometry)
    return None if g.is_empty else g


def envelope_confidence_components(
    constraints: list[Constraint],
    precision_by_source: dict[str, GeometryPrecision] | None = None,
) -> dict[str, float] | None:
    """The §25.1 component decomposition behind :func:`envelope_confidence`.

    Phase 16 (§25 calibration): the same min-per-constraint inputs the legacy
    scalar blends are emitted as named components so reports/audits can show
    WHY the envelope confidence is what it is:

    * ``source_authority``   — min legal-status weight of the shaping constraints;
    * ``geometry_precision`` — min geometry-precision weight of their sources;
    * ``semantic_precision`` — min per-constraint confidence (content ambiguity).

    ``None`` when there are no constraints (no measurable components — the
    scalar falls back to its documented moderate 0.6).
    """
    precision_by_source = precision_by_source or {}
    if not constraints:
        return None
    return {
        "source_authority": min(
            _LEGAL_WEIGHT.get(c.source_legal_status, 0.4) for c in constraints
        ),
        "geometry_precision": min(
            _PRECISION_WEIGHT.get(
                precision_by_source.get(c.source_id, GeometryPrecision.UNKNOWN), 0.45
            )
            for c in constraints
        ),
        "semantic_precision": min(c.confidence for c in constraints),
    }


def envelope_confidence(
    constraints: list[Constraint],
    precision_by_source: dict[str, GeometryPrecision] | None = None,
) -> float:
    """Confidence of the envelope from the constraints that shaped it (§7.5).

    Heuristic v1: the envelope confidence is the *minimum* per-constraint confidence
    (a chain is as strong as its weakest link) blended with the source legal-status /
    geometry-precision weights. With no constraints at all, confidence is moderate (0.6):
    the parcel geometry alone is known, but the absence of constraint data is itself an
    uncertainty (it may simply be that sources were not checked / unavailable).
    """
    parts = envelope_confidence_components(constraints, precision_by_source)
    if parts is None:
        return 0.6
    legal_w = parts["source_authority"]
    prec_w = parts["geometry_precision"]
    conf_w = parts["semantic_precision"]
    value = round(min(1.0, max(0.0, 0.5 * conf_w + 0.25 * legal_w + 0.25 * prec_w)), 4)
    return value


def buildable_envelope_v1(
    parcel_geom: BaseGeometry,
    *,
    no_build: list[NoBuildZone],
    soft_setbacks: list[Constraint] | None = None,
    constraints: list[Constraint] | None = None,
    precision_by_source: dict[str, GeometryPrecision] | None = None,
    analysis_id: str | None = None,
) -> BuildableEnvelope:
    """Compute the v1 buildable envelope (§7.1.3 / §7.5).

    Parameters
    ----------
    parcel_geom:
        Parcel polygon in EPSG:2180.
    no_build:
        Hard no-build zones to subtract (from ``nobuild.no_build_zones``).
    soft_setbacks:
        Soft constraints to ALSO subtract for the envelope footprint (e.g. road/forest
        setback strips). They are advisory but a realistic envelope respects them; they
        are still surfaced as soft constraints/risks elsewhere. Defaults to none.
    constraints:
        The full constraint list, used only to rank envelope confidence (§7.5).
    precision_by_source:
        Optional ``source_id -> geometry_precision`` map to refine confidence ranking.
    """
    soft_setbacks = soft_setbacks or []
    constraints = constraints or []

    parcel_area = float(parcel_geom.area)
    buildable: BaseGeometry = parcel_geom
    trace: list[dict[str, object]] = []

    def _subtract(geom: BaseGeometry | None, *, label: str, kind: str, ref: str | None) -> None:
        nonlocal buildable
        if geom is None or geom.is_empty:
            return
        before = float(buildable.area)
        buildable = plot_geo.difference(buildable, geom)
        after = float(buildable.area)
        removed = max(0.0, before - after)
        if removed <= 0.0:
            return
        trace.append(
            {
                "label": label,
                "kind": kind,  # 'no_build' | 'soft_setback'
                "ref": ref,
                "removed_m2": round(removed, 3),
                "removed_percent": round((removed / parcel_area * 100.0) if parcel_area else 0.0, 3),
            }
        )

    for zone in no_build:
        _subtract(_geom(zone), label=zone.reason, kind="no_build", ref=zone.source_constraint_id)
    for con in soft_setbacks:
        _subtract(
            _geom(con),
            label=con.constraint_type,
            kind="soft_setback",
            ref=con.constraint_id,
        )

    buildable_area = float(buildable.area)
    # Largest inscribed rectangle (Phase 5) — only meaningful for a non-trivial polygon.
    lir_geojson = None
    if buildable_area > 0.0 and not buildable.is_empty:
        try:
            lir = plot_geo.largest_inscribed_rectangle(_largest_polygon(buildable))
            if lir is not None and not lir.is_empty:
                lir_geojson = mapping(lir)
        except (ValueError, ZeroDivisionError):
            # Degenerate sliver — no inscribed rectangle; not an error, just no LIR.
            lir_geojson = None

    confidence = envelope_confidence(constraints, precision_by_source)
    # Phase 16 (§25.1): the audited component decomposition + the composite the
    # calibration model assigns to those components. The legacy scalar stays the
    # headline value (snapshot stability); the components explain it.
    components = envelope_confidence_components(constraints, precision_by_source)
    composite_block: dict[str, object] | None = None
    if components is not None:
        composite_block = confidence_components(**components).to_dict()

    return BuildableEnvelope(
        id=f"env:{uuid.uuid4().hex[:8]}",
        analysis_id=analysis_id,
        geometry=mapping(buildable) if not buildable.is_empty else None,
        largest_inscribed_rectangle=lir_geojson,
        area_m2=round(buildable_area, 3),
        confidence=confidence,
        metadata={
            "method": "v1_parcel_minus_setbacks_minus_no_build",
            "parcel_area_m2": round(parcel_area, 3),
            "buildable_percent": round((buildable_area / parcel_area * 100.0) if parcel_area else 0.0, 3),
            # area-attribution trace: which constraint removed which area (§30 caption).
            "removed_by": trace,
            "confidence_basis": "ranked by source legal_status + geometry_precision (§7.5)",
            # §25.1 decomposition (Phase 16): None when no constraint shaped the
            # envelope (the scalar's documented 0.6 fallback is itself the story).
            "confidence_components": composite_block,
        },
    )


def _largest_polygon(geom: BaseGeometry) -> BaseGeometry:
    """Return the largest single polygon component (LIR needs a single Polygon)."""
    if geom.geom_type == "Polygon":
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    if not parts:
        return geom
    return max(parts, key=lambda g: g.area)
