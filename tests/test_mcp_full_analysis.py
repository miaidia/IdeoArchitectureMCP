"""Phase 12 MCP integration: full_due_diligence + analysis binding (delta 1–3).

Pure-mock connectors (zero network, TEST FIXTURES): the mock ULDK parcel is a
40×30 m rectangle at (630880, 497170)–(630920, 497200) EPSG:2180; risk layers,
the DEM and the neighbor buildings are synthetic features around it.

Covers the plan's verification list:

* full mode populates risks/constraints/unknowns + the §14 scores (delta 2) and
  ``sources_collect`` lists the new SourceRecords;
* hard blocker (flood over the parcel) → ``LIKELY_BLOCKED`` regardless of the
  other (favourable) scores;
* connector-kill: one dead source → PARTIAL + ``source_unavailable`` recorded
  (NOT silent) while the rest of the analysis completes (NFR-REL-001);
* ``map_preview`` renders the stored analysis' site-context layers;
* delta 3 binding: ``propose_layout(analysis_id=...)`` evaluates on the
  ANALYSIS parcel (not the sample), scopes variants/audit/overrides to it, runs
  the site checks (earthworks / flood stages / heritage / zjazd), and the
  koncepcja report pulls the bound lineage.
"""

from __future__ import annotations

from typing import Any

import pytest
from plot_domain import AnalysisInput
from tests.mocks import mock_connectors
from tests.site_fixtures import building_feature, gj_box, synthetic_dem_bytes

pytest.importorskip("rasterio")

from plot_envelope import RiskKind  # noqa: E402
from plot_mcp_server import usecases  # noqa: E402

# Mock ULDK parcel frame (tests/mocks.py DEFAULT_PARCEL_WKT): 40 × 30 m.
PX0, PY0, PX1, PY1 = 630880.0, 497170.0, 630920.0, 497200.0


def _dem(slope_pct: float = 0.0) -> bytes:
    return synthetic_dem_bytes(
        west=PX0 - 60, north=PY1 + 60, width=200, height=200, east_slope_pct=slope_pct
    )


def _road_geojson() -> dict[str, Any]:
    # Public road strip just south of the parcel (within the 5 m adjacency tol).
    return gj_box(PX0 - 10, PY0 - 12, PX1 + 10, PY0 - 1)


def _neighbor_feature() -> dict[str, Any]:
    # 25 m tall neighbor ~35 m north of the parcel (outside it; far enough not
    # to violate §13 for the fixture building — its presence still proves the
    # neighbors flow into the bound validators).
    return building_feature(gj_box(PX0, PY1 + 28, PX1, PY1 + 38), wysokosc=25.0, id="sasiad-N")


def _full_input() -> AnalysisInput:
    return AnalysisInput.model_validate(
        {
            "input": {"parcel_id": "141201_1.0001.1867/2"},
            "analysis_mode": "full_due_diligence",
            "investment_goal": {"type": "multifamily", "target_gfa_m2": 1500.0},
        }
    )


def _analyze_full(**mock_kwargs: Any) -> Any:
    usecases.set_connectors(mock_connectors(**mock_kwargs))
    try:
        return usecases.parcel_analyze(_full_input(), "test-ruleset")
    finally:
        usecases.set_connectors(None)


def _masterplan_payload() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "buildings": [
            {
                "name": "A1",
                "stage": 1,
                "segments": [
                    {
                        "polygon": gj_box(PX0 + 5, PY0 + 15, PX0 + 17, PY0 + 23),
                        "floors": 2,
                        "use": "mieszkalny",
                    }
                ],
            }
        ],
        "roads": [
            {
                "centerline": {
                    "type": "LineString",
                    "coordinates": [[PX0 + 25, PY0 + 1], [PX0 + 25, PY0 + 20]],
                },
                "width_m": 5.0,
                "function": "kdw",
            }
        ],
        # ≥25% of the 1200 m² parcel as PBC so §39 passes (20 × 16 = 320 m²).
        "greenery_polygons": [gj_box(PX0 + 18, PY0 + 8, PX0 + 38, PY0 + 24)],
    }


