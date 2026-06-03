"""Unit tests for the overlay/constraints/envelope/risk engine (Phase 7 §7.1.A/B).

Pure geometry — no network. Covers: overlay area attribution + hard/soft classification,
no-build zones (hard constraint + statutory setback), buildable envelope v1 (subtraction,
largest inscribed rectangle, source-ranked confidence, area-removal trace), red flags,
and the hard-blocker-dominance decision (§14.2).
"""

from __future__ import annotations

import plot_envelope as pe
import pytest
from plot_domain import Decision, GeometryPrecision, LegalStatus, RiskStatus, Severity
from shapely import wkt
from shapely.geometry import mapping

# 40 x 30 m parcel = 1200 m² (EPSG:2180 metres).
PARCEL = wkt.loads("POLYGON((0 0,40 0,40 30,0 30,0 0))")
# Left 15 m strip → 450 m² (37.5%).
FLOOD = wkt.loads("POLYGON((0 0,15 0,15 30,0 30,0 0))")
# A protected-area polygon covering the right 10 m → 300 m² (25%).
PROTECTED = wkt.loads("POLYGON((30 0,40 0,40 30,30 30,30 0))")


def _flood_layer() -> pe.RiskLayer:
    return pe.RiskLayer(
        kind=pe.RiskKind.FLOOD,
        geometries=[FLOOD],
        status="ok",
        source_id="pl.isok.wfs.flood",
        source_legal_status=LegalStatus.INFORMATIVE,
        source_confidence=0.8,
    )


def _protected_layer() -> pe.RiskLayer:
    return pe.RiskLayer(
        kind=pe.RiskKind.PROTECTED,
        geometries=[PROTECTED],
        status="ok",
        source_id="pl.gdos.wfs.crfop",
        source_legal_status=LegalStatus.INFORMATIVE,
        source_confidence=0.8,
    )


def test_overlay_area_attribution_and_hard_soft() -> None:
    cons = pe.overlay_layers(PARCEL, [_flood_layer(), _protected_layer()])
    assert len(cons) == 2
    # hard (flood) first by sort order
    flood = next(c for c in cons if c.constraint_type == "flood")
    protected = next(c for c in cons if c.constraint_type == "protected")
    assert pe.is_hard(flood) is True
    assert pe.is_hard(protected) is False
    # area attribution recorded (§11.3)
    assert flood.applies_to_area_m2 == pytest.approx(450.0)
    assert flood.applies_to_percent == pytest.approx(37.5)
    assert protected.applies_to_percent == pytest.approx(25.0)
    # confidence carried from the source
    assert flood.confidence == pytest.approx(0.8)


def test_no_build_zones_hard_plus_setback() -> None:
    cons = pe.overlay_layers(PARCEL, [_flood_layer()])
    nbz = pe.no_build_zones(PARCEL, cons, boundary_setback_m=3.0, setback_rule_id="PL-WT-001")
    reasons = {z.reason for z in nbz}
    assert any(r.startswith("hard_constraint_flood") for r in reasons)
    assert any(r.startswith("statutory_boundary_setback") for r in reasons)
    # soft constraints do NOT become no-build zones
    cons_soft = pe.overlay_layers(PARCEL, [_protected_layer()])
    nbz_soft = pe.no_build_zones(PARCEL, cons_soft, boundary_setback_m=0.0)
    assert nbz_soft == []


def test_buildable_envelope_v1_subtracts_and_traces() -> None:
    cons = pe.overlay_layers(PARCEL, [_flood_layer()])
    nbz = pe.no_build_zones(PARCEL, cons, boundary_setback_m=3.0, setback_rule_id="PL-WT-001")
    env = pe.buildable_envelope_v1(
        PARCEL, no_build=nbz, constraints=cons, analysis_id="a-1"
    )
    # parcel 1200 − flood 450 − setback ring (the part outside the flood) → ~528 m²
    assert env.area_m2 == pytest.approx(528.0, abs=1.0)
    # largest inscribed rectangle computed and contained
    assert env.largest_inscribed_rectangle is not None
    rect = wkt.loads(_geojson_to_wkt(env.largest_inscribed_rectangle))
    assert PARCEL.buffer(1e-6).contains(rect)
    # area-removal trace present (§30 caption source)
    trace = env.metadata["removed_by"]
    labels = {t["label"] for t in trace}
    assert any("flood" in label for label in labels)
    assert sum(t["removed_m2"] for t in trace) == pytest.approx(1200.0 - 528.0, abs=1.0)


