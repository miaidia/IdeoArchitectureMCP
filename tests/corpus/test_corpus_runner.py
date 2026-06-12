"""Masterplan golden-corpus runner (Phase 16, v2 addition 1).

For EVERY corpus member (see ``tests/corpus/__init__.py``):

1. **capacity scenarios** land inside the fixture's expected RANGES (base PUM /
   mieszkania), the coverage cap holds, and the scenarios stay strictly ordered
   conservative < base < optimistic ≤ max;
2. the **hand-written masterplan** passes the HARD validators with zero fails
   (or only the fixture's documented ``expected_fails``) and zero hard
   violations through the REAL ``DrawingLoop`` (capacity → WT/ppoż validators →
   staging → score → render → audit);
3. the **renderer** produces a real PNG for the plan;
4. the render matches the committed **golden-image snapshot**
   (``tests/corpus/golden/<name>.png``; mean-abs-diff tolerance, same idiom as
   ``tests/test_render_golden.py``; ``UPDATE_GOLDEN=1`` regenerates).

Plus the narrow-infill positive control: the same layout WITHOUT the
śródmiejska flag must FAIL §13 (proves the corpus exercises the halving, not a
vacuously distant pair).
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage
from tests.corpus import (
    CorpusFixture,
    assembly_merged_parcel,
    assembly_parcels,
    corpus_fixtures,
    narrow_infill_masterplan,
    narrow_infill_parcel,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
#: Mean-abs-diff tolerance (0..255), as in tests/test_render_golden.py.
TOLERANCE = 3.0

FIXTURES = {f.name: f for f in corpus_fixtures()}
NAMES = list(FIXTURES)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _loop_for(fixture: CorpusFixture, tmp_path: Path):
    from plot_agent.context import AnalysisContext
    from plot_agent.drawing import DrawingLoop, ExemplarStoreV2

    context = AnalysisContext.with_loaded_rules(
        parcel=fixture.parcel, buildable_envelope=fixture.parcel
    )
    return DrawingLoop(
        context=context,
        exemplar_store=ExemplarStoreV2(tmp_path / "exemplars"),
        artifact_store_base=tmp_path / "artifacts",
        indicators=dict(fixture.indicators),
    )


def _iterate(fixture: CorpusFixture, tmp_path: Path):
    from plot_agent.drawing import MasterplanProposal

    loop = _loop_for(fixture, tmp_path)
    proposal = MasterplanProposal.model_validate(fixture.masterplan)
    return loop.iterate(proposal, rationale=f"golden corpus: {fixture.name}")


def _mean_abs_diff(a: bytes, b: bytes) -> float:
    img_a = np.asarray(PILImage.open(io.BytesIO(a)).convert("RGB"), dtype=np.int16)
    img_b = np.asarray(PILImage.open(io.BytesIO(b)).convert("RGB"), dtype=np.int16)
    if img_a.shape != img_b.shape:
        return 255.0
    return float(np.abs(img_a - img_b).mean())


# --------------------------------------------------------------------------- #
# Corpus shape
# --------------------------------------------------------------------------- #
def test_corpus_has_five_members_spanning_plot_classes() -> None:
    assert NAMES == [
        "riverside_irregular",
        "narrow_infill_srodmiejska",
        "suburban_mn_1ha",
        "corner_mixed_use",
        "multi_parcel_assembly",
    ]
    areas = {n: FIXTURES[n].parcel.area for n in NAMES}
    assert 45_000 < areas["riverside_irregular"] < 56_000  # ~5 ha
    assert 1_400 < areas["narrow_infill_srodmiejska"] < 1_700  # ~0.15 ha
    assert areas["suburban_mn_1ha"] == pytest.approx(10_000)  # 1 ha
    assert 4_500 < areas["corner_mixed_use"] < 5_500  # ~0.5 ha
    assert areas["multi_parcel_assembly"] == pytest.approx(20_000)  # ~2 ha


def test_assembly_merge_produces_single_investment_area() -> None:
    """plot_geo.merge_parcels fuses the two source parcels; the masterplan's R1
    straddles the erstwhile internal boundary (the merge demonstration)."""
    from shapely.geometry import shape

    parcels = assembly_parcels()
    merged = assembly_merged_parcel()
    assert merged.geom_type == "Polygon"
    assert merged.area == pytest.approx(sum(p.area for p in parcels))
    r1 = shape(FIXTURES["multi_parcel_assembly"].masterplan["buildings"][0]["segments"][0]["polygon"])
    # R1 sits partly on each source parcel — only the merged area contains it.
    assert r1.intersection(parcels[0]).area > 0
    assert r1.intersection(parcels[1]).area > 0
    assert r1.within(merged.buffer(1e-9))


# --------------------------------------------------------------------------- #
# 1) Capacity scenarios within expected ranges
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", NAMES)
def test_capacity_scenarios_within_expected_ranges(name: str) -> None:
    from plot_planning import generate_capacity_scenarios

    fixture = FIXTURES[name]
    area = float(fixture.parcel.area)
    scenario_set = generate_capacity_scenarios(
        envelope_area_m2=area,
        parcel_area_m2=area,
        indicators=fixture.indicators,
    )
    by_type = {s.scenario_type: s.metrics for s in scenario_set.scenarios}
    assert set(by_type) == {"conservative", "base", "optimistic", "max"}

    base = by_type["base"]
    lo, hi = fixture.expected.pum_m2
    assert lo <= float(base["pum_m2"]) <= hi, f"{name}: base PUM {base['pum_m2']}"
    mlo, mhi = fixture.expected.mieszkania
    assert mlo <= int(base["mieszkania_estimate"]) <= mhi
    # Coverage cap holds on every scenario (footprint never exceeds the MPZP cap).
    for metrics in by_type.values():
        coverage = float(metrics["footprint_m2"]) / area
        assert coverage <= fixture.expected.max_coverage_ratio + 1e-9
    # Strict scenario ordering (conservative < base < optimistic <= max — the
    # intensity cap may flatten the top scenarios, never invert them).
    pums = [float(by_type[t]["pum_m2"]) for t in ("conservative", "base", "optimistic", "max")]
    assert pums[0] < pums[1] <= pums[2] <= pums[3]


# --------------------------------------------------------------------------- #
# 2+3) Hand-written masterplan passes hard validators; renderer produces image
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def corpus_runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """One DrawingLoop iteration per fixture, shared by the assertions below."""
    tmp = tmp_path_factory.mktemp("corpus")
    return {name: _iterate(FIXTURES[name], tmp / name) for name in NAMES}


@pytest.mark.parametrize("name", NAMES)
def test_masterplan_zero_hard_violations(name: str, corpus_runs) -> None:
    result = corpus_runs[name]
    assert result.score.valid is True, [v.to_dict() for v in result.score.violations]
    assert result.score.violations == []


@pytest.mark.parametrize("name", NAMES)
def test_masterplan_passes_hard_validators(name: str, corpus_runs) -> None:
    fixture = FIXTURES[name]
    result = corpus_runs[name]
    failing = {
        c.rule_id: c.message
        for c in result.inter_building_checks
        if c.status.value == "fail"
    }
    unexpected = {rid: msg for rid, msg in failing.items() if rid not in fixture.expected_fails}
    assert not unexpected, f"{name}: unexpected FAILs: {unexpected}"
    # Documented expected-fails must actually occur (a stale exemption is a lie).
    for rule_id, reason in fixture.expected_fails.items():
        assert rule_id in failing, f"{name}: documented expected-fail {rule_id} ({reason}) did not occur"


@pytest.mark.parametrize("name", NAMES)
def test_renderer_produces_png(name: str, corpus_runs) -> None:
    result = corpus_runs[name]
    assert result.render_mime == "image/png"
    assert result.render_image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.artifact_uri


@pytest.mark.parametrize("name", NAMES)
def test_audit_records_corpus_iteration(name: str, corpus_runs) -> None:
    result = corpus_runs[name]
    assert result.rationale == f"golden corpus: {name}"


# --------------------------------------------------------------------------- #
# 4) Golden-image snapshots (committed references; UPDATE_GOLDEN=1 regenerates)
# --------------------------------------------------------------------------- #
def _golden_render(fixture: CorpusFixture) -> bytes:
    """Deterministic render for snapshotting: fixed title, no loop counter."""
    from plot_agent.drawing import MasterplanProposal
    from plot_planning import CapacityConfig, masterplan_metrics
    from plot_reports import render_masterplan
    from plot_rules import load_rulesets

    proposal = MasterplanProposal.model_validate(fixture.masterplan)
    registry = load_rulesets("rulesets/PL")
    metrics = masterplan_metrics(
        proposal,
        fixture.parcel,
        dict(fixture.indicators),
        config=CapacityConfig(),
        registry=registry,
    )
    render = render_masterplan(
        proposal,
        fixture.parcel,
        metrics=metrics,
        envelope=fixture.parcel,
        title=f"Golden corpus — {fixture.name}",
    )
    return render.data


def _golden_gate(reference: Path, rendered: bytes) -> None:
    """The golden-reference gate (same idiom as tests/test_render_golden.py):
    ``UPDATE_GOLDEN=1`` (re)writes the reference; otherwise a MISSING reference
    is a hard failure — never silently self-healed with the current render
    (that would make the snapshot comparison vacuous)."""
    if os.environ.get("UPDATE_GOLDEN") == "1":
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(rendered)
        return
    if not reference.exists():
        pytest.fail(
            f"golden reference missing: {reference}; "
            "run UPDATE_GOLDEN=1 pytest tests/corpus to create it"
        )


@pytest.mark.parametrize("name", NAMES)
def test_golden_image_snapshot(name: str) -> None:
    reference = GOLDEN_DIR / f"{name}.png"
    rendered = _golden_render(FIXTURES[name])
    _golden_gate(reference, rendered)
    diff = _mean_abs_diff(rendered, reference.read_bytes())
    assert diff <= TOLERANCE, f"{name}: render drifted from golden (mad={diff:.2f})"


def test_golden_gate_fails_on_missing_reference_without_update_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2 regression: a missing golden reference without UPDATE_GOLDEN=1 must
    FAIL (not self-heal by writing the current render and passing vacuously)."""
    monkeypatch.delenv("UPDATE_GOLDEN", raising=False)
    missing = tmp_path / "golden" / "nope.png"
    with pytest.raises(pytest.fail.Exception, match="UPDATE_GOLDEN=1"):
        _golden_gate(missing, b"\x89PNG fake")
    assert not missing.exists(), "the gate must not create the reference"