# --------------------------------------------------------------------------- #
# Full mode: site context + scores + sources
# --------------------------------------------------------------------------- #
def test_full_due_diligence_populates_site_context_scores_and_sources() -> None:
    result = _analyze_full(
        feature_geoms={
            RiskKind.ROADS: [_road_geojson()],
            RiskKind.UTILITIES: [
                {
                    "type": "LineString",
                    "coordinates": [[PX0 - 5, PY0 - 5], [PX1 + 5, PY0 - 5]],
                }
            ],
        },
        terrain_raster=_dem(),
        building_features=[_neighbor_feature()],
    )
    sc = result.planning["_site_context"]
    assert sc["terrain"]["status"] == "ok"
    assert sc["terrain"]["earthworks"]["parcel_class"] == "niski"
    assert sc["access"]["road_access"]["has_public_road_access"] is True
    assert sc["water"]["flood_checked"] is True and sc["water"]["flood_detected"] is False
    assert sc["neighbors"] == [{"name": "sasiad-N", "height_m": 25.0}]
    # §14 scores wired (delta 2): numeric block + the explainable set.
    assert result.scores.terrain > 0.8
    assert result.scores.infrastructure > 0.5
    explained = result.planning["_scores_explained"]
    assert explained["terrain_score"]["unknown"] is False
    assert explained["terrain_score"]["positive_factors"]
    # Honest unknowns persist: GZWP / mining / groundwater are not wired.
    reasons = {u.reason for u in result.unknowns}
    assert "source_not_wired" in reasons
    # New SourceRecords flow into sources_collect.
    usecases.DEFAULT_STORE.put(result)
    sources = usecases.sources_collect(result.analysis_id)["sources"]
    ids = {s["source_id"] for s in sources}
    assert "src.terrain:test" in ids and "src.buildings:test" in ids
    # Typed site context stored for the masterplan integration (delta 1).
    from plot_agent.analysis import DEFAULT_SITE_CONTEXT_STORE

    assert DEFAULT_SITE_CONTEXT_STORE.get(result.analysis_id) is not None


def test_full_mode_flood_hard_blocker_dominates_good_scores() -> None:
    result = _analyze_full(
        feature_geoms={
            RiskKind.FLOOD: [gj_box(PX0, PY0, PX1, PY1)],  # whole parcel flooded
            RiskKind.ROADS: [_road_geojson()],
        },
        terrain_raster=_dem(),  # flat → terrain_score high
    )
    assert result.scores.terrain > 0.8  # other scores favourable...
    assert result.decision.value == "LIKELY_BLOCKED"  # ...but never summed past §14.2


def test_full_mode_kill_one_connector_is_partial_not_silent() -> None:
    result = _analyze_full(
        unavailable={RiskKind.FLOOD},
        feature_geoms={RiskKind.ROADS: [_road_geojson()]},
        terrain_raster=_dem(),
    )
    assert result.status.value == "partial"
    assert result.planning["_risk_layer_status"]["flood"] == "source_unavailable"
    assert any(
        u.reason == "source_unavailable" and "powod" in u.topic.lower() for u in result.unknowns
    )
    # The rest of the analysis still completed (terrain/road themes intact).
    assert result.planning["_site_context"]["terrain"]["status"] == "ok"


def test_full_mode_dead_terrain_source_keeps_score_unknown() -> None:
    result = _analyze_full(
        feature_geoms={RiskKind.ROADS: [_road_geojson()]},
        terrain_fail=True,
    )
    assert result.status.value == "partial"
    explained = result.planning["_scores_explained"]
    assert explained["terrain_score"]["unknown"] is True
    assert "terrain_data_unavailable" in explained["terrain_score"]["negative_factors"]
    assert result.scores.terrain == 0.0  # numeric block NOT faked


