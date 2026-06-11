"""Phase 12 site-context module goldens (v1 Phase 10 §10.1.1–10.1.6).

Fixture-driven, zero network: every module consumes synthetic geometries /
rasters (clearly marked TEST FIXTURES). Covers the plan's golden list:

* slope fixture → earthworks class per parcel AND per building footprint;
* flood overlap → risk + clipped envelope + per-stage flags;
* landslide → geotech risk + investigation-brief unknown;
* Natura2000 → ruleset-driven EIA screening result;
* zjazd: KDW touching / not touching the public-road frontage;
* neighbor 25 m building shades a fixture flat → §60 fail via the validators'
  ``neighbors`` parameter (existing API — no re-implementation);
* heritage interventions: ``zabytek_do_remontu`` → konserwator question.
"""

from __future__ import annotations

import pytest
from plot_planning.site_context import (
    SiteContext,
    analyze_access,
    analyze_environment,
    analyze_geology,
    analyze_terrain,
    analyze_water,
    check_zjazd_kdw,
    eia_screening,
    heritage_interventions,
    neighbor_shading_impact,
    neighbors_from_features,
    terrain_unavailable,
    windows_at_boundary_precheck,
)
from plot_planning.wt_validators import (
    RULE_WT60,
    NeighborBuilding,
    ValidatorConfig,
    run_inter_building_checks,
)
from plot_planning.wt_validators.context import ObstructorPart
from plot_rules import RuleStatus, load_rulesets
from shapely.geometry import LineString, Polygon, box
from tests.site_fixtures import building_feature, gj_box, synthetic_dem_bytes
from tests.wt_fixtures import X0, Y0, building, parcel_square

REGISTRY = load_rulesets("rulesets/PL")


# --------------------------------------------------------------------------- #
# Terrain (§10.1.1)
# --------------------------------------------------------------------------- #
def _dem(**kw) -> bytes:
    return synthetic_dem_bytes(west=X0 - 50, north=Y0 + 250, width=300, height=300, **kw)


def test_terrain_slope_fixture_earthworks_classes() -> None:
    # Flat until col 150 (x = X0+100), then 12% eastward slope.
    raster = _dem(east_slope_pct=12.0, flat_until_col=150)
    flat_parcel = box(X0 + 10, Y0 + 10, X0 + 80, Y0 + 80)
    steep_parcel = box(X0 + 120, Y0 + 10, X0 + 190, Y0 + 80)

    flat = analyze_terrain(raster, flat_parcel, storage_uri="artifact://t/dem.tif")
    assert flat.status == "ok"
    assert flat.earthworks["parcel_class"] == "niski"
    assert flat.earthworks["basis"] == "industry_heuristic"
    assert flat.terrain_model is not None
    # Raster reference only — object-storage path convention (§26.3).
    assert flat.terrain_model.storage_uri == "artifact://t/dem.tif"

    steep = analyze_terrain(raster, steep_parcel)
    assert steep.earthworks["parcel_class"] == "wysoki"
    assert steep.earthworks["retaining_wall_risk"] is True
    assert steep.slope["mean_pct"] == pytest.approx(12.0, abs=0.5)
    # Per-building earthworks (masterplan delta 1).
    per_building = steep.earthworks_for_footprint(box(X0 + 130, Y0 + 20, X0 + 150, Y0 + 40))
    assert per_building["class"] == "wysoki"
    # A footprint outside the sampled window stays honestly unknown.
    outside = steep.earthworks_for_footprint(box(X0 + 5000, Y0 + 5000, X0 + 5020, Y0 + 5020))
    assert outside["status"] == "unknown"


def test_terrain_profiles_depressions_and_basement() -> None:
    raster = _dem(depression_at=(200, 100), depression_depth_m=0.6)  # inside flat area
    parcel = box(X0 + 10, Y0 + 10, X0 + 120, Y0 + 80)
    t = analyze_terrain(raster, parcel)
    assert t.depressions["detected"] is True and t.depressions["cell_count"] >= 1
    assert len(t.profiles) == 2 and t.profiles[0]["points"]
    # Basement precheck NEVER claims feasibility — groundwater is unknown (§21).
    assert t.basement_precheck["groundwater"] == "unknown"
    assert t.basement_precheck["verdict"] == "wymaga_badan_gruntowych"
    assert any(u.topic.startswith("Poziom wód gruntowych") for u in t.unknowns)


