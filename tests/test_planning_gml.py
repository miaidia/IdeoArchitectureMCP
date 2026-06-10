"""APP/GML pipeline tests (Phase 8 §8.1.1; F-0108–0111).

Recorded synthetic-but-schema-shaped fixture (APP XSD v2.0 family, Dz.U. 2023
poz. 2409 — schemas at gov.pl/web/zagospodarowanieprzestrzenne/schematy-aplikacyjne):
acts + zones parse, symbol recognition, zone∩parcel coverage %, GML validation
(unsafe XML rejected), axis-order handling, and the GML-attribute → indicator
mapping used for conflict detection.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from plot_domain import PlanningActType
from plot_planning import (
    UNKNOWN_ACT_ID,
    GmlParseError,
    ParsedPlanning,
    PlanningStore,
    indicators_from_zone,
    parse_app_gml,
    zone_coverage,
)
from shapely.geometry import box, shape

FIXTURE = Path(__file__).parent / "fixtures" / "planning" / "app_gml_sample.gml"
#: Test parcel from the fixture header: 100 x 100 m square, EPSG:2180.
PARCEL = box(500000, 240000, 500100, 240100)


@pytest.fixture
def parsed():
    return parse_app_gml(FIXTURE.read_bytes(), municipality_id="146501", source_id="src:gml")


# --------------------------------------------------------------------------- #
# Act + zone parse (F-0108/0109/0111)
# --------------------------------------------------------------------------- #
def test_act_metadata_parsed(parsed) -> None:
    assert len(parsed.acts) == 1
    act = parsed.acts[0]
    assert act.id == "act:MPZP-FIXTURE-1"
    assert act.act_type is PlanningActType.MPZP
    assert act.status == "prawnie wiazacy lub realizowany"
    assert act.valid_from == date(2021, 3, 25)  # legalActDate from dataUchwalenia
    assert act.municipality_id == "146501"
    assert act.source_id == "src:gml"
    assert act.metadata["schema_family"] == "APP XSD v2.0 (Dz.U. 2023 poz. 2409)"


def test_zone_symbols_recognized(parsed) -> None:
    symbols = {z.symbol for z in parsed.zones}
    assert symbols == {"MW", "U", "ZP"}  # F-0111 symbol recognition
    for zone in parsed.zones:
        assert zone.act_id == "act:MPZP-FIXTURE-1"
        assert zone.geometry is not None
        assert shape(zone.geometry).is_valid


def test_zone_attributes_preserved_for_conflict_detection(parsed) -> None:
    mw = next(z for z in parsed.zones if z.symbol == "MW")
    assert mw.attributes["maksymalnaWysokoscZabudowy"] == "16"
    # GML attributes → canonical indicator names (F-0125 comparison input)
    indicators = indicators_from_zone(mw)
    assert indicators["max_height_m"] == 16.0
    assert indicators["max_intensity"] == 1.2


# --------------------------------------------------------------------------- #
# Coverage % (F-0110)
# --------------------------------------------------------------------------- #
def test_zone_coverage_percentages_exact(parsed) -> None:
    coverage = zone_coverage(PARCEL, parsed.zones)
    by_symbol = {c.symbol: c for c in coverage}
    # MW covers the west 60 m of the 100 m parcel; U the east 40 m; ZP is outside.
    assert by_symbol["MW"].coverage_pct == 60.0
    assert by_symbol["MW"].intersection_area_m2 == 6000.0
    assert by_symbol["U"].coverage_pct == 40.0
    assert by_symbol["U"].intersection_area_m2 == 4000.0
    assert "ZP" not in by_symbol  # zero intersection is not reported as coverage
    # sorted by coverage descending (dominant zone first)
    assert [c.symbol for c in coverage] == ["MW", "U"]


def test_zone_coverage_empty_parcel_is_empty() -> None:
    from shapely.geometry import Polygon

    parsed = parse_app_gml(FIXTURE.read_bytes(), municipality_id="146501")
    assert zone_coverage(Polygon(), parsed.zones) == []


# --------------------------------------------------------------------------- #
# Validation + untrusted-XML guards (F-0109; NFR-SEC-002)
# --------------------------------------------------------------------------- #
def test_doctype_rejected() -> None:
    evil = b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><x>&e;</x>"
    with pytest.raises(GmlParseError, match="DOCTYPE/ENTITY"):
        parse_app_gml(evil, municipality_id="146501")


def test_malformed_xml_rejected() -> None:
    with pytest.raises(GmlParseError, match="parse error"):
        parse_app_gml(b"<not-closed>", municipality_id="146501")


def test_size_cap_enforced() -> None:
    import plot_planning.gml as gml_mod

    oversized = b"x" * (gml_mod.MAX_GML_BYTES + 1)
    with pytest.raises(GmlParseError, match="NFR-SEC-009"):
        parse_app_gml(oversized, municipality_id="146501")


def test_document_without_act_warns_not_invents() -> None:
    doc = b"<?xml version='1.0'?><FeatureCollection></FeatureCollection>"
    parsed = parse_app_gml(doc, municipality_id="146501")
    assert parsed.acts == []
    assert parsed.zones == []
    assert any("no AktPlanowaniaPrzestrzennego" in w for w in parsed.warnings)


def test_urn_srs_axis_order_swapped() -> None:
    """urn:ogc:def:crs:EPSG::2180 → (northing, easting) posList is swapped to x/y."""
    doc = """<?xml version="1.0"?>
    <gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"
        xmlns:app="https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/2.0">
      <gml:featureMember>
        <app:AktPlanowaniaPrzestrzennego>
          <app:tytul>Axis test</app:tytul>
          <app:status>obowiazujacy</app:status>
          <app:dataUchwalenia>2024-01-01</app:dataUchwalenia>
        </app:AktPlanowaniaPrzestrzennego>
      </gml:featureMember>
      <gml:featureMember>
        <app:WydzieleniePlanistyczne>
          <app:symbol>MN</app:symbol>
          <app:zasiegPrzestrzenny>
            <gml:Polygon srsName="urn:ogc:def:crs:EPSG::2180">
              <gml:exterior><gml:LinearRing>
                <gml:posList>240000 500000 240000 500100 240100 500100 240100 500000 240000 500000</gml:posList>
              </gml:LinearRing></gml:exterior>
            </gml:Polygon>
          </app:zasiegPrzestrzenny>
        </app:WydzieleniePlanistyczne>
      </gml:featureMember>
    </gml:FeatureCollection>"""
    parsed = parse_app_gml(doc, municipality_id="146501")
    geom = shape(parsed.zones[0].geometry)
    # After the swap the polygon is the (500000..500100, 240000..240100) square.
    assert geom.bounds == (500000.0, 240000.0, 500100.0, 240100.0)
    assert zone_coverage(PARCEL, parsed.zones)[0].coverage_pct == 100.0


def _axis_doc(zone_geometry_xml: str) -> str:
    return f"""<?xml version="1.0"?>
    <gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"
        xmlns:app="https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/2.0">
      <gml:featureMember>
        <app:AktPlanowaniaPrzestrzennego>
          <app:tytul>Axis test</app:tytul>
          <app:status>obowiazujacy</app:status>
          <app:dataUchwalenia>2024-01-01</app:dataUchwalenia>
        </app:AktPlanowaniaPrzestrzennego>
      </gml:featureMember>
      <gml:featureMember>
        <app:WydzieleniePlanistyczne>
          <app:symbol>MN</app:symbol>
          <app:zasiegPrzestrzenny>
            {zone_geometry_xml}
          </app:zasiegPrzestrzenny>
        </app:WydzieleniePlanistyczne>
      </gml:featureMember>
    </gml:FeatureCollection>"""


#: posList in (northing, easting) order for the (500000..500100, 240000..240100) square.
_NORTHING_FIRST_RING = (
    "<gml:exterior><gml:LinearRing>"
    "<gml:posList>240000 500000 240000 500100 240100 500100 240100 500000 240000 500000</gml:posList>"
    "</gml:LinearRing></gml:exterior>"
)


def test_multisurface_level_srs_axis_order_swapped() -> None:
    """M2 regression: srsName declared on gml:MultiSurface (the common real-world
    form) must propagate the axis swap to its member polygons — otherwise the
    coordinates are transposed and zone∩parcel coverage is silently empty."""
    doc = _axis_doc(
        '<gml:MultiSurface srsName="urn:ogc:def:crs:EPSG::2180">'
        "<gml:surfaceMember><gml:Polygon>" + _NORTHING_FIRST_RING + "</gml:Polygon>"
        "</gml:surfaceMember></gml:MultiSurface>"
    )
    parsed = parse_app_gml(doc, municipality_id="146501")
    geom = shape(parsed.zones[0].geometry)
    assert geom.bounds == (500000.0, 240000.0, 500100.0, 240100.0)
    assert zone_coverage(PARCEL, parsed.zones)[0].coverage_pct == 100.0


def test_ogc_url_form_crs_axis_order_swapped() -> None:
    """M2 regression: the OGC URL CRS form implies (northing, easting) like the
    URN form — it must be recognized and swapped."""
    doc = _axis_doc(
        '<gml:Polygon srsName="http://www.opengis.net/def/crs/EPSG/0/2180">'
        + _NORTHING_FIRST_RING
        + "</gml:Polygon>"
    )
    parsed = parse_app_gml(doc, municipality_id="146501")
    geom = shape(parsed.zones[0].geometry)
    assert geom.bounds == (500000.0, 240000.0, 500100.0, 240100.0)
    assert zone_coverage(PARCEL, parsed.zones)[0].coverage_pct == 100.0


# --------------------------------------------------------------------------- #
# Multi-act zone attribution (M3) + idempotent store ingest (m3)
# --------------------------------------------------------------------------- #
TWO_ACTS_GML = """<?xml version="1.0"?>
<gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:app="https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/2.0">
  <gml:featureMember>
    <app:AktPlanowaniaPrzestrzennego>
      <app:idIIP><app:Identyfikator>
        <app:przestrzenNazw>PL.ZIPPZP.9999/146501-MPZP-A</app:przestrzenNazw>
        <app:lokalnyId>ACT-1</app:lokalnyId>
      </app:Identyfikator></app:idIIP>
      <app:tytul>Plan A</app:tytul>
      <app:status>obowiazujacy</app:status>
      <app:dataUchwalenia>2020-01-01</app:dataUchwalenia>
    </app:AktPlanowaniaPrzestrzennego>
  </gml:featureMember>
  <gml:featureMember>
    <app:AktPlanowaniaPrzestrzennego>
      <app:idIIP><app:Identyfikator>
        <app:przestrzenNazw>PL.ZIPPZP.9999/146501-MPZP-B</app:przestrzenNazw>
        <app:lokalnyId>ACT-2</app:lokalnyId>
      </app:Identyfikator></app:idIIP>
      <app:tytul>Plan B</app:tytul>
      <app:status>obowiazujacy</app:status>
      <app:dataUchwalenia>2022-01-01</app:dataUchwalenia>
    </app:AktPlanowaniaPrzestrzennego>
  </gml:featureMember>
  <gml:featureMember>
    <app:WydzieleniePlanistyczne>
      <app:idIIP><app:Identyfikator>
        <app:przestrzenNazw>PL.ZIPPZP.9999/146501-MPZP-B</app:przestrzenNazw>
        <app:lokalnyId>TEREN-MW-B</app:lokalnyId>
      </app:Identyfikator></app:idIIP>
      <app:symbol>MW</app:symbol>
    </app:WydzieleniePlanistyczne>
  </gml:featureMember>
  <gml:featureMember>
    <app:WydzieleniePlanistyczne>
      <app:idIIP><app:Identyfikator>
        <app:lokalnyId>TEREN-U-X</app:lokalnyId>
      </app:Identyfikator></app:idIIP>
      <app:symbol>U</app:symbol>
    </app:WydzieleniePlanistyczne>
  </gml:featureMember>