def test_golden_gate_writes_reference_only_under_update_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UPDATE_GOLDEN", "1")
    reference = tmp_path / "golden" / "made.png"
    _golden_gate(reference, b"\x89PNG fake")
    assert reference.read_bytes() == b"\x89PNG fake"


def test_golden_snapshot_bites() -> None:
    """A layout-scale mistake (greenery layer gone + a relocated house) exceeds
    the tolerance — the snapshot test is real, not vacuously loose."""
    fixture = FIXTURES["suburban_mn_1ha"]
    perturbed = dict(fixture.masterplan)
    perturbed["buildings"] = [dict(b) for b in fixture.masterplan["buildings"]]
    moved = dict(perturbed["buildings"][0])
    seg = dict(moved["segments"][0])
    from tests.wt_fixtures import gj_rect

    seg["polygon"] = gj_rect(70, 40, 10, 8)  # move Dom 1 across the plot
    moved["segments"] = [seg]
    perturbed["buildings"][0] = moved
    perturbed["greenery_polygons"] = []  # the green half of the plot vanishes
    rendered = _golden_render(
        CorpusFixture(
            name=fixture.name,
            description=fixture.description,
            parcel=fixture.parcel,
            indicators=fixture.indicators,
            masterplan=perturbed,
            expected=fixture.expected,
        )
    )
    reference = (GOLDEN_DIR / "suburban_mn_1ha.png").read_bytes()
    assert _mean_abs_diff(rendered, reference) > TOLERANCE