def test_terrain_unavailable_is_explicit_unknown() -> None:
    t = terrain_unavailable("source_unavailable")
    assert t.status == "no_data"
    assert t.unknowns and t.unknowns[0].reason == "source_unavailable"
    assert t.earthworks_for_footprint(box(0, 0, 1, 1))["status"] == "unknown"


def test_terrain_nodata_hole_does_not_poison_slope_stats() -> None:
    """Review B1: cells adjacent to a -9999 nodata hole must NOT produce garbage
    gradients — a flat parcel touching a hole stays flat (klasa niski), never
    mean_pct ≈ 9852 / bardzo_wysoki."""
    # Flat raster with a 20×20 nodata hole INSIDE the parcel footprint
    # (rows 190..210 / cols 90..110 → x X0+40..60, y Y0+40..60).
    raster = _dem(nodata_rects=[(190, 210, 90, 110)])
    parcel = box(X0 + 10, Y0 + 10, X0 + 80, Y0 + 80)
    t = analyze_terrain(raster, parcel)
    assert t.status == "ok"
    assert t.slope["mean_pct"] == pytest.approx(0.0, abs=0.5)
    assert t.slope["max_pct"] == pytest.approx(0.0, abs=0.5)
    assert t.earthworks["parcel_class"] == "niski"
    # Per-footprint query on the shared _slope_pct grid: flat footprint NEXT TO
    # the hole stays niski; a footprint fully inside the hole is honestly unknown.
    beside_hole = t.earthworks_for_footprint(box(X0 + 15, Y0 + 35, X0 + 38, Y0 + 65))
    assert beside_hole["class"] == "niski"
    assert beside_hole["mean_slope_pct"] == pytest.approx(0.0, abs=0.5)
    inside_hole = t.earthworks_for_footprint(box(X0 + 45, Y0 + 45, X0 + 55, Y0 + 55))
    assert inside_hole["status"] == "unknown"


def test_terrain_mostly_nodata_window_is_unavailable() -> None:
    """Review B1: too few valid cells → honest no_data, never fake flat stats."""
    parcel = box(X0 + 10, Y0 + 10, X0 + 80, Y0 + 80)
    # Whole window nodata → the parcel covers no valid cell.
    all_nodata = _dem(nodata_rects=[(0, 300, 0, 300)])
    t = analyze_terrain(all_nodata, parcel)
    assert t.status == "no_data"
    assert t.unknowns and t.unknowns[0].reason == "raster_window_empty"
    # A 2×2 valid island in a nodata sea: every valid cell has a nodata
    # neighbour → no finite gradient → insufficient_valid_cells.
    island = _dem(nodata_rects=[(200, 202, 100, 102)], nodata_invert=True)
    t2 = analyze_terrain(island, parcel)
    assert t2.status == "no_data"
    assert t2.unknowns and t2.unknowns[0].reason == "insufficient_valid_cells"


# --------------------------------------------------------------------------- #
# Water / flood (§10.1.2) — golden: overlap → constraint+risk + clipped envelope
# --------------------------------------------------------------------------- #
def test_flood_overlap_risk_clip_and_stage_flags() -> None:
    parcel = parcel_square()  # 200×200 at X0/Y0
    flood = box(X0, Y0, X0 + 200, Y0 + 60)  # southern strip flooded
    w = analyze_water(
        parcel,
        flood_geoms=[flood],
        watercourse_geoms=[LineString([(X0 - 30, Y0 - 10), (X0 + 230, Y0 - 10)])],
        mean_slope_pct=1.0,
    )
    assert w.flood_detected is True
    assert w.flood_coverage_percent == pytest.approx(30.0, abs=0.5)
    assert any(r.risk_type.value == "flood" for r in w.risks)
    envelope = box(X0 + 10, Y0 + 10, X0 + 190, Y0 + 190)
    clipped, removed = w.clip_envelope(envelope)
    assert removed == pytest.approx(180 * 50, rel=0.01)  # 10..60 strip inside envelope
    assert clipped.area < envelope.area
    # Delta 1: stage flags — stage 1 building in the flood strip, stage 2 clear.
    checks = w.stage_flood_checks(
        envelope,
        [
            ("A1", 1, box(X0 + 20, Y0 + 20, X0 + 40, Y0 + 40)),
            ("B1", 2, box(X0 + 20, Y0 + 100, X0 + 40, Y0 + 120)),
        ],
    )
    by_stage = {c["stage"]: c for c in checks}
    assert by_stage[1]["status"] == "flagged" and by_stage[1]["affected_buildings"] == ["A1"]
    assert by_stage[2]["status"] == "clear"
    assert by_stage[2]["envelope_after_flood_clip_m2"] < by_stage[2]["envelope_area_m2"]
    # Watercourse distance + water-law precheck question.
    assert w.watercourse_distance_m == pytest.approx(10.0, abs=0.1)
    assert w.water_law_precheck["procedure_question"] is True
    # Retention heuristic carries its basis; GZWP is an explicit unknown.
    assert w.retention["basis"] == "industry_heuristic"
    assert w.gzwp["status"] == "unknown"