def test_map_preview_renders_site_context_layers_for_analysis() -> None:
    result = _analyze_full(
        feature_geoms={
            RiskKind.FLOOD: [gj_box(PX0, PY0, PX1, PY0 + 8)],
            RiskKind.UTILITIES: [
                {
                    "type": "LineString",
                    "coordinates": [[PX0 + 20, PY0 - 5], [PX0 + 20, PY1 + 5]],
                }
            ],
        },
        terrain_raster=_dem(),
    )
    usecases.DEFAULT_STORE.put(result)
    render = usecases.map_preview_render(result.analysis_id, "png")
    assert render.data[:8] == b"\x89PNG\r\n\x1a\n"
    roles = {(layer["name"], layer["role"]) for layer in render.style_metadata["layers"]}
    assert ("flood", "constraint_hard") in roles
    assert ("utilities", "network") in roles  # Phase 12 layer mapping
    # Review m1: an UNKNOWN analysis id is a hard error (a sample render would be
    # indistinguishable from the real map, §21); only the no-id call keeps the
    # documented Phase 3 sample-preview behaviour.
    with pytest.raises(ValueError, match="nie istnieje"):
        usecases.map_preview_render("no-such-analysis", "png")
    sample = usecases.map_preview_render(None, "png")
    assert sample.data[:8] == b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- #
# Review fixes: module isolation (M1), register dedupe (M3), EIA basis (M4),
# site-context store cap (m4)
# --------------------------------------------------------------------------- #
def test_full_mode_corrupt_terrain_raster_is_isolated_module_error() -> None:
    """Review M1: corrupt raster bytes with fetch status OK must degrade ONLY the
    terrain theme (partial + unknown), never kill the whole analysis."""
    result = _analyze_full(
        feature_geoms={RiskKind.ROADS: [_road_geojson()]},
        terrain_raster=b"NOT-A-GEOTIFF \x00\x01 garbage bytes",
    )
    assert result.status.value == "partial"
    sc = result.planning["_site_context"]
    assert sc["terrain"]["status"] == "no_data"
    # Other themes completed (NFR-REL-001).
    assert sc["access"]["road_access"]["has_public_road_access"] is True
    assert sc["water"]["flood_checked"] is True
    assert any(u.reason.startswith("module_error:") for u in result.unknowns)
    explained = result.planning["_scores_explained"]
    assert explained["terrain_score"]["unknown"] is True
    assert result.scores.terrain == 0.0  # never faked


def test_full_mode_flood_risk_not_duplicated_in_register() -> None:
    """Review M3: screening red_flags + site water both detect the flood — the
    merged register must carry exactly ONE flood risk."""
    result = _analyze_full(
        feature_geoms={
            RiskKind.FLOOD: [gj_box(PX0, PY0, PX1, PY0 + 8)],
            RiskKind.ROADS: [_road_geojson()],
        },
        terrain_raster=_dem(),
    )
    flood_risks = [r for r in result.risks if r.risk_type.value == "flood"]
    assert len(flood_risks) == 1
    # The kept entry is the screening red flag — it carries the evidence link.
    assert flood_risks[0].source_id


def test_full_mode_dead_source_yields_single_unknown_per_topic() -> None:
    """Review M3: a dead flood source must produce ONE flood unknown, not the
    screening entry + the site-module duplicate."""
    result = _analyze_full(
        unavailable={RiskKind.FLOOD},
        feature_geoms={RiskKind.ROADS: [_road_geojson()]},
        terrain_raster=_dem(),
    )
    flood_unknowns = [u for u in result.unknowns if "powodziow" in u.topic.lower()]
    assert len(flood_unknowns) == 1
    assert flood_unknowns[0].reason == "source_unavailable"
    # No exact-topic duplicates anywhere in the merged register.
    topics = [u.topic for u in result.unknowns]
    assert len(topics) == len(set(topics))


def test_full_mode_eia_screening_uses_footprint_proxy_not_raw_gfa() -> None:
    """Review M4: the EIA thresholds are powierzchnia zabudowy/terenu (ha) — raw
    GFA over-triggers. 25 000 m² GFA on the 1 200 m² mock parcel caps at the
    parcel area (0.12 ha < 2.0 ha) and labels the basis."""
    usecases.set_connectors(
        mock_connectors(
            feature_geoms={RiskKind.ROADS: [_road_geojson()]}, terrain_raster=_dem()
        )
    )
    try:
        payload = AnalysisInput.model_validate(
            {
                "input": {"parcel_id": "141201_1.0001.1867/2"},
                "analysis_mode": "full_due_diligence",
                "investment_goal": {"type": "multifamily", "target_gfa_m2": 25_000.0},
            }
        )
        result = usecases.parcel_analyze(payload, "test-ruleset")
    finally:
        usecases.set_connectors(None)
    scr = result.planning["_site_context"]["environment"]["eia_screening"]
    assert scr["area_basis"] == "gfa_capped_proxy_conservative"
    assert scr["investment_area_ha"] == pytest.approx(0.12)
    assert scr["required"] is False  # 0.12 ha < 2.0 ha mieszkaniowa threshold


