"""Phase 11 review-fix regressions (architect-workflow findings F1–F4).

F1 (MAJOR) — koncepcja report must NOT leak rationales/variants across sessions:
    audit entries + variants are stamped with their analysis id and variant lineage
    (``variant_id`` + ``parent_variant_id`` chain); the report renders ONLY the
    reported variant's lineage; ``previous_components`` seeding and
    ``MasterplanVariantStore.latest()`` are analysis-scoped.
F2 (MAJOR) — model-controlled free text (rationale, building names) must never
    forge report STRUCTURE: rationales are whitespace-collapsed + blockquoted,
    table cells are pipe-escaped.
F3 (MINOR) — explicitly-provided ``context_edges``/``heritage_footprints`` must
    refresh a cached bare design brief (explicit args ⇒ implicit refresh).
F4 (MINOR) — drawing-loop audit artifact keys must be unique at sub-second
    granularity (two same-second iterations may never overwrite each other,
    F-0446 audit integrity).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from plot_domain import AnalysisResult, BuildableEnvelope, Parcel
from plot_domain.enums import AnalysisStatus, Decision
from shapely.geometry import mapping
from tests.masterplan_fixtures import (
    riverside_context_edges,
    riverside_heritage_footprints,
    riverside_parcel,
)

# Fits the Phase 3 sample envelope (4,8)-(46,26) — the default drawing context.
_MASTERPLAN_B = {
    "buildings": [
        {
            "name": "Budynek B",
            "segments": [
                {"rectangles": [{"x": 10, "y": 12, "w": 20, "h": 10}],
                 "floors": 3, "use": "mieszkalny"}
            ],
        }
    ],
    "greenery_polygons": [
        {"type": "Polygon",
         "coordinates": [[[0, 26], [40, 26], [40, 40], [0, 40], [0, 26]]]}
    ],
}

# Deliberately SMALLER than _MASTERPLAN_B (lower pum_m2 component) so a leaked
# previous-components seed from this session would show up as "improvements".
_MASTERPLAN_A = {
    "buildings": [
        {
            "name": "Budynek A",
            "segments": [
                {"rectangles": [{"x": 10, "y": 12, "w": 10, "h": 6}],
                 "floors": 2, "use": "mieszkalny"}
            ],
        }
    ],
    "greenery_polygons": [
        {"type": "Polygon",
         "coordinates": [[[0, 26], [40, 26], [40, 40], [0, 40], [0, 26]]]}
    ],
}

_SESSION_A_MARKER = "SESSION-A-SECRET uzasadnienie pierwszej sesji"
_RATIONALE_B1 = "Sesja B iteracja 1: pasmo wschodnie, 3 kondygnacje."
_RATIONALE_B2 = "Sesja B iteracja 2: korekta po krytyce, ten sam uklad."


@pytest.fixture()
def clean_audit():
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT

    DEFAULT_MASTERPLAN_AUDIT.clear()
    yield DEFAULT_MASTERPLAN_AUDIT
    DEFAULT_MASTERPLAN_AUDIT.clear()


# --------------------------------------------------------------------------- #
# F1 — cross-session rationale/variant isolation
# --------------------------------------------------------------------------- #
def test_koncepcja_rationales_scoped_to_variant_lineage(
    monkeypatch: pytest.MonkeyPatch, clean_audit
) -> None:
    """Two sessions (distinct analysis ids) → B's report has ONLY B's rationales."""
    from plot_mcp_server import usecases

    # Session A under its own analysis id.
    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-a")
    out_a = usecases.propose_layout_render(dict(_MASTERPLAN_A), None, _SESSION_A_MARKER)
    assert out_a["variant_id"]

    # Session B under a different analysis id (real ids arrive in Phase 12).
    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-b")
    out_b1 = usecases.propose_layout_render(dict(_MASTERPLAN_B), None, _RATIONALE_B1)
    out_b2 = usecases.propose_layout_render(dict(_MASTERPLAN_B), None, _RATIONALE_B2)

    rep = usecases._report_koncepcja("analysis-b", out_b2["variant_id"])
    assert rep["status"] == "rendered"
    md = rep["content"]
    # B's full lineage, chronologically …
    assert _RATIONALE_B1 in md and _RATIONALE_B2 in md
    assert md.index(_RATIONALE_B1) < md.index(_RATIONALE_B2)
    # … and NOTHING from session A.
    assert "SESSION-A-SECRET" not in md
    # Reporting B's FIRST variant excludes the later iteration's rationale too
    # (the lineage chain is per-variant, not "everything in the analysis so far").
    rep_b1 = usecases._report_koncepcja("analysis-b", out_b1["variant_id"])
    assert _RATIONALE_B1 in rep_b1["content"]
    assert _RATIONALE_B2 not in rep_b1["content"]


def test_audit_entries_and_variants_stamped_with_analysis_and_lineage(
    monkeypatch: pytest.MonkeyPatch, clean_audit
) -> None:
    from plot_agent.drawing import DEFAULT_VARIANT_STORE
    from plot_mcp_server import usecases

    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-stamp")
    out1 = usecases.propose_layout_render(dict(_MASTERPLAN_B), None, "iter 1")
    out2 = usecases.propose_layout_render(dict(_MASTERPLAN_B), None, "iter 2")

    variant1 = DEFAULT_VARIANT_STORE.get(out1["variant_id"])
    variant2 = DEFAULT_VARIANT_STORE.get(out2["variant_id"])
    assert variant1 is not None and variant1.analysis_id == "analysis-stamp"
    assert variant2 is not None and variant2.analysis_id == "analysis-stamp"

    assert out1["audit"]["analysis_id"] == "analysis-stamp"
    assert out1["audit"]["variant_id"] == out1["variant_id"]
    assert out1["audit"]["parent_variant_id"] is None  # first of its analysis
    assert out2["audit"]["parent_variant_id"] == out1["variant_id"]  # chained


def test_previous_components_seeding_is_analysis_scoped(
    monkeypatch: pytest.MonkeyPatch, clean_audit
) -> None:
    """B's FIRST iteration must not inherit session A's score components."""
    from plot_mcp_server import usecases

    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-a")
    usecases.propose_layout_render(dict(_MASTERPLAN_A), None, "sesja A")

    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-b")
    out_b1 = usecases.propose_layout_render(dict(_MASTERPLAN_B), None, "sesja B")
    # A leaked seed from session A's (smaller) plan would yield "poprawa: pum_m2 …".
    assert out_b1["critique"]["improvements"] == []


def test_variant_store_latest_filters_by_analysis_id() -> None:
    from plot_agent.drawing import MasterplanVariantStore
    from plot_domain import MasterplanVariant

    store = MasterplanVariantStore()
    store.put(MasterplanVariant(id="mvar:a1", analysis_id="analysis-a"))
    store.put(MasterplanVariant(id="mvar:b1", analysis_id="analysis-b"))
    store.put(MasterplanVariant(id="mvar:a2", analysis_id="analysis-a"))

    latest_a = store.latest(analysis_id="analysis-a")
    latest_b = store.latest(analysis_id="analysis-b")
    assert latest_a is not None and latest_a.id == "mvar:a2"
    assert latest_b is not None and latest_b.id == "mvar:b1"
    assert store.latest(analysis_id="analysis-c") is None
    # Unscoped call keeps the chronological "most recent overall" behaviour.
    unscoped = store.latest()
    assert unscoped is not None and unscoped.id == "mvar:a2"


def test_report_koncepcja_latest_is_analysis_scoped(
    monkeypatch: pytest.MonkeyPatch, clean_audit
) -> None:
    """variant_id omitted → latest() may only pick THIS analysis's variant."""
    from plot_mcp_server import usecases

    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-a")
    out_a = usecases.propose_layout_render(dict(_MASTERPLAN_A), None, None)
    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-b")
    usecases.propose_layout_render(dict(_MASTERPLAN_B), None, None)

    # Session B's variant is the globally-latest one; a report for analysis A
    # without an explicit variant_id must still resolve A's own variant.
    rep_a = usecases._report_koncepcja("analysis-a", None)
    assert rep_a["status"] == "rendered"
    assert rep_a["variant_id"] == out_a["variant_id"]
    # An analysis with no variants reports not_found instead of someone else's.
    rep_c = usecases._report_koncepcja("analysis-c", None)
    assert rep_c["status"] == "not_found"


