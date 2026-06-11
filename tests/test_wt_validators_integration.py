"""Phase 10 integration goldens — orchestrator determinism, override consumption,
propose_layout E2E with the violation overlay.

Plan §10.3 cases: (h) the same proposal evaluated twice yields byte-identical
sorted RuleChecks JSON; (j) a manual_override on a failing consumed rule is
applied (audited, ``applied=True``/``status="active"``); (k) propose_layout on a
violating masterplan returns ``valid=False`` with rule ids in the violations and
a red violation layer recorded in the render's style-metadata sidecar — and a
WT-compliant masterplan stays ``valid=True`` through the same path.
"""

from __future__ import annotations

import json

import pytest
from plot_planning.wt_validators import (
    RULE_PPOZ_271,
    RULE_WT12,
    run_inter_building_checks,
)
from plot_rules import DEFAULT_OVERRIDE_STORE, RuleStatus, load_rulesets
from tests.wt_fixtures import building, gj_rect, masterplan, parcel_square


@pytest.fixture(scope="module")
def registry():
    return load_rulesets("rulesets/PL")


@pytest.fixture(autouse=True)
def _clean_override_store():
    DEFAULT_OVERRIDE_STORE.clear()
    yield
    DEFAULT_OVERRIDE_STORE.clear()


def _mixed_proposal():
    """A masterplan exercising every validator: blocks, road, parking, greenery."""
    return masterplan(
        [
            building("Budynek 1", 10, 10, 40, 14, 5),
            building("Budynek 2", 10, 31, 40, 14, 5),
        ],
        roads=[
            {
                "centerline": {
                    "type": "LineString",
                    "coordinates": [[500_005.0, 500_060.0], [500_110.0, 500_060.0]],
                },
                "width_m": 4.0,
                "function": "pozarowa",
            }
        ],
        parking=[
            {"kind": "naziemny", "polygon": gj_rect(70, 10, 35, 20), "spaces": 25}
        ],
        greenery_polygons=[gj_rect(10, 70, 100, 45)],
        playgrounds=[gj_rect(60, 75, 12, 10)],
    )


# --------------------------------------------------------------------------- #
# (h) determinism — pure validators, sorted output
# --------------------------------------------------------------------------- #
def test_run_inter_building_checks_is_deterministic(registry) -> None:
    parcel = parcel_square(120.0)
    first = run_inter_building_checks(_mixed_proposal(), parcel, registry)
    second = run_inter_building_checks(_mixed_proposal(), parcel, registry)
    as_json = lambda checks: json.dumps(  # noqa: E731
        [c.model_dump(mode="json") for c in checks], sort_keys=True
    )
    assert as_json(first) == as_json(second)
    # Sorted by (rule_id, message) — stable for the generative loop.
    keys = [(c.rule_id, c.message) for c in first]
    assert keys == sorted(keys)
    # The orchestrator ran EVERY validator family on this fixture.
    families = {c.rule_id for c in first}
    assert {
        "PL-WT-12-SETBACKS-001",
        "PL-WT-13-PRZESLANIANIE-001",
        "PL-WT-60-NASLONECZNIENIE-001",
        "PL-WT-19-PARKING-DISTANCES-001",
        "PL-WT-39-PBC-001",
        "PL-WT-40-PLAC-ZABAW-001",
        "PL-PPOZ-271-273-FIRE-SEPARATION-001",
        "PL-PPOZ-DROGA-POZAROWA-001",
    } <= families


# --------------------------------------------------------------------------- #
# (j) manual_override on a failing consumed rule
# --------------------------------------------------------------------------- #
def test_manual_override_is_consumed_audited_and_applied(registry) -> None:
    from plot_mcp_server import usecases

    pair = masterplan(
        [
            building("ZL-1", 10, 10, 20, 10, 1),
            building("ZL-2", 10, 26, 20, 10, 1),
        ]
    )
    parcel = parcel_square()

    # Baseline: the 6 m ZL pair fails §271.
    before = [
        c
        for c in run_inter_building_checks(pair, parcel, registry)
        if c.rule_id == RULE_PPOZ_271 and "ZL-2" in c.message
    ]
    assert any(c.status is RuleStatus.FAIL for c in before)

    out = usecases.manual_override(
        analysis_id="an-wt-1",
        target_type="rule",
        target_id=RULE_PPOZ_271,
        reason="Sciana oddzielenia ppoz REI potwierdzona w projekcie",
        user_id="ekspert@example.com",
        after={"status": "pass", "confidence": 0.95},
    )
    # §21 honesty (M3): the rule is consumed, but the ONLY production path
    # (propose_layout) evaluates under analysis_id="adhoc" — an override
    # recorded under "an-wt-1" cannot be consumed by it yet, so the tool may
    # not claim applied/active; the note explains when it activates.
    assert out["applied"] is False
    assert out["status"] == "recorded"
    assert out["audit_logged"] is True
    assert "adhoc" in out["note"]

    # An explicit evaluation under the SAME analysis id does consume it (audited).
    after = [
        c
        for c in run_inter_building_checks(
            pair,
            parcel,
            registry,
            overrides=DEFAULT_OVERRIDE_STORE,
            analysis_id="an-wt-1",
        )
        if c.rule_id == RULE_PPOZ_271
    ]
    assert after and all(c.status is RuleStatus.PASS for c in after)
    # Full audit (author + reason + original status) embedded in the trace.
    audit = after[0].trace["override"]
    assert audit["user_id"] == "ekspert@example.com"
    assert "potwierdzona" in audit["reason"]
    assert audit["original_status"] in ("fail", "pass")
    # An override recorded WITHOUT a subject applies rule-wide — audited as such.
    assert audit["scope"] == "rule-wide"
    assert DEFAULT_OVERRIDE_STORE.audit("an-wt-1")[0].target_id == RULE_PPOZ_271