def test_full_mode_eia_screening_prefers_masterplan_footprint_basis() -> None:
    """Review M4: a stored masterplan variant of the SAME analysis supplies the
    real powierzchnia_zabudowy total (0.3 ha fixture) as the screening basis."""
    import asyncio

    from plot_agent.analysis import run_full_due_diligence
    from plot_agent.drawing import DEFAULT_VARIANT_STORE
    from plot_domain import MasterplanVariant

    aid = "eia-mp-basis-1"
    DEFAULT_VARIANT_STORE.put(
        MasterplanVariant(
            id="mvar:eia-basis", analysis_id=aid,
            totals={"powierzchnia_zabudowy_m2": 3_000.0},
        )
    )
    payload = AnalysisInput.model_validate(
        {
            "input": {"parcel_id": "141201_1.0001.1867/2"},
            "analysis_mode": "full_due_diligence",
            "investment_goal": {"type": "multifamily", "target_gfa_m2": 25_000.0},
        }
    )
    result = asyncio.run(
        run_full_due_diligence(
            payload,
            connectors=mock_connectors(
                feature_geoms={RiskKind.ROADS: [_road_geojson()]},
                terrain_raster=_dem(),
            ),
            analysis_id=aid,
        )
    )
    scr = result.planning["_site_context"]["environment"]["eia_screening"]
    assert scr["area_basis"] == "masterplan_footprint"
    assert scr["investment_area_ha"] == pytest.approx(0.3)
    assert scr["required"] is False  # 0.3 ha < 2.0 ha mieszkaniowa threshold


def test_site_context_store_caps_growth() -> None:
    """Review m4: the typed contexts hold live float64 grids — the store must
    cap retained analyses (evict oldest) instead of growing unbounded."""
    from plot_agent.analysis.factory import SITE_CONTEXT_STORE_MAX, SiteContextStore

    store = SiteContextStore()
    for i in range(20):
        store.put(f"an-{i}", {"i": i})
    assert SITE_CONTEXT_STORE_MAX == 16
    assert len(store) <= SITE_CONTEXT_STORE_MAX
    assert store.get("an-19") == {"i": 19}  # latest retained
    assert store.get("an-0") is None  # oldest evicted


# --------------------------------------------------------------------------- #
# Delta 3: analysis binding of propose_layout / overrides / koncepcja lineage
# --------------------------------------------------------------------------- #
def _bound_analysis() -> Any:
    return _analyze_full(
        feature_geoms={
            RiskKind.ROADS: [_road_geojson()],
            # Flood strip across the parcel's NORTH edge (stage flags fixture).
            RiskKind.FLOOD: [gj_box(PX0, PY1 - 5, PX1, PY1)],
        },
        terrain_raster=_dem(slope_pct=3.0),
        building_features=[_neighbor_feature()],
    )


