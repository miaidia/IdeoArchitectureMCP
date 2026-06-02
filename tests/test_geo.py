"""Phase 5 geometry-core tests (packages/geo / plot_geo).

Covers (Implementation Plan §5.3):
  * CRS transform EPSG:4326 <-> 2180 within tolerance (F-0548).
  * Property-based invariants (hypothesis, F-0547): CRS round-trip area/perimeter
    invariance, ``repair`` idempotency, ``buffer_m`` area monotonicity.
  * Largest inscribed rectangle containment + positive area + beats a baseline.
  * Golden-parcel metric ranges (F-0541): simple / narrow / corner / irregular /
    multipart.
  * Overlay/buffer guard behaviour and snapshot stability.
"""

from __future__ import annotations

import math

import plot_geo as g
import pytest
import shapely
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from plot_geo.crs import ANALYTICAL_CRS, transform
from plot_geo.metrics import NeighborType
from shapely import box
from shapely.geometry import LineString, MultiPolygon, Polygon

# --------------------------------------------------------------------------------------
# Golden parcels (EPSG:2180 metres)
# --------------------------------------------------------------------------------------
SIMPLE = Polygon([(0, 0), (50, 0), (50, 40), (0, 40)])  # 50x40 rectangle, 2000 m²
NARROW = Polygon([(0, 0), (120, 0), (120, 12), (0, 12)])  # 120x12 strip, 1440 m²
# L-shape (irregular, non-convex).
IRREGULAR = Polygon([(0, 0), (60, 0), (60, 20), (20, 20), (20, 60), (0, 60)])
# Corner parcel: a square fronting roads on its south AND east edges.
CORNER = Polygon([(0, 0), (40, 0), (40, 40), (0, 40)])
ROAD_SOUTH = LineString([(-5, -1), (45, -1)])
ROAD_EAST = LineString([(41, -5), (41, 45)])
MULTI = MultiPolygon([box(0, 0, 10, 10), box(20, 0, 30, 10), box(40, 0, 50, 10)])


# --------------------------------------------------------------------------------------
# F-0548 — CRS transform within tolerance
# --------------------------------------------------------------------------------------
def test_crs_known_point_4326_to_2180() -> None:
    # Warsaw Palace of Culture ~ (21.0067 E, 52.2319 N). Known EPSG:2180 easting/northing
    # is around (637000, 486000) — assert the transform lands in that neighbourhood and
    # the inverse returns the original lon/lat within a millidegree.
    pt = shapely.Point(21.0067, 52.2319)
    proj = transform(pt, "EPSG:4326", ANALYTICAL_CRS)
    assert 630000 < proj.x < 645000
    assert 480000 < proj.y < 492000
    back = transform(proj, ANALYTICAL_CRS, "EPSG:4326")
    assert abs(back.x - 21.0067) < 1e-6
    assert abs(back.y - 52.2319) < 1e-6