def test_flood_source_unavailable_is_unknown_not_clear() -> None:
    w = analyze_water(
        parcel_square(), flood_geoms=None, watercourse_geoms=[], flood_status="source_unavailable"
    )
    assert w.flood_checked is False and w.flood_detected is False
    assert any(u.reason == "source_unavailable" for u in w.unknowns)
    # Stage checks degrade to unknown — NEVER "clear" without data (§21).
    checks = w.stage_flood_checks(None, [("A1", 1, box(X0, Y0, X0 + 10, Y0 + 10))])
    assert checks[0]["status"] == "unknown"


# --------------------------------------------------------------------------- #
# Geology (§10.1.3) — golden: landslide → risk + investigation unknown
# --------------------------------------------------------------------------- #
def test_landslide_overlap_geotech_risk_and_brief() -> None:
    parcel = parcel_square()
    g = analyze_geology(parcel, landslide_geoms=[box(X0, Y0, X0 + 100, Y0 + 100)])
    assert g.landslide_detected is True
    assert g.geotech_risk_class == "wysokie"
    assert any(r.risk_type.value == "geology" for r in g.risks)
    # Investigation brief stub is UnknownItem/finding-driven.
    assert any("osuwiskowego" in line for line in g.investigation_brief)
    assert any(u.reason == "source_not_wired" for u in g.unknowns)  # mining/MIDAS
    assert g.recommendations and g.recommendations[0].addressed_to.startswith("geotechnik")


def test_landslide_clear_is_not_low_risk_blindly() -> None:
    g = analyze_geology(parcel_square(), landslide_geoms=[])
    assert g.landslide_detected is False
    assert g.geotech_risk_class == "umiarkowane"  # soil still unverified
    g2 = analyze_geology(parcel_square(), landslide_geoms=None, landslide_status="source_unavailable")
    assert g2.geotech_risk_class is None  # honestly unknown without SOPO


# --------------------------------------------------------------------------- #
# Environment / heritage (§10.1.4) — golden: Natura2000 → EIA screening
# --------------------------------------------------------------------------- #
def test_natura2000_overlap_drives_eia_screening_thresholds() -> None:
    parcel = parcel_square()  # 4 ha
    env = analyze_environment(
        parcel,
        REGISTRY,
        protected_geoms=[parcel_square()],  # whole parcel protected (fixture)
        heritage_geoms=[],
        investment_type="multifamily",
        investment_area_m2=6_000.0,  # 0.6 ha
    )
    assert env.protected_detected is True
    scr = env.eia_screening
    # 0.6 ha ≥ protected threshold 0.5 ha → grupa II screening required.
    assert scr["required"] is True and scr["group"] == "II"
    assert scr["threshold_ha"] == 0.5 and scr["in_protected_area"] is True
    assert scr["rule_id"] == "PL-EIA-SCREENING-001"
    assert "Dz.U. 2019 poz. 1839" in scr["source_reference"]

    # Outside protection the same area is below the 2.0 ha threshold.
    scr2 = eia_screening(
        REGISTRY,
        investment_type="multifamily",
        investment_area_m2=6_000.0,
        in_protected_area=False,
    )
    assert scr2["required"] is False and scr2["threshold_ha"] == 2.0
    # Unknown investment type → honestly unknown, never defaulted (§0v2.4).
    scr3 = eia_screening(
        REGISTRY, investment_type="unknown", investment_area_m2=6_000.0, in_protected_area=False
    )
    assert scr3["required"] is None and scr3["reason"] == "investment_type_unknown"