def test_propose_layout_bound_to_analysis_uses_its_parcel_and_site_checks() -> None:
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT, DEFAULT_VARIANT_STORE

    DEFAULT_MASTERPLAN_AUDIT.clear()
    result = _bound_analysis()
    usecases.DEFAULT_STORE.put(result)
    aid = result.analysis_id
    try:
        out = usecases.propose_layout_render(
            _masterplan_payload(), None, "iteracja 1", analysis_id=aid
        )
        # Evaluated on the ANALYSIS parcel: the payload coordinates live inside
        # the ULDK parcel, far outside the (0,0)-based sample context — a bound
        # run has no hard violations, an unbound one must reject the placement.
        assert out["valid"] is True
        assert out["analysis_id"] == aid
        assert out["metrics_resource"].startswith(f"analysis://{aid}/masterplan/")
        # Variants + audit scope to the analysis (lineage isolation, F1).
        latest = DEFAULT_VARIANT_STORE.latest(analysis_id=aid)
        assert latest is not None and latest.id == out["variant_id"]
        assert out["audit"]["analysis_id"] == aid
        # Delta 1 site checks travel with the result + the stored variant.
        checks = out["site_checks"]
        assert checks is not None
        assert checks["earthworks_per_building"]["A1"]["status"] == "ok"
        assert checks["zjazd"]["status"] == "pass"
        stages = {c["stage"]: c for c in checks["flood_stages"]}
        assert stages[1]["status"] == "clear"  # A1 sits south, flood is north
        assert stages[1]["envelope_after_flood_clip_m2"] <= stages[1]["envelope_area_m2"]
        assert checks["heritage_interventions"] == []  # no heritage zone fixture
        assert latest.metadata["site_checks"]["zjazd"]["status"] == "pass"
    finally:
        DEFAULT_MASTERPLAN_AUDIT.clear()


def test_propose_layout_unbound_rejects_bound_coordinates() -> None:
    # The same payload WITHOUT analysis_id evaluates against the sample context
    # → the placement is outside that parcel → hard violation, never accepted.
    out = usecases.propose_layout_render(_masterplan_payload(), None, None)
    assert out["valid"] is False and out["accepted"] is False


def test_propose_layout_unknown_analysis_id_raises_clearly() -> None:
    with pytest.raises(ValueError, match="nie istnieje"):
        usecases.propose_layout_render(
            _masterplan_payload(), None, None, analysis_id="missing-id"
        )


# --------------------------------------------------------------------------- #
# Review M2 (F-0443/0445): the freshness verdict is stored at full-DD time and
# CONSUMED by the analysis-bound propose_layout path — real path, no manual
# mode passing.
# --------------------------------------------------------------------------- #
def test_drawing_loop_passes_evaluation_mode_into_inter_building_checks(
    tmp_path: Any,
) -> None:
    """The loop's ``evaluation_mode`` genuinely reaches run_inter_building_checks
    (trace-recorded by plot_rules.evaluate) — it is not hardcoded."""
    from plot_agent.drawing import DrawingLoop, MasterplanProposal

    result = _bound_analysis()
    usecases.DEFAULT_STORE.put(result)
    context = usecases._analysis_drawing_context(result.analysis_id)
    proposal = MasterplanProposal.model_validate(_masterplan_payload())

    def _modes(loop: Any) -> set[str]:
        res = loop.iterate_masterplan(proposal)
        return {
            c.trace.get("mode")
            for c in res.inter_building_checks
            if isinstance(c.trace, dict) and c.trace.get("mode")
        }

    strict_loop = DrawingLoop(
        context=context, evaluation_mode="strict", artifact_store_base=tmp_path
    )
    assert _modes(strict_loop) == {"strict"}
    default_loop = DrawingLoop(context=context, artifact_store_base=tmp_path)
    assert _modes(default_loop) == {"conservative"}  # the safe default


