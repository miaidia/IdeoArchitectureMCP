"""Phase 11 part A — design-brief generator tests (plan §11.1.1).

Covers: composition axes on the irregular riverside fixture (non-empty + sane
azimuth), skeleton/medial-axis robustness over the fixture set (no crash on concave
L / collinear / sliver), south-frontage + context classification, heritage echo,
buildable summary, rule ids in the Markdown, and the
``analysis://{id}/design-brief`` resource end-to-end via the MCP in-memory session.
"""

from __future__ import annotations

import importlib

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextResourceContents
from plot_domain import AnalysisResult, BuildableEnvelope, Parcel
from plot_domain.enums import AnalysisStatus, Decision
from plot_planning import composition_axes, generate_design_brief
from plot_planning.brief import HERITAGE_NOTE
from plot_rules import load_rulesets
from pydantic import AnyUrl
from shapely.geometry import mapping
from tests.masterplan_fixtures import (
    riverside_context_edges,
    riverside_heritage_footprints,
    riverside_indicators,
    riverside_parcel,
    skeleton_robustness_parcels,
)


@pytest.fixture(scope="module")
def registry():
    return load_rulesets("rulesets/PL")


@pytest.fixture(scope="module")
def brief(registry):
    return generate_design_brief(
        riverside_parcel(),
        registry=registry,
        indicators=riverside_indicators(),
        heritage_footprints=riverside_heritage_footprints(),
        context_edges=riverside_context_edges(),
        analysis_id="brief-test-1",
    )


def test_riverside_parcel_is_the_5ha_golden_shape() -> None:
    parcel = riverside_parcel()
    assert 45_000 <= parcel.area <= 55_000  # ~5 ha (plan §11.3 fixture)
    assert parcel.area / parcel.convex_hull.area < 0.97  # genuinely concave


def test_composition_axes_nonempty_and_sane(brief) -> None:
    axes = brief.axes
    assert axes.method in ("straight_skeleton", "medial_axis_voronoi")
    assert axes.spine is not None and axes.spine_length_m > 0
    assert 0.0 <= axes.dominant_azimuth_deg < 180.0
    # The riverside parcel is an E-W slab: the dominant composition axis must be
    # broadly east-west (the smoke value is ~99°; main_axis cross-check ~82°).
    assert 60.0 <= axes.dominant_azimuth_deg <= 140.0
    assert all(0.0 <= az < 180.0 for az in axes.secondary_azimuths_deg)


def test_skeleton_robustness_no_crash_on_fixture_set() -> None:
    # Property-ish sweep (plan part A): concave L, collinear vertices, sliver —
    # axes extraction must return a usable result for every one of them.
    for parcel in skeleton_robustness_parcels():
        axes = composition_axes(parcel)
        assert axes.spine is not None
        assert axes.spine_length_m > 0
        assert 0.0 <= axes.dominant_azimuth_deg < 180.0


def test_south_frontage_identified(brief) -> None:
    south = [e for e in brief.frontages if e.south_exposure_score > 0.5]
    assert south, "the riverside parcel's south boundary must score as south-facing"
    # The water runs along the south edges: south-facing frontage is the quiet side.
    assert any(e.context == "woda" for e in south)


def test_context_edges_classified(brief) -> None:
    kinds = {e.context for e in brief.frontages}
    assert "woda" in kinds and "kolej" in kinds and "droga_publiczna" in kinds
    by_kind = {e.context: e.character for e in brief.frontages if e.context}
    assert by_kind["woda"] == "quiet"
    assert by_kind["kolej"] == "noise"
    assert by_kind["droga_publiczna"] == "noise"


def test_heritage_echoed_with_retention_note(brief) -> None:
    assert len(brief.heritage) == 2
    names = {h.name for h in brief.heritage}
    assert names == {"Hala elektrowni", "Komin"}
    assert all(h.note == HERITAGE_NOTE for h in brief.heritage)
    assert "zabytek_do_remontu" in HERITAGE_NOTE