def test_detect_crs_from_geojson_named() -> None:
    gj = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2180"}},
    }
    assert g.detect_crs(gj) == "EPSG:2180"
    # OGC CRS84 maps to WGS84.
    gj2 = dict(gj)
    gj2["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    assert g.detect_crs(gj2) == "EPSG:4326"
    # Bare shapely geometry has no CRS.
    assert g.detect_crs(SIMPLE) is None


# --------------------------------------------------------------------------------------
# Property-based tests (F-0547)
# --------------------------------------------------------------------------------------
def _rect_strategy() -> st.SearchStrategy[Polygon]:
    """Random axis-aligned rectangles centred near central Poland (valid in 2180)."""
    cx = st.floats(min_value=400000, max_value=700000)
    cy = st.floats(min_value=300000, max_value=700000)
    w = st.floats(min_value=5.0, max_value=2000.0)
    h = st.floats(min_value=5.0, max_value=2000.0)
    return st.builds(
        lambda cx, cy, w, h: box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
        cx, cy, w, h,
    )


def _polygon_strategy() -> st.SearchStrategy[Polygon]:
    """Random (possibly non-convex) simple polygons via convex hull of random points."""
    pts = st.lists(
        st.tuples(
            st.floats(min_value=400000, max_value=700000),
            st.floats(min_value=300000, max_value=700000),
        ),
        min_size=3,
        max_size=12,
    )
    return st.builds(lambda ps: shapely.MultiPoint(ps).convex_hull, pts).filter(
        lambda gm: gm.geom_type == "Polygon" and gm.area > 1.0
    )


@settings(max_examples=120, suppress_health_check=[HealthCheck.too_slow])
@given(_rect_strategy())
def test_prop_crs_roundtrip_area_perimeter_invariant(poly: Polygon) -> None:
    """(a) Area & perimeter invariant under 2180 -> 4326 -> 2180 round-trip (F-0547)."""
    rt = transform(transform(poly, ANALYTICAL_CRS, "EPSG:4326"), "EPSG:4326", ANALYTICAL_CRS)
    # Relative tolerance: projection round-trip is ~mm accurate; allow 1e-4 relative.
    assert math.isclose(rt.area, poly.area, rel_tol=1e-4)
    assert math.isclose(rt.length, poly.length, rel_tol=1e-4)


@settings(max_examples=120, suppress_health_check=[HealthCheck.too_slow])
@given(_polygon_strategy())
def test_prop_repair_idempotent(poly: Polygon) -> None:
    """(b) repair is idempotent: repair(repair(g)) == repair(g) (F-0547)."""
    once = g.repair(poly)
    twice = g.repair(once)
    assert once.equals(twice)
    # And byte-stable (normalised) so the input hash is stable.
    assert shapely.to_wkb(once) == shapely.to_wkb(twice)


def test_prop_repair_idempotent_on_invalid() -> None:
    """repair fixes a self-intersecting bowtie and is then idempotent."""
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert not bowtie.is_valid
    once = g.repair(bowtie)
    assert once.is_valid
    twice = g.repair(once)
    assert once.equals(twice)


@settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
@given(_rect_strategy(), st.lists(st.floats(min_value=0.1, max_value=200.0), min_size=2, max_size=5))
def test_prop_buffer_area_monotonic(poly: Polygon, dists: list[float]) -> None:
    """(c) buffer_m area is non-decreasing in distance for positive buffers (F-0547)."""
    ordered = sorted(dists)
    areas = [g.buffer_m(poly, d).area for d in ordered]
    for a_prev, a_next in zip(areas, areas[1:], strict=False):
        assert a_next >= a_prev - 1e-6


# --------------------------------------------------------------------------------------
# Largest inscribed rectangle — containment + positive area + beats baseline
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "poly,name",
    [(SIMPLE, "simple"), (NARROW, "narrow"), (CORNER, "corner"), (IRREGULAR, "irregular")],
)
def test_largest_rectangle_contained_and_positive(poly: Polygon, name: str) -> None:
    rect = g.largest_inscribed_rectangle(poly)
    assert poly.buffer(1e-9).contains(rect), f"{name}: rect not contained"
    assert rect.area > 0.0, f"{name}: non-positive area"
    # 4-corner rectangle.
    assert len(rect.exterior.coords) == 5


def test_largest_rectangle_simple_beats_baseline() -> None:
    """For the 50x40 rectangle the LIR should recover almost the whole parcel."""
    rect = g.largest_inscribed_rectangle(SIMPLE, resolution=300)
    # Hand-known: the parcel is itself a rectangle of area 2000; a good LIR is >= 95%.
    assert rect.area >= 0.95 * SIMPLE.area


def test_largest_rectangle_irregular_beats_trivial_baseline() -> None:
    """On the L-shape the LIR must beat the maximum inscribed circle's bounding square."""
    rect = g.largest_inscribed_rectangle(IRREGULAR, resolution=300)
    circ_center = g.inscribed_circle_center(IRREGULAR)
    mic_line = shapely.maximum_inscribed_circle(IRREGULAR)
    r = mic_line.length
    inscribed_square_area = (r * math.sqrt(2)) ** 2  # square inscribed in the MIC
    assert rect.area > inscribed_square_area
    assert IRREGULAR.buffer(1e-9).contains(rect)
    assert circ_center is not None


def test_largest_rectangle_rotated_at_least_axis_aligned() -> None:
    """A rotated LIR should be >= the axis-aligned one (and still contained)."""
    diamond = Polygon([(0, 50), (50, 100), (100, 50), (50, 0)])  # 45-deg square
    axis = g.largest_inscribed_rectangle(diamond, allow_rotation=False)
    rot = g.largest_inscribed_rectangle(diamond, allow_rotation=True)
    assert diamond.buffer(1e-9).contains(rot)
    assert rot.area >= axis.area * 0.99  # rotation should not be worse


def test_largest_rectangle_multipolygon() -> None:
    rect = g.largest_inscribed_rectangle(MULTI)
    assert MULTI.buffer(1e-9).contains(rect)
    assert rect.area > 0.0


