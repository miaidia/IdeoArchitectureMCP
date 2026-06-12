"""§25.1 confidence calibration (Phase 16; F-0552 + F-0560 manual expert set).

* the composite model (`plot_domain.confidence`) is calibrated against the
  hand-labeled ``manual_review_set.json``: ≥90% of cases must land in the
  expert's band and NO case may be off by more than one band;
* monotonicity / anti-inflation properties (§20.10): improving a component never
  lowers the composite, unknown components never act as 1.0, an empty vector
  collapses to the hint band;
* the §25.1 threshold policy is applied in reports: a check below the moderate
  threshold (0.60) is flagged ``manual_review`` in the compliance summary and
  surfaces in the koncepcja Markdown;
* wiring proofs: envelope metadata, rule-engine traces and parser indicators all
  carry the component decomposition (v2 addition 3: validator confidence —
  declared/assumed inputs lower ``semantic_precision``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from plot_domain import (
    COMPONENT_NAMES,
    THRESHOLD_HIGH,
    THRESHOLD_LOW,
    THRESHOLD_MODERATE,
    band_for,
    confidence_components,
)

SET_PATH = Path(__file__).parent / "manual_review_set.json"
BANDS = ("hint", "low", "moderate", "high")


def _cases() -> list[dict]:
    return json.loads(SET_PATH.read_text(encoding="utf-8"))["cases"]


# --------------------------------------------------------------------------- #
# Calibration against the manual expert-review set (F-0552/F-0560)
# --------------------------------------------------------------------------- #
def test_manual_review_set_is_meaningful() -> None:
    """The set is big and diverse enough to calibrate against (≥20 cases, every
    band represented, every §25.1 component exercised somewhere)."""
    cases = _cases()
    assert len(cases) >= 20
    assert {c["expected_band"] for c in cases} == set(BANDS)
    used = {name for c in cases for name in c["components"]}
    assert used == set(COMPONENT_NAMES), f"unused components: {set(COMPONENT_NAMES) - used}"
    for case in cases:
        assert set(case["components"]) <= set(COMPONENT_NAMES)


def test_composite_lands_in_expert_band_for_90_percent() -> None:
    cases = _cases()
    mismatches: list[str] = []
    for case in cases:
        result = confidence_components(**case["components"])
        if result.band != case["expected_band"]:
            mismatches.append(
                f"{case['id']}: expected {case['expected_band']}, "
                f"got {result.band} ({result.value})"
            )
            # Off-by-more-than-one-band is a calibration failure outright.
            distance = abs(
                BANDS.index(result.band) - BANDS.index(case["expected_band"])
            )
            assert distance <= 1, f"{case['id']} off by {distance} bands"
    accuracy = 1.0 - len(mismatches) / len(cases)
    assert accuracy >= 0.90, f"calibration accuracy {accuracy:.0%}; {mismatches}"


def test_composite_consistent_with_policy_flags() -> None:
    """value/band/flags always agree with the §25.1 policy thresholds."""
    for case in _cases():
        result = confidence_components(**case["components"])
        assert result.band == band_for(result.value)
        assert result.requires_manual_review == (result.value < THRESHOLD_MODERATE)
        assert result.decision_grade == (result.value >= THRESHOLD_LOW)
        if case["components"]:
            assert result.weak_link == min(
                case["components"], key=lambda k: case["components"][k]
            )


# --------------------------------------------------------------------------- #
# Anti-inflation properties (§20.10: never remove uncertainty to please)
# --------------------------------------------------------------------------- #
def test_empty_component_vector_is_hint_not_default() -> None:
    result = confidence_components()
    assert result.band == "hint"
    assert result.requires_manual_review is True
    assert result.missing_components == list(COMPONENT_NAMES)


def test_unknown_component_never_acts_as_perfect() -> None:
    """Adding the SAME information explicitly as 1.0 may only raise the value —
    i.e. a missing component was never silently counted as 1.0."""
    base = confidence_components(source_authority=0.7, semantic_precision=0.7)
    boosted = confidence_components(
        source_authority=0.7, semantic_precision=0.7, manual_verification=1.0
    )
    assert boosted.value >= base.value
    # ...and a weak explicit value LOWERS it (the component genuinely counts).
    weakened = confidence_components(
        source_authority=0.7, semantic_precision=0.7, manual_verification=0.2
    )
    assert weakened.value < base.value


def test_monotonic_in_every_component() -> None:
    base_kwargs = {name: 0.6 for name in COMPONENT_NAMES}
    base = confidence_components(**base_kwargs).value
    for name in COMPONENT_NAMES:
        better = confidence_components(**{**base_kwargs, name: 0.9}).value
        worse = confidence_components(**{**base_kwargs, name: 0.3}).value
        assert better >= base >= worse, name


def test_weak_link_caps_the_composite() -> None:
    """One dead component keeps the composite out of the comfortable bands no
    matter how many strong components surround it (§20.10)."""
    result = confidence_components(
        source_authority=0.95,
        source_freshness=0.95,
        geometry_precision=0.95,
        semantic_precision=0.95,
        cross_source_agreement=0.1,
    )
    assert result.value <= 0.1 + 0.25 + 1e-9
    assert result.weak_link == "cross_source_agreement"
    assert result.band in ("hint", "low")


def test_component_out_of_range_rejected() -> None:
    with pytest.raises(ValueError):
        confidence_components(source_authority=1.4)
    with pytest.raises(ValueError):
        confidence_components(parser_confidence=-0.1)


def test_thresholds_match_spec_policy() -> None:
    """§25.1 example policy adopted verbatim (base_assumptions:2008-2031)."""
    assert (THRESHOLD_HIGH, THRESHOLD_MODERATE, THRESHOLD_LOW) == (0.85, 0.60, 0.35)
    assert band_for(0.85) == "high"
    assert band_for(0.84) == "moderate"
    assert band_for(0.60) == "moderate"
    assert band_for(0.59) == "low"
    assert band_for(0.35) == "low"
    assert band_for(0.34) == "hint"


# --------------------------------------------------------------------------- #
# §25.1 threshold policy applied in reports (manual_review flag)
# --------------------------------------------------------------------------- #
def test_compliance_summary_flags_low_confidence_for_manual_review() -> None:
    from plot_reports import compliance_summary

    checks = [
        {"rule_id": "R-DECIDED", "status": "pass", "confidence": 0.9},
        {"rule_id": "R-UNKNOWN", "status": "unknown", "confidence": 0.2},
        {"rule_id": "R-CONSERVATIVE", "status": "warning", "confidence": 0.4},
        # not_applicable is exempt — nothing was decided to confirm.
        {"rule_id": "R-NA", "status": "not_applicable", "confidence": 0.2},
    ]
    summary = compliance_summary(checks)
    assert summary["manual_review_rule_ids"] == ["R-CONSERVATIVE", "R-UNKNOWN"]
    assert summary["manual_review_threshold"] == THRESHOLD_MODERATE


def test_koncepcja_markdown_surfaces_manual_review_flag() -> None:
    from plot_domain import MasterplanVariant
    from plot_reports import build_koncepcja_model, render_model_markdown

    variant = MasterplanVariant(
        id="mp:cal",
        analysis_id="a-cal",
        buildings=[],
        stage_table=[],
        totals={"buildings": 0},
        metadata={
            "inter_building_checks": [
                {"rule_id": "R-LOWCONF", "status": "unknown", "confidence": 0.2},
            ]
        },
    )
    model = build_koncepcja_model(
        analysis_id="a-cal", variant=variant, generated_at="2026-06-12T00:00:00+00:00"
    )
    md = render_model_markdown(model)
    assert "manual review" in md and "R-LOWCONF" in md
    # ...and a fully confident plan does NOT carry the flag line.
    confident = variant.model_copy(
        update={
            "metadata": {
                "inter_building_checks": [
                    {"rule_id": "R-OK", "status": "pass", "confidence": 0.9}
                ]
            }
        }
    )
    md_ok = render_model_markdown(
        build_koncepcja_model(
            analysis_id="a-cal", variant=confident, generated_at="t"
        )
    )
    assert "manual review" not in md_ok


# --------------------------------------------------------------------------- #
# Wiring proofs: envelope / rule engine / parser emit the decomposition
# --------------------------------------------------------------------------- #
def test_envelope_metadata_carries_confidence_components(example_constraint) -> None:
    from plot_envelope import buildable_envelope_v1
    from shapely.geometry import Polygon

    parcel = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    env = buildable_envelope_v1(
        parcel, no_build=[], constraints=[example_constraint]
    )
    block = env.metadata["confidence_components"]
    assert set(block["components"]) == {
        "source_authority",
        "geometry_precision",
        "semantic_precision",
    }
    assert block["band"] == band_for(block["value"])
    # No constraints → the decomposition is honestly absent (None), never faked.
    bare = buildable_envelope_v1(parcel, no_build=[])
    assert bare.metadata["confidence_components"] is None


def test_rule_trace_components_lower_semantic_precision_for_assumed_inputs() -> None:
    """Validator confidence (v2 addition 3): a declared input_defaults assumption
    (e.g. the conservative windowed_walls default) lowers semantic_precision
    relative to a geometry-measured evaluation of the same rule."""
    from pathlib import Path

    from plot_rules import evaluate, load_rulesets

    repo = Path(__file__).resolve().parents[2]
    registry = load_rulesets(repo / "rulesets" / "PL")
    rule = registry.get("PL-WT-12-SETBACKS-001")
    assert rule is not None

    measured = evaluate(
        rule,
        {
            "wall_has_windows_or_doors": True,
            "is_multifamily_over_4_storeys": False,
            "distance_to_boundary_m": 6.0,
            "mpzp_allows_reduced_setback": False,
        },
    )
    assumed = evaluate(
        rule,
        {
            "wall_has_windows_or_doors": True,
            "is_multifamily_over_4_storeys": False,
            "distance_to_boundary_m": 6.0,
            # mpzp_allows_reduced_setback omitted → documented input_defaults
        },
    )
    cc_measured = measured.trace["confidence_components"]
    cc_assumed = assumed.trace["confidence_components"]
    assert "assumed_inputs" in assumed.trace
    assert (
        cc_assumed["components"]["semantic_precision"]
        < cc_measured["components"]["semantic_precision"]
    )
    assert cc_assumed["value"] <= cc_measured["value"]


def test_rule_trace_components_lower_ruleset_certainty_for_unknown() -> None:
    from pathlib import Path

    from plot_rules import evaluate, load_rulesets

    repo = Path(__file__).resolve().parents[2]
    registry = load_rulesets(repo / "rulesets" / "PL")
    rule = registry.get("PL-WT-12-SETBACKS-001")
    assert rule is not None
    unknown = evaluate(rule, {})  # nothing provided → unverifiable
    block = unknown.trace["confidence_components"]
    decided = evaluate(
        rule,
        {
            "wall_has_windows_or_doors": True,
            "is_multifamily_over_4_storeys": False,
            "distance_to_boundary_m": 6.0,
            "mpzp_allows_reduced_setback": False,
        },
    ).trace["confidence_components"]
    assert (
        block["components"]["ruleset_certainty"]
        < decided["components"]["ruleset_certainty"]
    )
    assert block["band"] in ("hint", "low")


def test_parser_indicators_carry_confidence_components() -> None:
    from plot_mcp_server import usecases

    text = (
        "Uchwala w sprawie MPZP. Maksymalna wysokosc zabudowy: 12 m. "
        "Minimalny udzial powierzchni biologicznie czynnej: 25%."
    )
    out = usecases.planning_parse_document(None, text)
    assert out["status"] in ("parsed", "manual_review_required")
    assert out["indicators"], "deterministic extraction should find indicators"
    for indicator in out["indicators"]:
        block = indicator["confidence_components"]
        assert "parser_confidence" in block["components"]
        assert block["components"]["source_authority"] == 0.5  # user document
        assert block["band"] == band_for(block["value"])
