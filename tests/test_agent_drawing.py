"""Phase 4 generative drawing-loop tests (IMPLEMENTATION_PLAN.md §4.3 verification).

Covers the required evidence:
  * guardrail: a footprint that pokes outside the envelope / into a hard constraint is
    REJECTED (accepted=False, violation listed) even with a HIGH raw coverage score; a
    compliant proposal scores higher, is accepted, and is stored as an exemplar (§4.4).
  * exemplar recall: a stored exemplar is recalled for a similar shape_class/program.
  * audit: every iteration appears in the audit log with inputs/score/artifact-uri/timestamp.
"""

from __future__ import annotations

from pathlib import Path

from plot_agent.context import AnalysisContext
from plot_agent.drawing import (
    DrawingExemplarStore,
    DrawingLoop,
    LayoutProposal,
    PlacedRectangle,
    score_proposal,
    shape_class_for,
    validate_hard,
)
from plot_agent.selfimprove.scenarios import _sample_geometry

GREENERY = {"type": "Polygon", "coordinates": [[[0, 30], [30, 30], [30, 40], [0, 40], [0, 30]]]}


def _context(tmp_rulesets: str | None = None) -> AnalysisContext:
    g = _sample_geometry()
    return AnalysisContext.with_loaded_rules(
        parcel=g["parcel"],
        buildable_envelope=g["envelope"],
        ruleset_dir=tmp_rulesets or "rulesets/PL",
        hard_constraints=[g["hard"]],
        soft_constraints=[g["soft"]],
    )


def _compliant() -> LayoutProposal:
    # Inside the buildable envelope (4,8)-(46,26); 30x14 at (8,10).
    return LayoutProposal(
        program_type="single_family",
        rectangles=[PlacedRectangle(x=8, y=10, w=30, h=14)],
        floors=1,
        parking_count=2,
        greenery_polygons=[GREENERY],
    )


def _spilling_high_coverage() -> LayoutProposal:
    # 44x34 from (5,5): coverage ~0.75 (HIGH) but spills outside the envelope AND into
    # the hard no-build strip (0,0)-(50,6).
    return LayoutProposal(
        program_type="single_family",
        rectangles=[PlacedRectangle(x=5, y=5, w=44, h=34)],
    )


def test_hard_violation_rejected_despite_high_coverage(tmp_path: Path) -> None:
    ctx = _context()
    bad = _spilling_high_coverage()
    good = _compliant()

    # The hard guard runs and flags violations.
    violations = validate_hard(bad, ctx)
    assert violations, "expected hard violations for a spilling footprint"
    kinds = {v.kind for v in violations}
    assert "outside_envelope" in kinds
    assert "intersects_hard_constraint" in kinds

    bad_score = score_proposal(bad, ctx)
    good_score = score_proposal(good, ctx)

    # The spilling proposal has a HIGH raw coverage ratio ...
    assert bad_score.components["coverage_ratio"] > 0.5
    # ... yet it is INVALID (a hard blocker dominates — §14.2) and cannot be accepted.
    assert bad_score.valid is False
    assert bad_score.total <= 0.0
    assert bad_score.violations

    # The compliant proposal is valid and scores higher than the rejected one's total.
    assert good_score.valid is True
    assert good_score.total > bad_score.total

    # End-to-end through the loop: bad is NOT accepted, good IS accepted + stored.
    store = DrawingExemplarStore(base_dir=tmp_path / "exemplars")
    loop = DrawingLoop(
        context=ctx,
        exemplar_store=store,
        artifact_store_base=tmp_path / "art",
    )
    bad_res = loop.iterate(bad)
    good_res = loop.iterate(good)
    assert bad_res.accepted is False
    assert bad_res.exemplar is None
    assert good_res.accepted is True
    assert good_res.exemplar is not None

    shape_class = shape_class_for(ctx.parcel_geom())
    recalled = store.recall(shape_class, "single_family", k=3)
    assert recalled, "compliant accepted proposal must be stored as an exemplar"
    assert recalled[0].score_total == good_res.score.total


def test_exemplar_recall_for_similar_context(tmp_path: Path) -> None:
    ctx = _context()
    store = DrawingExemplarStore(base_dir=tmp_path / "exemplars")
    loop = DrawingLoop(context=ctx, exemplar_store=store, artifact_store_base=tmp_path / "art")
    res = loop.iterate(_compliant())
    assert res.accepted is True

    # A SECOND similar context (same sample shape class + program) recalls the exemplar.
    shape_class = shape_class_for(ctx.parcel_geom())
    recalled = store.recall(shape_class, "single_family", k=5)
    assert len(recalled) == 1
    assert recalled[0].program_type == "single_family"
    assert recalled[0].shape_class == shape_class

    # And it exports to a JSONL fine-tuning dataset (collect-only — §4.1.B.5).
    out = tmp_path / "exemplars.jsonl"
    n = store.export_jsonl(out)
    assert n == 1
    assert out.read_text().strip().count("\n") == 0  # exactly one line


def test_every_iteration_audited(tmp_path: Path) -> None:
    ctx = _context()
    loop = DrawingLoop(
        context=ctx,
        exemplar_store=DrawingExemplarStore(base_dir=tmp_path / "exemplars"),
        artifact_store_base=tmp_path / "art",
    )
    loop.iterate(_spilling_high_coverage())
    loop.iterate(_compliant())

    assert len(loop.audit_log) == 2, "every iteration must be audited (F-0446)"
    for entry in loop.audit_log:
        d = entry.to_dict()
        assert d["inputs"], "audit must record the proposal inputs"
        assert "total" in d and isinstance(d["total"], float)  # score
        assert d["artifact_uri"], "audit must record the rendered artifact uri"
        assert d["timestamp"], "audit must record a timestamp"
    # First (spilling) is not accepted; second (compliant) is.
    assert loop.audit_log[0].accepted is False
    assert loop.audit_log[1].accepted is True


def test_render_returns_png_bytes(tmp_path: Path) -> None:
    ctx = _context()
    loop = DrawingLoop(
        context=ctx,
        exemplar_store=DrawingExemplarStore(base_dir=tmp_path / "exemplars"),
        artifact_store_base=tmp_path / "art",
    )
    res = loop.iterate(_compliant())
    assert res.render_mime == "image/png"
    assert res.render_image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