def test_largest_orthogonal_polygon_v1_is_rectangle() -> None:
    poly = g.largest_orthogonal_polygon(IRREGULAR)
    assert IRREGULAR.buffer(1e-9).contains(poly)
    assert poly.area > 0.0


def test_maximum_inscribed_circle_wrapper() -> None:
    circ = g.maximum_inscribed_circle(SIMPLE)
    assert circ.geom_type == "Polygon"
    assert SIMPLE.buffer(1e-6).contains(circ.centroid)


# --------------------------------------------------------------------------------------
# Golden-parcel metric ranges (F-0541)
# --------------------------------------------------------------------------------------
def test_metrics_simple() -> None:
    assert math.isclose(g.area_m2(SIMPLE), 2000.0)
    assert math.isclose(g.perimeter_m(SIMPLE), 180.0)
    assert 0.0 < g.compactness(SIMPLE) <= 1.0
    assert math.isclose(g.convexity(SIMPLE), 1.0)  # rectangle is convex
    assert 0.0 <= g.irregularity(SIMPLE) < 0.3


def test_metrics_narrow_width_profile() -> None:
    wp = g.width_profile(NARROW)
    # Constant 12 m width strip — min/median/max should all be ~12.
    assert math.isclose(wp.median_width_m, 12.0, abs_tol=1.0)
    npass, pt = g.narrowest_passage(NARROW)
    assert math.isclose(npass, 12.0, abs_tol=2.0)
    assert pt is not None
    # Narrow strip is far less compact than the simple parcel.
    assert g.compactness(NARROW) < g.compactness(SIMPLE)


def test_metrics_irregular_non_convex() -> None:
    assert g.convexity(IRREGULAR) < 1.0  # L-shape is non-convex
    assert 0.0 < g.compactness(IRREGULAR) <= 1.0
    assert g.irregularity(IRREGULAR) > g.irregularity(SIMPLE)


def test_metrics_corner_detection() -> None:
    assert g.is_corner(CORNER, [ROAD_SOUTH, ROAD_EAST]) is True
    # A parcel fronting only one road is NOT a corner.
    assert g.is_corner(CORNER, [ROAD_SOUTH]) is False


def test_metrics_frontage_and_azimuth() -> None:
    f = g.detect_frontage(CORNER, ROAD_SOUTH)
    assert f is not None
    assert f.length_m >= 35.0  # the ~40 m south edge
    # South edge runs east-west: azimuth ~90 or ~270 (mod 180 -> 90).
    assert math.isclose(f.azimuth_deg % 180.0, 90.0, abs_tol=5.0)
    # No road -> no frontage.
    assert g.detect_frontage(CORNER, LineString([(1000, 1000), (1010, 1010)])) is None


def test_metrics_main_axis_narrow() -> None:
    axis = g.main_axis(NARROW)
    # Main axis of a 120x12 strip runs east-west, length ~120.
    assert axis.length_m > 100.0
    assert math.isclose(axis.azimuth_deg % 180.0, 90.0, abs_tol=10.0)


def test_metrics_boundary_classification() -> None:
    edges = g.classify_boundary_edges(
        CORNER,
        {NeighborType.ROAD: [ROAD_SOUTH, ROAD_EAST]},
    )
    kinds = {e.neighbor_type for e in edges}
    assert NeighborType.ROAD in kinds
    assert NeighborType.UNKNOWN in kinds  # the north + west edges have no neighbour
    total = sum(e.length_m for e in edges)
    assert math.isclose(total, CORNER.boundary.length, rel_tol=0.05)


def test_metrics_usable_area_before_after() -> None:
    constraint = box(0, 0, 40, 10)  # remove a 400 m² strip
    ua = g.usable_area(CORNER, constraint)
    assert math.isclose(ua.before_m2, 1600.0)
    assert math.isclose(ua.removed_m2, 400.0, abs_tol=1.0)
    assert math.isclose(ua.after_m2, 1200.0, abs_tol=1.0)
    assert 20.0 < ua.removed_percent < 30.0
    # No constraints -> after == before.
    ua0 = g.usable_area(CORNER)
    assert ua0.after_m2 == ua0.before_m2