def test_typology_recommendations_present_with_why(brief) -> None:
    assert brief.typologies, "the brief must carry ranked typology suggestions"
    assert all(t.why for t in brief.typologies)
    assert all(t.basis == "design_practice" for t in brief.typologies)
    scores = [t.score for t in brief.typologies]
    assert scores == sorted(scores, reverse=True)


def test_indicators_echo_and_missing_list(brief) -> None:
    assert brief.indicators["max_intensity"] == 1.8
    # linia_zabudowy / dach_constraints were not provided → listed missing.
    assert "linia_zabudowy" in brief.missing_indicators
    assert "max_intensity" not in brief.missing_indicators


def test_markdown_contains_rule_ids_and_sections(brief) -> None:
    md = brief.to_markdown()
    assert "PL-WT-12-SETBACKS-001" in md
    assert "PL-WT-40-PLAC-ZABAW-001" in md
    assert "Osie kompozycyjne" in md
    assert "Pierzeje i orientacja" in md
    assert "zabytek_do_remontu" in md
    assert "Rekomendowane typologie" in md
    # Typology suggestions never appear among the hard rules in force.
    rules_section = md.split("## 7.")[1].split("## 8.")[0]
    assert "PL-TYP-" not in rules_section


def test_buildable_summary_from_envelope(registry) -> None:
    parcel = riverside_parcel()
    env = BuildableEnvelope(
        id="env-1",
        geometry=dict(mapping(parcel.buffer(-10))),
        area_m2=round(parcel.buffer(-10).area, 2),
        confidence=0.8,
    )
    brief = generate_design_brief(parcel, registry=registry, envelope=env)
    assert brief.buildable.envelope_area_m2 == env.area_m2
    assert brief.buildable.percent_of_parcel is not None
    assert 0 < brief.buildable.percent_of_parcel < 100


# --------------------------------------------------------------------------- #
# Resource end-to-end via the MCP in-memory session
# --------------------------------------------------------------------------- #
@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _load_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    return importlib.reload(server).mcp


@pytest.mark.anyio
async def test_design_brief_resource_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    from plot_agent.analysis import DEFAULT_STORE

    parcel = riverside_parcel()
    analysis_id = "brief-e2e-1"
    DEFAULT_STORE.put(
        AnalysisResult(
            analysis_id=analysis_id,
            status=AnalysisStatus.COMPLETE,
            decision=Decision.OK,
            parcel=Parcel(id="p-1", geometry=dict(mapping(parcel))),
            buildable_envelope=BuildableEnvelope(
                id="env-e2e",
                analysis_id=analysis_id,
                geometry=dict(mapping(parcel.buffer(-8))),
                area_m2=round(parcel.buffer(-8).area, 2),
                confidence=0.7,
            ),
        )
    )
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        res = await client.read_resource(AnyUrl(f"analysis://{analysis_id}/design-brief"))
    assert len(res.contents) == 1
    content = res.contents[0]
    assert isinstance(content, TextResourceContents)
    assert content.mimeType == "text/markdown"
    md = content.text
    assert "# Design brief" in md
    assert "PL-WT-12-SETBACKS-001" in md  # hard rules in force, by id
    assert "Rekomendowane typologie" in md
    # The generated brief is cached in the store (variants pattern).
    from plot_planning import DEFAULT_BRIEF_STORE

    assert DEFAULT_BRIEF_STORE.get(analysis_id) is not None


@pytest.mark.anyio
async def test_design_brief_resource_unknown_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = _load_server(monkeypatch)
    async with create_connected_server_and_client_session(mcp) as client:
        res = await client.read_resource(AnyUrl("analysis://no-such-analysis/design-brief"))
    content = res.contents[0]
    assert isinstance(content, TextResourceContents)
    assert "parcel_analyze" in content.text  # honest not-found note, no fabricated brief