def test_heritage_interventions_constrain_zabytek_do_remontu() -> None:
    parcel = parcel_square()
    heritage_zone = box(X0, Y0, X0 + 80, Y0 + 80)
    env = analyze_environment(
        parcel,
        REGISTRY,
        protected_geoms=[],
        heritage_geoms=[heritage_zone],
        investment_type="multifamily",
        investment_area_m2=1_000.0,
    )
    assert env.heritage_detected is True
    entries = heritage_interventions(
        env,
        [
            ("Hala", "zabytek_do_remontu", box(X0 + 10, Y0 + 10, X0 + 30, Y0 + 30)),
            ("Nowy-A", "projektowany", box(X0 + 40, Y0 + 40, X0 + 60, Y0 + 60)),  # in zone
            ("Nowy-B", "projektowany", box(X0 + 120, Y0 + 120, X0 + 140, Y0 + 140)),  # outside
        ],
    )
    kinds = {e["building"]: e["kind"] for e in entries}
    assert kinds["Hala"] == "zabytek_do_remontu"
    assert kinds["Nowy-A"] == "new_in_heritage_zone"
    assert "Nowy-B" not in kinds
    # Every intervention carries the konserwator question (soft, validator-style).
    assert all(e["requires_konserwator"] and e["question"] for e in entries)
    # Heritage layer down → the zabytek note flags the unverified register (§21).
    entries2 = heritage_interventions(
        None, [("Hala", "zabytek_do_remontu", box(X0, Y0, X0 + 10, Y0 + 10))]
    )
    assert "niezweryfikowany" in entries2[0]["note"]


# --------------------------------------------------------------------------- #
# Roads / utilities (§10.1.5) — golden: zjazd KDW touching / not touching
# --------------------------------------------------------------------------- #
def test_zjazd_kdw_touching_and_not_touching_frontage() -> None:
    parcel = parcel_square()  # 200×200
    public_road = box(X0 - 20, Y0 - 22, X0 + 220, Y0 - 2)  # along the south side
    kdw_ok = LineString([(X0 + 100, Y0 + 0.5), (X0 + 100, Y0 + 150)])  # reaches frontage
    kdw_bad = LineString([(X0 + 100, Y0 + 60), (X0 + 100, Y0 + 150)])  # floats inside

    ok = check_zjazd_kdw([("kdw-1", kdw_ok)], parcel, public_road)
    assert ok["status"] == "pass" and ok["connected_roads"] == ["kdw-1"]
    bad = check_zjazd_kdw([("kdw-1", kdw_bad)], parcel, public_road)
    assert bad["status"] == "fail"
    # No public-road layer → unknown (data absence ≠ no requirement, §21).
    unk = check_zjazd_kdw([("kdw-1", kdw_ok)], parcel, None)
    assert unk["status"] == "unknown"
    # Parcel with no public-road frontage at all → fail with the access message.
    island = check_zjazd_kdw(
        [("kdw-1", kdw_ok)], parcel, box(X0 + 500, Y0 + 500, X0 + 600, Y0 + 520)
    )
    assert island["status"] == "fail"


def test_analyze_access_adjacency_utilities_and_collisions() -> None:
    parcel = parcel_square()
    road = box(X0 - 20, Y0 - 22, X0 + 220, Y0 - 2)
    gas_line = LineString([(X0 + 50, Y0 - 50), (X0 + 50, Y0 + 250)])  # crosses parcel
    a = analyze_access(
        parcel,
        road_geoms=[road],
        utility_features=[
            building_feature(
                {"type": "LineString", "coordinates": [list(c) for c in gas_line.coords]},
                rodzajSieci="gazowa",
                gestor="PSG",
            )
        ],
    )
    assert a.road_access is not None and a.road_access.has_public_road_access is True
    assert (a.road_access.frontage_length_m or 0) > 0
    assert a.zjazd["feasibility"] == "mozliwy"
    assert a.utilities and a.utilities[0].network_type == "gas"
    assert a.utilities[0].operator == "PSG"
    assert a.technical_zones and a.technical_zones[0]["network_type"] == "gas"
    # Collision warnings vs masterplan elements (delta 1).
    collisions = a.network_collisions([("A1", box(X0 + 45, Y0 + 10, X0 + 60, Y0 + 30))])
    assert collisions and collisions[0]["network_type"] == "gas"
    # Connection capacity stays a gestor unknown.
    assert any(u.reason == "requires_gestor_statement" for u in a.unknowns)