# --------------------------------------------------------------------------------------
# Multipart (F-0021/0022/0023)
# --------------------------------------------------------------------------------------
def test_multipart_explode_and_merge() -> None:
    assert g.is_multipart(MULTI) is True
    assert g.component_count(MULTI) == 3
    parts = g.explode(MULTI)
    assert len(parts) == 3
    # Merging adjacent parcels dissolves shared boundaries.
    merged = g.merge_parcels([box(0, 0, 10, 10), box(10, 0, 20, 10)])
    assert g.component_count(merged) == 1  # touching -> single polygon
    assert math.isclose(merged.area, 200.0)
    # Non-adjacent stay separate.
    merged2 = g.merge_parcels([box(0, 0, 10, 10), box(30, 0, 40, 10)])
    assert g.component_count(merged2) == 2


# --------------------------------------------------------------------------------------
# Validation / quality
# --------------------------------------------------------------------------------------
def test_validate_repair_bowtie() -> None:
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert not g.is_valid(bowtie)
    fixed = g.repair(bowtie)
    assert g.is_valid(fixed)
    assert g.explain_invalidity(bowtie) is not None
    assert g.explain_invalidity(fixed) is None


def test_assess_quality_flags() -> None:
    # Good geometry: no flags.
    report = g.assess_quality(SIMPLE)
    assert report.is_valid
    assert not report.is_uncertain
    # Sliver: very thin tiny polygon.
    sliver = Polygon([(0, 0), (5, 0), (5, 0.01), (0, 0.01)])
    rep2 = g.assess_quality(sliver)
    assert g.UncertainGeometryFlag.SLIVER in rep2.flags or g.UncertainGeometryFlag.TINY_AREA in rep2.flags
    # Empty geometry.
    rep3 = g.assess_quality(Polygon())
    assert g.UncertainGeometryFlag.EMPTY in rep3.flags


# --------------------------------------------------------------------------------------
# Overlay / buffer guard / snapshot
# --------------------------------------------------------------------------------------
def test_overlay_set_ops() -> None:
    a = box(0, 0, 10, 10)
    b = box(5, 5, 15, 15)
    assert math.isclose(g.intersection(a, b).area, 25.0)
    assert math.isclose(g.union(a, b).area, 175.0)
    assert math.isclose(g.difference(a, b).area, 75.0)
    assert math.isclose(g.symmetric_difference(a, b).area, 150.0)
    assert math.isclose(g.dissolve([a, b]).area, 175.0)


def test_buffer_guard_rejects_or_projects_geographic() -> None:
    # A geographic (degree) input MUST NOT be buffered in degrees — passing src_crs
    # auto-projects to EPSG:2180 first, so the result is a sane metric-scale buffer.
    pt = shapely.Point(21.0, 52.0)  # lon/lat
    buffered = g.buffer_m(pt, 100.0, src_crs="EPSG:4326")
    # 100 m buffer in 2180 -> area ~ pi*100^2 ~ 31416 m², NOT a degree-scale blob.
    assert 25000 < buffered.area < 40000


def test_buffer_metric_assumed_when_no_crs() -> None:
    poly = box(0, 0, 10, 10)
    buffered = g.buffer_m(poly, 5.0)
    assert buffered.area > poly.area


def test_simplify_topo_preserves_validity() -> None:
    poly = shapely.Point(500000, 400000).buffer(100.0)  # many-vertex circle
    simp = g.simplify_topo(poly, 5.0)
    assert simp.is_valid
    assert len(simp.exterior.coords) < len(poly.exterior.coords)


def test_nearest_and_distance_to_layer() -> None:
    target = shapely.Point(0, 0)
    layer = [shapely.Point(10, 0), shapely.Point(3, 0), shapely.Point(100, 0)]
    n = g.nearest(target, layer)
    assert n is not None and n.equals(shapely.Point(3, 0))
    assert math.isclose(g.distance_to_layer(target, layer), 3.0)


def test_clip_to_parcel_buffer() -> None:
    layer = box(-50, -50, 50, 50)
    window = box(0, 0, 10, 10)
    clipped = g.clip(layer, window)
    assert math.isclose(clipped.area, 100.0)


def test_input_hash_stable_and_snapshot() -> None:
    # Same geometry with reversed coordinate order hashes identically (normalised).
    p1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    p2 = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])  # reversed winding
    assert g.input_hash(p1) == g.input_hash(p2)
    # Params change the hash.
    assert g.input_hash(p1, {"k": 1}) != g.input_hash(p1, {"k": 2})
    # Snapshot rehydrates.
    snap = g.GeometrySnapshot.create(p1, params={"k": 1})
    assert snap.crs == ANALYTICAL_CRS
    assert snap.geometry.equals(shapely.normalize(p1))
    assert snap.hash == g.input_hash(p1, {"k": 1})
