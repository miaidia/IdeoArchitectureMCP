<?xml version="1.0" encoding="UTF-8"?>
<!--
  SYNTHETIC-but-schema-shaped APP GML fixture (Phase 8 tests; recorded fixture,
  no network). Modeled on the APP application-schema family XSD v2.0 announced by
  Rozporzadzenie w sprawie zbiorow danych przestrzennych oraz metadanych w zakresie
  zagospodarowania przestrzennego (Dz.U. 2023 poz. 2409); schemas published at
  https://www.gov.pl/web/zagospodarowanieprzestrzenne/schematy-aplikacyjne
  Coordinates are EPSG:2180 (easting northing order for the short srsName form).
  Test parcel: square (500000,240000)-(500100,240100), area 10 000 m2.
  Expected coverage: MW 60% (6000 m2), U 40% (4000 m2), ZP 0%.
-->
<gml:FeatureCollection
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:app="https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/2.0"
    xmlns:xlink="http://www.w3.org/1999/xlink">
  <gml:featureMember>
    <app:AktPlanowaniaPrzestrzennego gml:id="APP_FIXTURE_1">
      <app:idIIP>
        <app:Identyfikator>
          <app:przestrzenNazw>PL.ZIPPZP.9999/146501-MPZP</app:przestrzenNazw>
          <app:lokalnyId>MPZP-FIXTURE-1</app:lokalnyId>
          <app:wersjaId>20240801T000000</app:wersjaId>
        </app:Identyfikator>
      </app:idIIP>
      <app:tytul>Miejscowy plan zagospodarowania przestrzennego rejonu ulicy Testowej (fixture)</app:tytul>
      <app:typPlanu>miejscowy plan zagospodarowania przestrzennego</app:typPlanu>
      <app:poziomHierarchii>lokalny</app:poziomHierarchii>
      <app:status>prawnie wiazacy lub realizowany</app:status>
      <app:dataUchwalenia>2021-03-25</app:dataUchwalenia>
      <app:zasiegPrzestrzenny>
        <gml:Polygon gml:id="APP_FIXTURE_1_GEOM" srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>499900 239900 500300 239900 500300 240200 499900 240200 499900 239900</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </app:zasiegPrzestrzenny>
    </app:AktPlanowaniaPrzestrzennego>
  </gml:featureMember>
  <gml:featureMember>
    <app:WydzieleniePlanistyczne gml:id="TEREN_MW_1">
      <app:idIIP>
        <app:Identyfikator>
          <app:przestrzenNazw>PL.ZIPPZP.9999/146501-MPZP</app:przestrzenNazw>
          <app:lokalnyId>TEREN-MW-1</app:lokalnyId>
        </app:Identyfikator>
      </app:idIIP>
      <app:symbol>MW</app:symbol>
      <app:nazwa>Teren zabudowy mieszkaniowej wielorodzinnej</app:nazwa>
      <app:maksymalnaWysokoscZabudowy uom="m">16</app:maksymalnaWysokoscZabudowy>
      <app:maksymalnaIntensywnoscZabudowy>1.2</app:maksymalnaIntensywnoscZabudowy>
      <app:zasiegPrzestrzenny>
        <gml:Polygon gml:id="TEREN_MW_1_GEOM" srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>499950 239950 500060 239950 500060 240150 499950 240150 499950 239950</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </app:zasiegPrzestrzenny>
    </app:WydzieleniePlanistyczne>
  </gml:featureMember>
  <gml:featureMember>
    <app:WydzieleniePlanistyczne gml:id="TEREN_U_1">
      <app:idIIP>
        <app:Identyfikator>
          <app:przestrzenNazw>PL.ZIPPZP.9999/146501-MPZP</app:przestrzenNazw>
          <app:lokalnyId>TEREN-U-1</app:lokalnyId>
        </app:Identyfikator>
      </app:idIIP>
      <app:symbol>U</app:symbol>
      <app:nazwa>Teren zabudowy uslugowej</app:nazwa>
      <app:zasiegPrzestrzenny>
        <gml:Polygon gml:id="TEREN_U_1_GEOM" srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>500060 239950 500200 239950 500200 240150 500060 240150 500060 239950</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </app:zasiegPrzestrzenny>
    </app:WydzieleniePlanistyczne>
  </gml:featureMember>
  <gml:featureMember>
    <app:WydzieleniePlanistyczne gml:id="TEREN_ZP_1">
      <app:idIIP>
        <app:Identyfikator>
          <app:przestrzenNazw>PL.ZIPPZP.9999/146501-MPZP</app:przestrzenNazw>
          <app:lokalnyId>TEREN-ZP-1</app:lokalnyId>
        </app:Identyfikator>
      </app:idIIP>
      <app:symbol>ZP</app:symbol>
      <app:nazwa>Teren zieleni urzadzonej</app:nazwa>
      <app:zasiegPrzestrzenny>
        <gml:Polygon gml:id="TEREN_ZP_1_GEOM" srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>500300 240000 500400 240000 500400 240100 500300 240100 500300 240000</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </app:zasiegPrzestrzenny>
    </app:WydzieleniePlanistyczne>
  </gml:featureMember>
</gml:FeatureCollection>
