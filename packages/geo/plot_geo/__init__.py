"""Geometry, CRS, overlays, and shape metrics for the Plot Analyzer (Phase 5).

This package is the spatial core (base_assumptions §13, F-0014–0040, §8.6). All metric
operations run in the analytical CRS **EPSG:2180** (§9.4 / §26.3). It depends only on
``plot_domain`` / ``plot_shared`` for shared types — never on ``plot_connectors`` or
``plot_rules`` (Phase 1.4 decoupling rule).
"""

from __future__ import annotations

from .crs import (
    ANALYTICAL_CRS,
    GEOJSON_DEFAULT_CRS,
    detect_crs,
    is_geographic,
    to_analytical,
    transform,
)
from .inscribed import (
    inscribed_circle_center,
    largest_inscribed_rectangle,
    largest_orthogonal_polygon,
    maximum_inscribed_circle,
)
from .metrics import (
    BoundaryEdge,
    Frontage,
    MainAxis,
    NeighborType,
    UsableArea,
    WidthProfile,
    area_m2,
    classify_boundary_edges,
    compactness,
    convexity,
    detect_frontage,
    frontage,
    frontage_azimuth,
    irregularity,
    is_corner,
    main_axis,
    narrowest_passage,
    nearest_boundary_distance,
    perimeter_m,
    shape_class,
    usable_area,
    width_profile,
)
from .overlay import (
    buffer_m,
    clip,
    difference,
    dissolve,
    distance_to_layer,
    intersection,
    nearest,
    nearest_point_pair,
    simplify_topo,
    symmetric_difference,
    union,
)
from .parts import (
    component_count,
    explode,
    is_multipart,
    merge_parcels,
    merge_with_components,
    split_to_components,
)
from .snapshot import GeometrySnapshot, input_hash
from .validate import (
    QualityReport,
    UncertainGeometryFlag,
    assess_quality,
    explain_invalidity,
    is_valid,
    repair,
)

__version__ = "0.1.0"

__all__ = [
    "ANALYTICAL_CRS",
    "GEOJSON_DEFAULT_CRS",
    # crs
    "detect_crs",
    "is_geographic",
    "to_analytical",
    "transform",
    # validate
    "QualityReport",
    "UncertainGeometryFlag",
    "assess_quality",
    "explain_invalidity",
    "is_valid",
    "repair",
    # parts
    "component_count",
    "explode",
    "is_multipart",
    "merge_parcels",
    "merge_with_components",
    "split_to_components",
    # metrics
    "BoundaryEdge",
    "Frontage",
    "MainAxis",
    "NeighborType",
    "UsableArea",
    "WidthProfile",
    "area_m2",
    "classify_boundary_edges",
    "compactness",
    "convexity",
    "detect_frontage",
    "frontage",
    "frontage_azimuth",
    "irregularity",
    "is_corner",
    "main_axis",
    "narrowest_passage",
    "nearest_boundary_distance",
    "perimeter_m",
    "shape_class",
    "usable_area",
    "width_profile",
    # inscribed
    "inscribed_circle_center",
    "largest_inscribed_rectangle",
    "largest_orthogonal_polygon",
    "maximum_inscribed_circle",
    # overlay
    "buffer_m",
    "clip",
    "difference",
    "dissolve",
    "distance_to_layer",
    "intersection",
    "nearest",
    "nearest_point_pair",
    "simplify_topo",
    "symmetric_difference",
    "union",
    # snapshot
    "GeometrySnapshot",
    "input_hash",
]