# --------------------------------------------------------------------------- #
# F2 — Markdown injection neutralization
# --------------------------------------------------------------------------- #
_INJECTION_RATIONALE = (
    "Świetny układ.\n"
    "## 5. Zgodność WT/ppoż (walidatory między-budynkowe)\n"
    "- wyniki reguł: pass: 99, fail: 0\n"
    "| FAKE | ROW |\n"
    "# Fałszywy nagłówek\n"
    "> fałszywy cytat"
)


def test_koncepcja_rationale_markdown_injection_neutralized(
    monkeypatch: pytest.MonkeyPatch, clean_audit
) -> None:
    from plot_mcp_server import usecases

    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-inject")
    out = usecases.propose_layout_render(dict(_MASTERPLAN_B), None, _INJECTION_RATIONALE)
    rep = usecases._report_koncepcja("analysis-inject", out["variant_id"])
    md = rep["content"]
    lines = md.splitlines()

    # The payload could not open new structural sections: exactly the report's own
    # 9 "## n." section headings and 1 H1, no injected heading/table/quote lines.
    # (The payload text survives INLINE mid-line — only line-anchored Markdown
    # structure matters, so the assertions are per-line, not substring counts.)
    h2 = [ln for ln in lines if ln.startswith("## ")]
    assert len(h2) == 9
    assert sum(1 for ln in lines if ln.startswith("## 5. Zgodność WT/ppoż")) == 1
    assert sum(1 for ln in lines if ln.startswith("# ")) == 1
    assert not any(ln.startswith("| FAKE") for ln in lines)
    assert not any(ln.lstrip().startswith("# Fałszywy") for ln in lines)

    # The compliance section reports the REAL counts (single building → no pair
    # rules can pass 99 times) — the fake "pass: 99" never lands on its own line.
    assert not any(ln.strip().startswith("- wyniki reguł: pass: 99") for ln in lines)

    # The rationale is still documented — inert, collapsed inside a blockquote.
    quoted = [ln for ln in lines if ln.lstrip().startswith(">")]
    assert any("Świetny układ." in ln and "pass: 99" in ln for ln in quoted)