def test_stale_bound_analysis_forces_conservative_checks_through_propose_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review M2 regression: a bound analysis whose sources are STALE (per the
    env max-age policy) stores a degraded freshness verdict at full-DD time,
    and the bound propose_layout evaluates the inter-building checks under
    conservative mode + surfaces the banner — the model SEES the basis."""
    import plot_shared.config as cfg

    monkeypatch.setenv("PLOT_SOURCE_MAX_AGE_DAYS", "1e-09")  # everything is stale
    cfg.get_settings.cache_clear()
    try:
        result = _bound_analysis()
    finally:
        monkeypatch.delenv("PLOT_SOURCE_MAX_AGE_DAYS", raising=False)
        cfg.get_settings.cache_clear()
    verdict = result.planning["_freshness"]
    assert verdict["degraded"] is True
    assert verdict["stale_sources"]  # names the stale sources

    usecases.DEFAULT_STORE.put(result)
    out = usecases.propose_layout_render(
        _masterplan_payload(), None, None, analysis_id=result.analysis_id
    )
    assert out["evaluation_mode"] == "conservative"
    assert out["freshness"]["degraded"] is True
    assert out["freshness"]["stale_sources"]
    assert "tryb konserwatywny" in out["freshness"]["banner"]
    # The checks were genuinely evaluated conservative (trace through the loop).
    modes = {
        c.get("trace", {}).get("mode")
        for c in out["inter_building_checks"]
        if c.get("trace", {}).get("mode")
    }
    assert modes == {"conservative"}


def test_fresh_bound_analysis_reports_verdict_without_stale_banner() -> None:
    """Fresh sources: the verdict travels with the result (degraded=False, no
    banner) and the mode stays the validators' conservative default — freshness
    never RELAXES the evaluation mode (§21)."""
    result = _bound_analysis()
    verdict = result.planning["_freshness"]
    assert verdict["degraded"] is False
    assert verdict["stale_sources"] == []

    usecases.DEFAULT_STORE.put(result)
    out = usecases.propose_layout_render(
        _masterplan_payload(), None, None, analysis_id=result.analysis_id
    )
    assert out["evaluation_mode"] == "conservative"  # default, never relaxed
    assert out["freshness"]["degraded"] is False
    assert "banner" not in out["freshness"]


def test_manual_override_scoped_to_stored_analysis_is_active() -> None:
    result = _bound_analysis()
    usecases.DEFAULT_STORE.put(result)
    from plot_planning.wt_validators import RULE_PPOZ_271

    out = usecases.manual_override(
        analysis_id=result.analysis_id,
        target_type="rule",
        target_id=RULE_PPOZ_271,
        reason="Sciana oddzielenia ppoz potwierdzona",
        user_id="ekspert@example.com",
        after={"status": "pass"},
    )
    # Phase 12: a STORED analysis id is reachable via propose_layout binding.
    assert out["applied"] is True and out["status"] == "active"
    assert result.analysis_id in out["note"]


def test_koncepcja_report_pulls_bound_lineage() -> None:
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT

    DEFAULT_MASTERPLAN_AUDIT.clear()
    result = _bound_analysis()
    usecases.DEFAULT_STORE.put(result)
    aid = result.analysis_id
    try:
        usecases.propose_layout_render(
            _masterplan_payload(), None, "RATIONALE-PIERWSZA", analysis_id=aid
        )
        out2 = usecases.propose_layout_render(
            _masterplan_payload(), None, "RATIONALE-DRUGA", analysis_id=aid
        )
        # Lineage chain: iteration 2's parent is iteration 1's variant.
        assert out2["audit"]["parent_variant_id"]

        report = usecases.report_generate(aid, "koncepcja")  # no explicit variant
        assert report["status"] == "rendered"
        assert report["analysis_id"] == aid
        assert report["variant_id"] == out2["variant_id"]  # latest OF THIS analysis
        md = report["content"]
        assert "RATIONALE-PIERWSZA" in md and "RATIONALE-DRUGA" in md
        assert md.index("RATIONALE-PIERWSZA") < md.index("RATIONALE-DRUGA")
    finally:
        DEFAULT_MASTERPLAN_AUDIT.clear()


def test_koncepcja_bound_variant_missing_analysis_geometry_raises() -> None:
    """Review m2: a BOUND variant whose analysis lost its parcel geometry must
    hard-error — never silently render on the Phase 3 sample geometry (§21)."""
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT
    from plot_domain import AnalysisResult
    from plot_domain.enums import AnalysisStatus, Decision

    DEFAULT_MASTERPLAN_AUDIT.clear()
    result = _bound_analysis()
    usecases.DEFAULT_STORE.put(result)
    aid = result.analysis_id
    try:
        out = usecases.propose_layout_render(
            _masterplan_payload(), None, None, analysis_id=aid
        )
        assert out["variant_id"]
        # The stored analysis loses its parcel geometry (store overwrite models a
        # reset / Phase 13 persistence gap).
        usecases.DEFAULT_STORE.put(
            AnalysisResult(
                analysis_id=aid,
                status=AnalysisStatus.PARTIAL,
                decision=Decision.NEEDS_MANUAL_REVIEW,
            )
        )
        with pytest.raises(ValueError, match="geometri"):
            usecases.report_generate(aid, "koncepcja", variant_id=out["variant_id"])
    finally:
        DEFAULT_MASTERPLAN_AUDIT.clear()