def test_envelope_confidence_ranked_by_source() -> None:
    # Binding + cadastral source ⇒ higher envelope confidence than informative + approximate.
    strong = pe.overlay_layers(
        PARCEL,
        [pe.RiskLayer(kind=pe.RiskKind.HERITAGE, geometries=[PROTECTED], status="ok",
                      source_id="pl.nid.wfs.heritage", source_legal_status=LegalStatus.BINDING,
                      source_confidence=0.9)],
    )
    weak = pe.overlay_layers(
        PARCEL,
        [pe.RiskLayer(kind=pe.RiskKind.HERITAGE, geometries=[PROTECTED], status="ok",
                      source_id="weak", source_legal_status=LegalStatus.UNKNOWN,
                      source_confidence=0.4)],
    )
    c_strong = pe.envelope_confidence(
        strong, {"pl.nid.wfs.heritage": GeometryPrecision.CADASTRAL}
    )
    c_weak = pe.envelope_confidence(weak, {"weak": GeometryPrecision.APPROXIMATE})
    assert c_strong > c_weak


def test_decision_hard_blocker_dominates_large_envelope() -> None:
    # A flood hazard (hard) overlaps a SMALL part of a big parcel — envelope is large — yet
    # the decision must be LIKELY_BLOCKED (hard-blocker dominance, §14.2).
    small_flood = wkt.loads("POLYGON((0 0,5 0,5 5,0 5,0 0))")  # 25 m² of 1200 m²
    layer = pe.RiskLayer(kind=pe.RiskKind.FLOOD, geometries=[small_flood], status="ok",
                         source_id="pl.isok.wfs.flood", source_confidence=0.8)
    cons = pe.overlay_layers(PARCEL, [layer])
    nbz = pe.no_build_zones(PARCEL, cons, boundary_setback_m=3.0)
    env = pe.buildable_envelope_v1(PARCEL, no_build=nbz, constraints=cons)
    # Otherwise-large envelope: > 800 m² of the 1200 m² parcel remains buildable.
    assert (env.area_m2 or 0.0) > 800.0
    risks = pe.red_flags(cons, env, parcel_area_m2=PARCEL.area)
    dec = pe.decision(risks, env, parcel_area_m2=PARCEL.area)
    assert dec is Decision.LIKELY_BLOCKED


def test_decision_no_road_access_forces_block() -> None:
    risks = pe.red_flags([], None, parcel_area_m2=PARCEL.area, no_road_access=True)
    assert any(r.severity is Severity.CRITICAL for r in risks)
    dec = pe.decision(risks, None)
    assert dec is Decision.LIKELY_BLOCKED


def test_decision_clean_is_ok_and_soft_is_ok_with_risks() -> None:
    assert pe.decision([], None) is Decision.OK
    cons = pe.overlay_layers(PARCEL, [_protected_layer()])  # soft, medium severity
    risks = pe.red_flags(cons, None, parcel_area_m2=PARCEL.area)
    assert pe.decision(risks, None) is Decision.OK_WITH_RISKS


def test_unknowns_distinguish_unavailable_from_not_detected() -> None:
    unknowns = pe.unknowns_for_unavailable([pe.RiskKind.FLOOD, pe.RiskKind.PROTECTED])
    assert {u.reason for u in unknowns} == {"source_unavailable"}
    # a hard theme unavailable is at least HIGH severity
    flood_unk = next(u for u in unknowns if "flood" in u.topic)
    assert flood_unk.severity is Severity.HIGH


def test_red_flags_carry_status_and_source() -> None:
    cons = pe.overlay_layers(PARCEL, [_flood_layer()])
    risks = pe.red_flags(cons, None, parcel_area_m2=PARCEL.area)
    assert all(r.status is RiskStatus.DETECTED for r in risks)
    assert risks[0].source_id == "pl.isok.wfs.flood"


def _geojson_to_wkt(geojson: dict) -> str:
    from shapely.geometry import shape

    return shape(geojson).wkt


# keep mapping import used (round-trip sanity)
def test_constraint_geometry_is_geojson() -> None:
    cons = pe.overlay_layers(PARCEL, [_flood_layer()])
    assert cons[0].geometry == mapping(__import__("shapely").geometry.shape(cons[0].geometry))