# --------------------------------------------------------------------------- #
# Narrow-infill positive control: the śródmiejska halving actually bites
# --------------------------------------------------------------------------- #
def test_narrow_infill_srodmiejska_halving_bites(tmp_path: Path) -> None:
    """Same geometry, śródmiejska flag OFF → §13 przesłanianie FAILS for the
    Frontowy/Oficyna pair; flag ON (the corpus member) → zero fails. This is
    the corpus' proof that the ust. 4 halving is exercised on a real layout,
    not satisfied vacuously."""
    from plot_agent.context import AnalysisContext
    from plot_agent.drawing import DrawingLoop, ExemplarStoreV2, MasterplanProposal
    from plot_planning.wt_validators import RULE_WT13

    context = AnalysisContext.with_loaded_rules(
        parcel=narrow_infill_parcel(), buildable_envelope=narrow_infill_parcel()
    )
    loop = DrawingLoop(
        context=context,
        exemplar_store=ExemplarStoreV2(tmp_path / "exemplars"),
        artifact_store_base=tmp_path / "artifacts",
        indicators=FIXTURES["narrow_infill_srodmiejska"].indicators,
    )
    result = loop.iterate(
        MasterplanProposal.model_validate(narrow_infill_masterplan(srodmiejska=False)),
        rationale="positive control: śródmiejska OFF",
    )
    wt13_fails = [
        c
        for c in result.inter_building_checks
        if c.rule_id == RULE_WT13 and c.status.value == "fail"
    ]
    assert wt13_fails, "without śródmiejska the 12 m courtyard must violate §13"
    assert any("Frontowy" in c.message and "Oficyna" in c.message for c in wt13_fails)