def test_analyze_access_roads_unavailable_is_unknown() -> None:
    a = analyze_access(
        parcel_square(), road_geoms=None, utility_features=None,
        roads_status="source_unavailable", utilities_status="source_unavailable",
    )
    assert a.road_access is not None
    assert a.road_access.has_public_road_access is None  # unknown, NOT False
    assert sum(1 for u in a.unknowns if u.reason == "source_unavailable") == 2


# --------------------------------------------------------------------------- #
# Neighborhood / sun (§10.1.6) — golden: 25 m neighbor shades the fixture flat
# --------------------------------------------------------------------------- #
def test_neighbors_from_features_heights_and_honesty() -> None:
    feats = [
        building_feature(gj_box(X0 + 300, Y0, X0 + 320, Y0 + 20), wysokosc=25.0, id="N-wys"),
        building_feature(gj_box(X0 + 340, Y0, X0 + 360, Y0 + 20), liczbaKondygnacji=4, id="N-kond"),
        building_feature(gj_box(X0 + 380, Y0, X0 + 400, Y0 + 20), id="N-brak"),
    ]
    neighbors, notes = neighbors_from_features(feats)
    by_name = {n.name: n for n in neighbors}
    assert by_name["N-wys"].height_m == 25.0
    assert by_name["N-kond"].height_m == pytest.approx(4 * ValidatorConfig().floor_height_m)
    assert "N-brak" not in by_name  # no invented height (§21)
    assert any("storeys_heuristic" in n for n in notes)
    assert any("pominięty" in n for n in notes)


def test_neighbor_25m_building_fails_wt60_via_neighbors_param() -> None:
    parcel = parcel_square()
    # New 2-kond. flat with windows ONLY on the south wall; a 25 m tall, 80 m
    # long neighbor sits 8 m to the south — its equinox shadow (>25 m at noon)
    # covers the south windows through the whole 7:00–17:00 window.
    proposal_payload = {
        "buildings": [
            building("Mieszkalny-A", 60, 40, 30, 10, 2, windowed_walls=[0]),
        ],
        "schema_version": 2,
    }
    from plot_agent.drawing import MasterplanProposal

    proposal = MasterplanProposal.model_validate(proposal_payload)
    neighbor = NeighborBuilding(
        name="sasiad-25m",
        geometry=Polygon(
            [(X0 + 20, Y0 + 22), (X0 + 130, Y0 + 22), (X0 + 130, Y0 + 32), (X0 + 20, Y0 + 32)]
        ),
        height_m=25.0,
    )
    without = run_inter_building_checks(proposal, parcel, REGISTRY)
    wt60_without = [c for c in without if c.rule_id == RULE_WT60]
    assert wt60_without and all(c.status is RuleStatus.PASS for c in wt60_without)

    with_neighbor = run_inter_building_checks(
        proposal, parcel, REGISTRY, neighbors=[neighbor]
    )
    wt60_with = [c for c in with_neighbor if c.rule_id == RULE_WT60]
    assert wt60_with and any(c.status is RuleStatus.FAIL for c in wt60_with), (
        "the 25 m neighbor must shade the south-facing windows into a §60 FAIL"
    )


def test_neighbor_shading_impact_report_reuses_sun_engine() -> None:
    # The NEW building stands 8 m north of... no: SOUTH of the neighbor, so the
    # neighbor's south-facing facade loses sun. (Impact TO neighbors, F-0343.)
    cfg = ValidatorConfig()
    neighbor = NeighborBuilding(
        name="dom-sasiada",
        geometry=Polygon(
            [(X0 + 40, Y0 + 40), (X0 + 60, Y0 + 40), (X0 + 60, Y0 + 50), (X0 + 40, Y0 + 50)]
        ),
        height_m=6.0,
    )
    new_part = ObstructorPart(
        owner="Nowy",
        geometry=Polygon(
            [(X0 + 20, Y0 + 10), (X0 + 80, Y0 + 10), (X0 + 80, Y0 + 30), (X0 + 20, Y0 + 30)]
        ),
        height_m=26.4,  # 8 kond.
        source="proposal",
        is_new=True,
    )
    impacts = neighbor_shading_impact(
        [new_part],
        [neighbor],
        config=cfg,
        window_start_h=7.0,
        window_end_h=17.0,
        min_required_hours=3.0,
    )
    assert impacts, "a 26 m slab 10 m south must shade the neighbor's facade"
    worst = impacts[0]
    assert worst["neighbor"] == "dom-sasiada"
    assert worst["hours_lost"] > 0 and worst["hours_after"] < worst["hours_before"]
    assert worst["status"] == "warning"  # SOFT impact report, not a hard rule
    # Without new buildings there is no impact to report.
    assert neighbor_shading_impact(
        [], [neighbor], config=cfg, window_start_h=7.0, window_end_h=17.0
    ) == []


