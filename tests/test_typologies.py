"""Phase 11 part A — typology knowledge tests (plan §11.1.2).

Covers: the 7-document corpus loads under the ``typologies`` category; narrow plots
rank punktowiec/klatkowiec over kwartał; a wide śródmiejska plot with a usługi
indicator ranks kwartał + usługi-w-parterze on top; editing a YAML in a tmp dir
flips a recommendation (proves the knowledge is data-driven); and the anti-pattern
guard — typology documents are suggestions, never evaluable rules.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from plot_planning import recommend_typologies
from plot_planning.typologies import TYPOLOGY_CATEGORY
from plot_rules import load_rulesets

RULESET_DIR = Path("rulesets/PL")

EXPECTED_IDS = {
    "PL-TYP-WIELORODZINNY-TRAKT-001",
    "PL-TYP-PUNKTOWIEC-001",
    "PL-TYP-KLATKOWIEC-SEKCYJNY-001",
    "PL-TYP-KWARTAL-OBRZEZNY-001",
    "PL-TYP-GALERIOWIEC-001",
    "PL-TYP-USLUGI-W-PARTERZE-001",
    "PL-TYP-HALA-GARAZOWA-DZIEDZINIEC-001",
}


@pytest.fixture(scope="module")
def registry():
    return load_rulesets(RULESET_DIR)


def test_typology_corpus_loads_under_typologies_category(registry) -> None:
    assert TYPOLOGY_CATEGORY in registry.categories
    loaded = {r.id for r in registry.by_category(TYPOLOGY_CATEGORY)}
    assert loaded == EXPECTED_IDS
    assert not [e for e in registry.errors if "typologies" in e]


def test_typology_documents_are_never_evaluable_rules(registry) -> None:
    # Anti-pattern guard (plan §11.4): suggestions carry NO checks (nothing for the
    # rules engine to enforce), are soft, and declare basis design_practice.
    for rule in registry.by_category(TYPOLOGY_CATEGORY):
        assert not rule.raw.get("checks"), f"{rule.id} must not carry engine checks"
        assert rule.severity == "soft"
        assert rule.raw.get("basis") == "design_practice"
        assert rule.raw.get("legal_force") == "none"


def test_narrow_plot_ranks_punktowiec_klatkowiec_over_kwartal(registry) -> None:
    recs = recommend_typologies(
        "narrow",
        {},
        False,
        {"width_m": 22.0, "area_m2": 2500.0},
        registry=registry,
    )
    ids = [r.typology_id for r in recs]
    # Kwartał obrzeżny is EXCLUDED: its applicability needs a wide plot.
    assert "PL-TYP-KWARTAL-OBRZEZNY-001" not in ids
    assert ids[0] == "PL-TYP-PUNKTOWIEC-001"
    assert ids.index("PL-TYP-KLATKOWIEC-SEKCYJNY-001") < len(ids) - 1
    punktowiec = recs[0]
    assert any("wąska działka" in w for w in punktowiec.why)


def test_wide_srodmiejska_uslugi_plot_ranks_kwartal_and_uslugi_top(registry) -> None:
    recs = recommend_typologies(
        "rectangular",
        {"parking_per_100m2_uslug": 2.0},  # usługi indicator present
        True,  # śródmiejska
        {"width_m": 85.0, "area_m2": 50_000.0},
        registry=registry,
    )
    top_two = [r.typology_id for r in recs[:2]]
    assert top_two == [
        "PL-TYP-KWARTAL-OBRZEZNY-001",
        "PL-TYP-USLUGI-W-PARTERZE-001",
    ]
    kwartal = recs[0]
    assert any("śródmiejska" in w for w in kwartal.why)
    assert any("usługow" in w.lower() for w in kwartal.why)


def test_recommendations_carry_parameters_pros_cons(registry) -> None:
    recs = recommend_typologies(
        "rectangular", {}, False, {"width_m": 70.0, "area_m2": 40_000.0}, registry=registry
    )
    trakt = next(r for r in recs if r.typology_id == "PL-TYP-WIELORODZINNY-TRAKT-001")
    assert trakt.parameters["trakt_depth_m"] == {"min": 12, "max": 18}
    assert trakt.parameters["section_length_m"] == {"min": 20, "max": 35}
    assert trakt.pros and trakt.cons
    assert trakt.basis == "design_practice"


def test_yaml_edit_flips_recommendation(tmp_path: Path, registry) -> None:
    # Copy the corpus to a tmp dir, raise galeriowiec's base preference above all
    # others, reload → the ranking flips WITHOUT any code change (data-driven).
    target = tmp_path / "typologies"
    shutil.copytree(RULESET_DIR / "typologies", target)
    gal = target / "galeriowiec.yaml"
    gal.write_text(gal.read_text(encoding="utf-8").replace("base: 0.3", "base: 5.0"), "utf-8")

    metrics = {"width_m": 85.0, "area_m2": 50_000.0}
    before = recommend_typologies("rectangular", {}, True, metrics, registry=registry)
    after = recommend_typologies(
        "rectangular", {}, True, metrics, registry=load_rulesets(tmp_path)
    )
    assert before[0].typology_id != "PL-TYP-GALERIOWIEC-001"
    assert after[0].typology_id == "PL-TYP-GALERIOWIEC-001"


def test_yaml_applicability_edit_excludes_typology(tmp_path: Path) -> None:
    # Tighten kwartał's minimum width beyond the plot — it drops out of the ranking.
    target = tmp_path / "typologies"
    shutil.copytree(RULESET_DIR / "typologies", target)
    kw = target / "kwartal-obrzezny.yaml"
    kw.write_text(
        kw.read_text(encoding="utf-8").replace("width_m: {min: 60}", "width_m: {min: 500}"),
        "utf-8",
    )
    recs = recommend_typologies(
        "rectangular", {}, True, {"width_m": 85.0}, registry=load_rulesets(tmp_path)
    )
    assert "PL-TYP-KWARTAL-OBRZEZNY-001" not in [r.typology_id for r in recs]


def test_unknown_features_do_not_exclude(registry) -> None:
    # Suggestions are lenient: with NO parcel metrics at all, kwartał is still listed
    # (unknown width earns no bonus and triggers no exclusion).
    recs = recommend_typologies("rectangular", {}, False, {}, registry=registry)
    assert "PL-TYP-KWARTAL-OBRZEZNY-001" in [r.typology_id for r in recs]