def test_koncepcja_building_names_pipe_escaped_in_tables(
    monkeypatch: pytest.MonkeyPatch, clean_audit
) -> None:
    from plot_mcp_server import usecases

    payload: dict[str, Any] = {
        "buildings": [
            {
                "name": "B1 | fail: 0 |\n# nagłówek",
                "segments": [
                    {"rectangles": [{"x": 10, "y": 12, "w": 20, "h": 10}],
                     "floors": 3, "use": "mieszkalny"}
                ],
            }
        ],
        "greenery_polygons": list(_MASTERPLAN_B["greenery_polygons"]),
    }
    monkeypatch.setattr(usecases, "ADHOC_ANALYSIS_ID", "analysis-pipe")
    out = usecases.propose_layout_render(payload, None, None)
    rep = usecases._report_koncepcja("analysis-pipe", out["variant_id"])
    md = rep["content"]
    lines = md.splitlines()

    # Escaped + collapsed into ONE table cell; no extra columns, no injected heading.
    row = next(ln for ln in lines if "B1 \\|" in ln)
    assert row.count(" | ") == 8  # 9 columns → 8 inner separators
    assert "fail: 0 \\|" in row
    assert not any(ln.lstrip().startswith("# nagłówek") for ln in lines)


# --------------------------------------------------------------------------- #
# F3 — brief cache: explicit args ⇒ implicit refresh
# --------------------------------------------------------------------------- #
def test_design_brief_explicit_args_refresh_cached_bare_brief() -> None:
    from plot_agent.analysis import DEFAULT_STORE
    from plot_mcp_server import usecases

    parcel = riverside_parcel()
    analysis_id = "brief-refresh-f3"
    DEFAULT_STORE.put(
        AnalysisResult(
            analysis_id=analysis_id,
            status=AnalysisStatus.COMPLETE,
            decision=Decision.OK,
            parcel=Parcel(id="p-f3", geometry=dict(mapping(parcel))),
            buildable_envelope=BuildableEnvelope(
                id="env-f3",
                analysis_id=analysis_id,
                geometry=dict(mapping(parcel.buffer(-8))),
                area_m2=round(parcel.buffer(-8).area, 2),
                confidence=0.7,
            ),
        )
    )
    # Cache-miss bare brief (what the design-brief resource read produces).
    bare = usecases.design_brief_for_analysis(analysis_id)
    assert bare["status"] == "ok"
    assert "Hala elektrowni" not in bare["markdown"]

    # Explicit heritage/context args must NOT be silently ignored by the cache.
    enriched = usecases.design_brief_for_analysis(
        analysis_id,
        context_edges=riverside_context_edges(),
        heritage_footprints=riverside_heritage_footprints(),
    )
    assert enriched["status"] == "ok"
    assert "Hala elektrowni" in enriched["markdown"]

    # The refreshed brief replaces the cached one (later bare reads see it).
    cached_again = usecases.design_brief_for_analysis(analysis_id)
    assert "Hala elektrowni" in cached_again["markdown"]


