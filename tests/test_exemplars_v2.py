"""Phase 11 part A — exemplar memory v2 tests (plan §11.1.4).

Covers: density banding (config, basis marker), persistence + reload on a fresh
store instance over the same directory (process-restart semantics), thumbnail PNG
saved next to the JSON, exact-key recall, nearest-neighbour relaxation (density
band, then program), v1-API compatibility (the Phase 4 tests in
``test_agent_drawing.py`` stay green — verified by the suite), and few-shot
prompt formatting.
"""

from __future__ import annotations

import json
from pathlib import Path

from plot_agent.drawing import (
    DensityBands,
    DrawingExemplarStore,
    ExemplarStoreV2,
    LayoutProposal,
    density_class_for,
    format_exemplars_for_prompt,
)
from plot_agent.drawing.proposal import MasterplanProposal, PlacedRectangle
from tests.masterplan_fixtures import staged_masterplan_payload


def _layout(w: float = 10.0) -> LayoutProposal:
    return LayoutProposal(
        program_type="multifamily",
        rectangles=[PlacedRectangle(x=0, y=0, w=w, h=10)],
        floors=4,
    )


def _masterplan() -> MasterplanProposal:
    return MasterplanProposal.model_validate(staged_masterplan_payload())


# --------------------------------------------------------------------------- #
# Density banding
# --------------------------------------------------------------------------- #
def test_density_class_banding_defaults() -> None:
    assert density_class_for(0.3) == "low"
    assert density_class_for(0.5) == "mid"
    assert density_class_for(1.5) == "mid"
    assert density_class_for(1.6) == "high"
    assert density_class_for(None) == "unknown"


def test_density_bands_are_config_with_basis_marker() -> None:
    bands = DensityBands(low_max=0.8, high_min=2.0)
    assert density_class_for(0.7, bands) == "low"
    assert density_class_for(1.9, bands) == "mid"
    assert DensityBands().basis == "design_practice"  # never a legal value


# --------------------------------------------------------------------------- #
# Persistence: store → restart (new instance, same dir) → recall
# --------------------------------------------------------------------------- #
def test_store_restart_recall_roundtrip(tmp_path: Path) -> None:
    store = ExemplarStoreV2(tmp_path / "ex")
    stored = store.store(
        shape_class="rectangular",
        program_type="multifamily",
        proposal=_masterplan(),
        score_total=0.82,
        components={"coverage": 0.9},
        density_class="mid",
        thumbnail_png=b"\x89PNG\r\n\x1a\nfake",
    )
    # One JSON per exemplar + the thumbnail PNG next to it, path relative to base.
    json_file = tmp_path / "ex" / "rectangular__multifamily__mid" / f"{stored.exemplar_id}.json"
    assert json_file.exists()
    assert stored.thumbnail_path is not None
    assert (tmp_path / "ex" / stored.thumbnail_path).exists()
    doc = json.loads(json_file.read_text(encoding="utf-8"))
    assert doc["density_class"] == "mid"
    assert doc["proposal"]["schema_version"] == 2

    # Process restart: a NEW instance over the same dir reloads the index.
    reloaded = ExemplarStoreV2(tmp_path / "ex")
    recalled = reloaded.recall("rectangular", "multifamily", k=3, density_class="mid")
    assert [e.exemplar_id for e in recalled] == [stored.exemplar_id]
    assert recalled[0].score_total == 0.82


def test_v1_files_reload_into_v2_store(tmp_path: Path) -> None:
    # Exemplars written by the Phase 4 store (no density key) are still recalled.
    v1 = DrawingExemplarStore(base_dir=tmp_path / "ex")
    old = v1.store(
        shape_class="square",
        program_type="single_family",
        proposal=_layout(),
        score_total=0.7,
    )
    v2 = ExemplarStoreV2(tmp_path / "ex")
    recalled = v2.recall("square", "single_family", k=3)
    assert [e.exemplar_id for e in recalled] == [old.exemplar_id]
    assert recalled[0].density_class is None  # dataclass default fills the v1 doc


# --------------------------------------------------------------------------- #
# Recall ranking: exact key first, then nearest-neighbour relaxation
# --------------------------------------------------------------------------- #
def test_exact_key_beats_relaxed_matches(tmp_path: Path) -> None:
    store = ExemplarStoreV2(tmp_path / "ex")
    low = store.store(
        shape_class="narrow", program_type="multifamily", proposal=_masterplan(),
        score_total=0.99, density_class="low",
    )
    mid = store.store(
        shape_class="narrow", program_type="multifamily", proposal=_masterplan(),
        score_total=0.55, density_class="mid",
    )
    recalled = store.recall("narrow", "multifamily", k=2, density_class="mid")
    # Exact density match first even though the relaxed one scores higher.
    assert [e.exemplar_id for e in recalled] == [mid.exemplar_id, low.exemplar_id]


def test_density_relaxation_orders_by_band_distance(tmp_path: Path) -> None:
    store = ExemplarStoreV2(tmp_path / "ex")
    high = store.store(
        shape_class="narrow", program_type="multifamily", proposal=_masterplan(),
        score_total=0.9, density_class="high",
    )
    mid = store.store(
        shape_class="narrow", program_type="multifamily", proposal=_masterplan(),
        score_total=0.9, density_class="mid",
    )
    recalled = store.recall("narrow", "multifamily", k=2, density_class="low")
    # No exact "low" match: mid (band distance 1) precedes high (band distance 2).
    assert [e.exemplar_id for e in recalled] == [mid.exemplar_id, high.exemplar_id]


def test_program_relaxation_same_shape_class(tmp_path: Path) -> None:
    store = ExemplarStoreV2(tmp_path / "ex")
    other_program = store.store(
        shape_class="corner", program_type="services", proposal=_layout(),
        score_total=0.8, density_class="mid",
    )
    recalled = store.recall("corner", "multifamily", k=3, density_class="mid")
    # Nothing for the requested program: SAME shape_class is still recalled.
    assert [e.exemplar_id for e in recalled] == [other_program.exemplar_id]
    # A different shape_class is never recalled.
    assert store.recall("narrow", "multifamily", k=3, density_class="mid") == []


def test_v1_recall_signature_still_works_on_v2(tmp_path: Path) -> None:
    store = ExemplarStoreV2(tmp_path / "ex")
    a = store.store(
        shape_class="square", program_type="multifamily", proposal=_layout(),
        score_total=0.9, density_class="high",
    )
    b = store.store(
        shape_class="square", program_type="multifamily", proposal=_layout(),
        score_total=0.6, density_class="low",
    )
    # v1 call shape (no density): any density, best score first.
    recalled = store.recall("square", "multifamily", k=2)
    assert [e.exemplar_id for e in recalled] == [a.exemplar_id, b.exemplar_id]


# --------------------------------------------------------------------------- #
# Few-shot prompt formatting
# --------------------------------------------------------------------------- #
def test_format_for_prompt_compact_json_and_scores(tmp_path: Path) -> None:
    store = ExemplarStoreV2(tmp_path / "ex")
    store.store(
        shape_class="rectangular", program_type="multifamily", proposal=_masterplan(),
        score_total=0.82, components={"coverage": 0.9}, density_class="mid",
    )
    text = format_exemplars_for_prompt(
        store.recall("rectangular", "multifamily", k=3, density_class="mid")
    )
    assert "score 0.820" in text
    assert "density=mid" in text
    assert '"buildings":[{' in text  # compact JSON (no spaces) of the typed proposal
    assert format_exemplars_for_prompt([]).startswith("Brak")