def test_manual_override_adhoc_analysis_is_active() -> None:
    # M3: the "adhoc" analysis id IS consumable (propose_layout queries it until
    # the tool becomes analysis-bound) → applied/active is an honest claim.
    from plot_mcp_server import usecases

    out = usecases.manual_override(
        analysis_id="adhoc",
        target_type="rule",
        target_id=RULE_PPOZ_271,
        reason="Sciana oddzielenia ppoz REI potwierdzona w projekcie",
        user_id="ekspert@example.com",
        after={"status": "pass"},
    )
    assert out["applied"] is True
    assert out["status"] == "active"
    assert "adhoc" in out["note"]


def test_manual_override_other_analysis_recorded_until_bound() -> None:
    # M3: any other analysis id is recorded-but-not-yet-consumable — the note
    # states when it will activate (analysis-bound evaluation, Phase 12).
    from plot_mcp_server import usecases

    out = usecases.manual_override(
        analysis_id="an:1234",
        target_type="rule",
        target_id=RULE_PPOZ_271,
        reason="test",
        user_id="ekspert@example.com",
        after={"status": "pass"},
    )
    assert out["applied"] is False
    assert out["status"] == "recorded"
    assert "an:1234" in out["note"] and "adhoc" in out["note"]


# --------------------------------------------------------------------------- #
# (M2) subject-scoped overrides + honest override messages
# --------------------------------------------------------------------------- #
def test_manual_override_subject_scoped_vs_rule_wide(registry) -> None:
    from plot_mcp_server import usecases

    # Two NEW 5-kond. multifamily buildings, each with its west wall 2 m from
    # the granica → both FAIL the §12 5 m bracket.
    pair = masterplan(
        [
            building("Budynek A", 2, 10, 20, 10, 5),
            building("Budynek B", 2, 60, 20, 10, 5),
        ]
    )
    parcel = parcel_square()
    base = [
        c
        for c in run_inter_building_checks(pair, parcel, registry)
        if c.rule_id == RULE_WT12 and c.status is RuleStatus.FAIL
    ]
    assert {c.geometry_evidence["building"] for c in base} == {
        "Budynek A",
        "Budynek B",
    }

    # Override scoped to ONE building via the target_id syntax "rule_id#subject".
    out = usecases.manual_override(
        analysis_id="an-scope",
        target_type="rule",
        target_id=f"{RULE_WT12}#Budynek A",
        reason="Zgoda na odstepstwo dla budynku A",
        user_id="ekspert@example.com",
        after={"status": "pass", "confidence": 0.9},
    )
    assert out["scope"] == "subject:Budynek A"

    checks = [
        c
        for c in run_inter_building_checks(
            pair, parcel, registry,
            overrides=DEFAULT_OVERRIDE_STORE, analysis_id="an-scope",
        )
        if c.rule_id == RULE_WT12
    ]
    a = [c for c in checks if c.geometry_evidence.get("building") == "Budynek A"]
    b = [c for c in checks if c.geometry_evidence.get("building") == "Budynek B"]
    assert a and all(c.status is RuleStatus.PASS for c in a)
    for check in a:
        # The engine's audited override message is NEVER rewritten into a
        # geometric claim — validator context is only appended.
        assert check.message.startswith(
            f"{RULE_WT12}: status overridden to 'pass'"
        ), check.message
        assert check.trace["override"]["scope"] == "subject:Budynek A"
    # Building B is NOT covered by the subject-scoped override.
    assert b and any(c.status is RuleStatus.FAIL for c in b)
    assert all("override" not in c.trace for c in b)

    # A rule-wide override (bare rule id) covers every building of the rule.
    out2 = usecases.manual_override(
        analysis_id="an-wide",
        target_type="rule",
        target_id=RULE_WT12,
        reason="Odstepstwo dla calej reguly",
        user_id="ekspert@example.com",
        after={"status": "pass"},
    )
    assert out2["scope"] == "rule-wide"
    wide = [
        c
        for c in run_inter_building_checks(
            pair, parcel, registry,
            overrides=DEFAULT_OVERRIDE_STORE, analysis_id="an-wide",
        )
        if c.rule_id == RULE_WT12
    ]
    assert wide and all(c.status is RuleStatus.PASS for c in wide)
    assert all(c.trace["override"]["scope"] == "rule-wide" for c in wide)