</gml:FeatureCollection>"""


def test_multi_act_zone_attributed_via_namespace_never_first_act() -> None:
    """M3 regression: in a multi-act document a zone is attributed via the shared
    przestrzenNazw namespace (here → the SECOND act), and an unmatchable zone is
    marked with the explicit unknown sentinel + a warning — never acts[0]."""
    parsed = parse_app_gml(TWO_ACTS_GML, municipality_id="146501")
    assert [a.id for a in parsed.acts] == ["act:ACT-1", "act:ACT-2"]
    by_symbol = {z.symbol: z for z in parsed.zones}
    assert by_symbol["MW"].act_id == "act:ACT-2"  # namespace match, not acts[0]
    assert by_symbol["U"].act_id == UNKNOWN_ACT_ID  # ambiguous → never guessed
    assert any(
        "zone:TEREN-U-X" in w and UNKNOWN_ACT_ID in w for w in parsed.warnings
    )


def test_store_reingest_is_idempotent() -> None:
    """m3 regression: re-ingesting the same parse result (including act-less
    results) must not duplicate zones."""
    store = PlanningStore()
    parsed = parse_app_gml(FIXTURE.read_bytes(), municipality_id="146501")
    store.ingest("146501", parsed)
    store.ingest("146501", parsed)
    assert len(store.acts_for("146501")) == 1
    assert len(store.zones_for("146501")) == 3
    # An act-less ParsedPlanning (zones only) is deduped by zone id.
    actless = ParsedPlanning(acts=[], zones=list(parsed.zones))
    store.ingest("146501", actless)
    store.ingest("146501", actless)
    assert len(store.zones_for("146501")) == 3