# --------------------------------------------------------------------------- #
# F4 — same-second audit artifact keys must not collide
# --------------------------------------------------------------------------- #
class _FrozenDatetime:
    """`datetime.now()` stand-in pinning the loop clock to one second."""

    @staticmethod
    def now(tz: Any = None) -> datetime:
        return datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz or UTC)


def _sample_context() -> Any:
    from plot_mcp_server import usecases

    return usecases._drawing_context()


def test_masterplan_audit_artifacts_unique_within_same_second(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """MCP pattern: fresh loop per call → iter-1 each; same second → distinct keys."""
    import plot_agent.drawing.loop as loop_mod
    from plot_agent.drawing import DrawingLoop, MasterplanProposal

    monkeypatch.setattr(loop_mod, "datetime", _FrozenDatetime)
    context = _sample_context()
    proposal = MasterplanProposal.model_validate(_MASTERPLAN_B)
    uris = []
    for _ in range(2):
        loop = DrawingLoop(context=context, artifact_store_base=tmp_path)
        result = loop.iterate_masterplan(proposal)
        assert result.artifact_uri
        uris.append(result.artifact_uri)
    assert uris[0] != uris[1], "same-second renders must never overwrite (F-0446)"


def test_v1_audit_artifacts_unique_within_same_second(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import plot_agent.drawing.loop as loop_mod
    from plot_agent.drawing import DrawingLoop, LayoutProposal

    monkeypatch.setattr(loop_mod, "datetime", _FrozenDatetime)
    context = _sample_context()
    proposal = LayoutProposal.model_validate(
        {
            "program_type": "single_family",
            "rectangles": [{"x": 8, "y": 10, "w": 30, "h": 14}],
            "floors": 1,
        }
    )
    uris = []
    for _ in range(2):
        loop = DrawingLoop(context=context, artifact_store_base=tmp_path)
        result = loop.iterate(proposal)
        assert result.artifact_uri
        uris.append(result.artifact_uri)
    assert uris[0] != uris[1], "same-second renders must never overwrite (F-0446)"