def test_manual_override_on_unconsumed_rule_stays_recorded() -> None:
    from plot_mcp_server import usecases

    out = usecases.manual_override(
        analysis_id="an-wt-2",
        target_type="rule",
        target_id="PL-MN-COVERAGE-001",  # not consumed by the Phase 10 validators
        reason="test",
        user_id="ekspert@example.com",
        after={"status": "pass"},
    )
    assert out["applied"] is False  # §21 honesty: recorded, consumed by no path
    assert out["status"] == "recorded"
    assert out["audit_logged"] is True


# --------------------------------------------------------------------------- #
# (k) propose_layout E2E — violating masterplan → valid=False + overlay sidecar
# --------------------------------------------------------------------------- #
def test_propose_layout_violating_masterplan_e2e() -> None:
    from plot_mcp_server import usecases
    from plot_reports import get_artifact_store

    # Two 5-kond. blocks 6 m apart inside the SAMPLE envelope (4,8)-(46,26):
    # §271 (6 < 8 m), §13 (6 < 15.5 m) and the missing fire road all fail.
    violating = {
        "buildings": [
            {
                "name": "Blok A",
                "segments": [
                    {"rectangles": [{"x": 8, "y": 10, "w": 10, "h": 14}],
                     "floors": 5, "use": "mieszkalny"}
                ],
            },
            {
                "name": "Blok B",
                "segments": [
                    {"rectangles": [{"x": 24, "y": 10, "w": 10, "h": 14}],
                     "floors": 5, "use": "mieszkalny"}
                ],
            },
        ]
    }
    out = usecases.propose_layout_render(violating)

    assert out["valid"] is False
    assert out["accepted"] is False
    rule_violations = [
        v for v in out["violations"] if v["kind"] == "inter_building_rule"
    ]
    assert rule_violations, "WT/ppoż fails must surface as hard violations"
    cited = " ".join(v["detail"] for v in rule_violations)
    assert "PL-PPOZ-271-273-FIRE-SEPARATION-001" in cited
    assert "PL-WT-13-PRZESLANIANIE-001" in cited

    # Full rule outcomes (trace + geometry evidence) in the structured result.
    failing = [c for c in out["inter_building_checks"] if c["status"] == "fail"]
    assert failing and all(c["rule_id"].startswith("PL-") for c in failing)

    # The red violation overlay is in the render + its persisted style sidecar.
    roles = [layer["role"] for layer in out["style_metadata"]["layers"]]
    assert "violation" in roles
    assert roles[-1] == "violation", "violation overlay is the TOP layer"
    sidecar = get_artifact_store().get_style_metadata(
        f"masterplan/{out['variant_id']}.png"
    )
    assert any(layer["role"] == "violation" for layer in sidecar["layers"])

    # PNG bytes really rendered (the model SEES the overlay).
    assert out["png_bytes"][:8] == b"\x89PNG\r\n\x1a\n"


def test_propose_layout_compliant_masterplan_stays_valid_e2e() -> None:
    from plot_mcp_server import usecases

    # Single 3-kond. block (9.9 m → no fire road), ≥4 m setbacks, no neighbor
    # within §13/§271 range, PBC 560/2000 = 28% ≥ 25%, 8 mieszkań ≤ 20 → §40 n/a.
    compliant = {
        "buildings": [
            {
                "name": "Kameralny",
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
    out = usecases.propose_layout_render(compliant)

    assert out["valid"] is True, [v["detail"] for v in out["violations"]]
    assert out["violations"] == []
    # Unknowns stay honest (conservative warnings), but nothing hard FAILED.
    statuses = {c["rule_id"]: c["status"] for c in out["inter_building_checks"]}
    assert "fail" not in statuses.values()
    # No violation overlay layer for a compliant plan.
    roles = [layer["role"] for layer in out["style_metadata"]["layers"]]
    assert "violation" not in roles