def test_neighbor_shading_existing_buildings_count_in_baseline() -> None:
    """Review m3: the proposal's EXISTING on-parcel buildings are context in BOTH
    runs — pre-existing insolation loss must never be attributed to new buildings."""
    cfg = ValidatorConfig()
    neighbor = NeighborBuilding(
        name="dom-sasiada",
        geometry=Polygon(
            [(X0 + 40, Y0 + 40), (X0 + 60, Y0 + 40), (X0 + 60, Y0 + 50), (X0 + 40, Y0 + 50)]
        ),
        height_m=6.0,
    )
    # Existing 26.4 m slab already shades the neighbor's south facade…
    existing_tower = ObstructorPart(
        owner="Wieza-istniejaca",
        geometry=Polygon(
            [(X0 + 20, Y0 + 10), (X0 + 80, Y0 + 10), (X0 + 80, Y0 + 30), (X0 + 20, Y0 + 30)]
        ),
        height_m=26.4,
        source="proposal",
        is_new=False,
    )
    # …and the NEW part sits strictly INSIDE the tower's footprint, same height —
    # it cannot add a single ray of new shadow.
    new_inside = ObstructorPart(
        owner="Nowy-maly",
        geometry=Polygon(
            [(X0 + 40, Y0 + 10), (X0 + 60, Y0 + 10), (X0 + 60, Y0 + 30), (X0 + 40, Y0 + 30)]
        ),
        height_m=26.4,
        source="proposal",
        is_new=True,
    )
    # Sanity: WITHOUT the existing context the same new part reports a big loss.
    assert neighbor_shading_impact(
        [new_inside], [neighbor], config=cfg, window_start_h=7.0, window_end_h=17.0
    ), "fixture must shade the facade when evaluated alone"
    # With the existing tower in BOTH runs the attributable loss is ~0 → no report.
    impacts = neighbor_shading_impact(
        [new_inside],
        [neighbor],
        config=cfg,
        window_start_h=7.0,
        window_end_h=17.0,
        existing_parts=[existing_tower],
    )
    assert impacts == []


def test_windows_at_boundary_precheck() -> None:
    parcel = parcel_square()
    near = NeighborBuilding(
        name="blisko", geometry=box(X0 - 22, Y0 + 50, X0 - 2, Y0 + 70), height_m=8.0
    )
    far = NeighborBuilding(
        name="daleko", geometry=box(X0 - 60, Y0 + 50, X0 - 40, Y0 + 70), height_m=8.0
    )
    flags = windows_at_boundary_precheck([near, far], parcel)
    assert [f["neighbor"] for f in flags] == ["blisko"]
    assert flags[0]["status"] == "verify"  # verification flag, not a determination


# --------------------------------------------------------------------------- #
# SiteContext aggregation
# --------------------------------------------------------------------------- #
def test_site_context_aggregates_and_serializes() -> None:
    parcel = parcel_square()
    site = SiteContext(analysis_id="an-1")
    site.water = analyze_water(
        parcel, flood_geoms=[box(X0, Y0, X0 + 50, Y0 + 50)], watercourse_geoms=[]
    )
    site.geology = analyze_geology(parcel, landslide_geoms=[])
    assert site.all_risks()  # flood risk present
    assert site.all_unknowns()  # GZWP + mining unknowns
    d = site.to_dict()
    assert d["analysis_id"] == "an-1"
    assert d["water"]["flood_detected"] is True
    assert d["terrain"] is None  # module not run — honest absence
