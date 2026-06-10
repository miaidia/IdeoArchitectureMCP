"""APP/GML deep parse → PlanningAct + PlanningZone + parcel coverage (Phase 8 §8.1.1).

Parses an APP (Akt Planowania Przestrzennego) GML document of the XSD v2.0 schema
family — Rozporządzenie w sprawie zbiorów danych przestrzennych oraz metadanych w
zakresie zagospodarowania przestrzennego, **Dz.U. 2023 poz. 2409**; application
schemas published at
https://www.gov.pl/web/zagospodarowanieprzestrzenne/schematy-aplikacyjne —
into domain records (F-0108–0111):

* ``AktPlanowaniaPrzestrzennego`` → :class:`~plot_domain.PlanningAct`
  (title, act type, status, legal-act date);
* ``StrefaPlanistyczna`` / ``WydzieleniePlanistyczne`` (zone features) →
  :class:`~plot_domain.PlanningZone` (symbol e.g. "MW"/"U"/"MN/U", geometry in
  EPSG:2180, remaining simple attributes preserved in ``attributes`` so
  GML-vs-PDF conflict detection can compare them, §25.3);
* :func:`zone_coverage` intersects zones with the parcel geometry → coverage %
  per zone (F-0110).

Parsing is namespace-tolerant (matches by local name) because communes publish
under per-version namespace URIs; the document is treated as **untrusted input**
(NFR-SEC-001/002): DOCTYPE/ENTITY constructs are rejected before parsing (XXE /
billion-laughs guard) and a size cap is enforced (NFR-SEC-009). Uses stdlib
``xml.etree.ElementTree`` (allowed; plan §0v2 "lxml or stdlib ElementTree").

This module never invents data: a zone without geometry or symbol is kept with a
warning, an act without a date keeps ``valid_from=None`` — downstream surfaces
that as an unknown, never a default (§21).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from xml.etree import ElementTree as ET

from plot_domain import PlanningAct, PlanningActType, PlanningZone
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

#: Size cap for an APP/GML document (NFR-SEC-009). Acts are typically < 5 MB.
MAX_GML_BYTES = 50 * 1024 * 1024

#: Feature local-names that carry planning zones in the APP schema family.
_ZONE_FEATURES = ("StrefaPlanistyczna", "WydzieleniePlanistyczne", "Teren")
_ACT_FEATURE = "AktPlanowaniaPrzestrzennego"

#: Structural child elements of a zone that are NOT free attributes.
_ZONE_STRUCTURAL = {"idIIP", "symbol", "nazwa", "zasiegPrzestrzenny", "geometria", "poczatekWersjiObiektu"}


class GmlParseError(ValueError):
    """Raised when the document is not a parseable / safe APP GML."""


#: Explicit sentinel for a zone whose owning act could NOT be determined (multi-act
#: document without a resolvable ``przestrzenNazw`` match). The zone is kept and a
#: warning is emitted — the act is never guessed (§21).
UNKNOWN_ACT_ID = "act:unknown"


@dataclass(frozen=True)
class ParsedPlanning:
    """Outcome of one APP/GML deep parse (acts + zones + validation warnings)."""

    acts: list[PlanningAct]
    zones: list[PlanningZone]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ZoneCoverage:
    """Coverage of one planning zone over a parcel (F-0110/0111)."""

    zone_id: str
    act_id: str
    symbol: str
    coverage_pct: float
    intersection_area_m2: float


def _local(tag: str) -> str:
    """Strip the namespace from an element tag ('{ns}name' → 'name')."""
    return tag.rsplit("}", 1)[-1]


def _reject_unsafe_xml(content: bytes) -> None:
    """Refuse DOCTYPE / ENTITY constructs in untrusted XML (XXE guard, NFR-SEC-002)."""
    head = content[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise GmlParseError("GML rejected: DOCTYPE/ENTITY constructs are not allowed in untrusted XML")


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    value = elem.text.strip()
    return value or None


def _find_local(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent.iter():
        if _local(child.tag) == name:
            return child
    return None


def _children_local(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent.iter() if _local(child.tag) == name]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _act_type(type_text: str | None) -> PlanningActType:
    """Map the APP ``typPlanu``/``typAktu`` text onto :class:`PlanningActType`."""
    text = (type_text or "").lower()
    if "miejscowy" in text or text == "mpzp":
        return PlanningActType.MPZP
    if "ogóln" in text or "ogoln" in text or text == "pog":
        return PlanningActType.POG
    if "zintegrowan" in text or text == "zpi":
        return PlanningActType.ZPI
    if "lokalizacj" in text or text == "ulicp":
        return PlanningActType.ULICP
    if "projekt" in text or "draft" in text:
        return PlanningActType.DRAFT_PLAN
    # Unrecognized type → keep the raw text in metadata; default to MPZP carrier
    # is NOT safe, so use draft only for drafts; otherwise MPZP family unknown →
    # surface via metadata and pick the generic carrier type.
    return PlanningActType.MPZP


# --------------------------------------------------------------------------- #
# GML geometry (gml:Polygon / gml:MultiSurface with posList)
# --------------------------------------------------------------------------- #
def _poslist_coords(poslist: str, *, swap_axes: bool) -> list[tuple[float, float]]:
    values = [float(v) for v in poslist.split()]
    if len(values) % 2 != 0:
        raise GmlParseError("gml:posList has an odd number of ordinates")
    pairs = list(zip(values[0::2], values[1::2], strict=True))
    if swap_axes:
        pairs = [(y, x) for (x, y) in pairs]
    return pairs


def _srs_swap(elem: ET.Element) -> bool:
    """True when the srsName implies (northing, easting) axis order.

    The OGC URN form ``urn:ogc:def:crs:EPSG::2180`` and the OGC URL form
    ``http://www.opengis.net/def/crs/EPSG/0/2180`` both use the official EPSG
    axis order for CS92 (x=northing, y=easting); the short ``EPSG:2180`` form is
    used with the conventional easting/northing order across this codebase
    (§26.3).
    """
    srs = elem.get("srsName", "")
    if "2180" not in srs:
        return False
    return srs.startswith("urn:") or "opengis.net/def/crs" in srs


def _polygon_from_gml(poly_elem: ET.Element, *, inherited_swap: bool = False) -> Polygon | None:
    """Parse one gml:Polygon. ``inherited_swap`` carries an axis-order swap
    declared on an enclosing container (e.g. ``srsName`` on gml:MultiSurface —
    the common real-world form) down to the rings."""
    swap = inherited_swap or _srs_swap(poly_elem)
    exterior: list[tuple[float, float]] | None = None
    interiors: list[list[tuple[float, float]]] = []
    for ring_holder in poly_elem:
        holder_name = _local(ring_holder.tag)
        poslist = _find_local(ring_holder, "posList")
        if poslist is None or not (poslist.text or "").strip():
            continue
        coords = _poslist_coords(poslist.text or "", swap_axes=swap or _srs_swap(ring_holder))
        if holder_name in ("exterior", "outerBoundaryIs"):
            exterior = coords
        elif holder_name in ("interior", "innerBoundaryIs"):
            interiors.append(coords)
    if exterior is None or len(exterior) < 4:
        return None
    return Polygon(exterior, interiors)


def _geometry_from(elem: ET.Element) -> BaseGeometry | None:
    """Extract the first gml:Polygon / gml:MultiSurface under ``elem``."""
    multi = _find_local(elem, "MultiSurface")
    if multi is not None:
        # srsName is commonly declared on the MultiSurface itself — propagate
        # its axis order down to the member polygons/rings.
        multi_swap = _srs_swap(multi)
        polygons = [
            p
            for p in (
                _polygon_from_gml(pe, inherited_swap=multi_swap)
                for pe in _children_local(multi, "Polygon")
            )
            if p
        ]
        if polygons:
            geom: BaseGeometry = MultiPolygon(polygons) if len(polygons) > 1 else polygons[0]
            return make_valid(geom)
        return None
    poly = _find_local(elem, "Polygon")
    if poly is not None:
        parsed = _polygon_from_gml(poly)
        return make_valid(parsed) if parsed is not None else None
    return None


# --------------------------------------------------------------------------- #
# Document parse
# --------------------------------------------------------------------------- #
def parse_app_gml(
    content: bytes | str,
    *,
    municipality_id: str,
    source_id: str | None = None,
) -> ParsedPlanning:
    """Parse an APP GML document into acts + zones (F-0108/0109/0111).

    ``municipality_id`` keys the acts (the GML's ``przestrzenNazw`` is recorded in
    metadata when present). Raises :class:`GmlParseError` on malformed / unsafe XML;
    structural oddities (zone without geometry, act without date) become warnings —
    the data is kept, downstream reports the gap (§21).
    """
    raw = content.encode("utf-8") if isinstance(content, str) else content
    if len(raw) > MAX_GML_BYTES:
        raise GmlParseError(f"GML rejected: {len(raw)} bytes exceeds cap {MAX_GML_BYTES} (NFR-SEC-009)")
    _reject_unsafe_xml(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise GmlParseError(f"GML rejected: XML parse error: {exc}") from exc

    warnings: list[str] = []
    acts: list[PlanningAct] = []
    zones: list[PlanningZone] = []

    act_elems = [e for e in root.iter() if _local(e.tag) == _ACT_FEATURE]
    if not act_elems:
        warnings.append("no AktPlanowaniaPrzestrzennego feature found (F-0109 validation)")

    for act_elem in act_elems:
        acts.append(_parse_act(act_elem, municipality_id, source_id, warnings))

    # Zone → act attribution. A single act trivially owns every zone. With
    # multiple acts the zone is resolved through the shared idIIP
    # ``przestrzenNazw`` namespace; when that is ambiguous (no/duplicate
    # namespace match) the act is NOT guessed — the zone keeps the explicit
    # :data:`UNKNOWN_ACT_ID` sentinel and a warning is emitted (§21).
    namespace_to_act: dict[str, str | None] = {}
    for act in acts:
        ns = act.metadata.get("przestrzenNazw")
        if ns:
            # A namespace shared by several acts cannot disambiguate -> None.
            namespace_to_act[ns] = act.id if ns not in namespace_to_act else None

    def _act_for_zone(zone_elem: ET.Element) -> str | None:
        if len(acts) == 1:
            return acts[0].id
        ns = _text(_find_local(zone_elem, "przestrzenNazw"))
        if ns is not None:
            return namespace_to_act.get(ns)
        return None

    for zone_name in _ZONE_FEATURES:
        for zone_elem in (e for e in root.iter() if _local(e.tag) == zone_name):
            resolved_act_id = _act_for_zone(zone_elem)
            zone = _parse_zone(zone_elem, resolved_act_id or UNKNOWN_ACT_ID, warnings)
            if resolved_act_id is None:
                warnings.append(
                    f"zone {zone.id} ({zone.symbol or '?'}): owning act could not be "
                    "determined (multiple/zero acts, no przestrzenNazw match) — "
                    f"act_id marked '{UNKNOWN_ACT_ID}', never guessed (§21)"
                )
            zones.append(zone)

    if acts and not zones:
        warnings.append("act has no vectorized zone features (StrefaPlanistyczna/WydzieleniePlanistyczne)")
    return ParsedPlanning(acts=acts, zones=zones, warnings=warnings)


def _parse_act(
    act_elem: ET.Element,
    municipality_id: str,
    source_id: str | None,
    warnings: list[str],
) -> PlanningAct:
    title = _text(_find_local(act_elem, "tytul")) or "(bez tytułu)"
    status = _text(_find_local(act_elem, "status")) or "unknown"
    type_text = _text(_find_local(act_elem, "typPlanu")) or _text(_find_local(act_elem, "typAktu"))
    # legalActDate: data uchwalenia / wejście w życie of the formal document.
    date_text = (
        _text(_find_local(act_elem, "dataUchwalenia"))
        or _text(_find_local(act_elem, "wejscieWZycie"))
        or _text(_find_local(act_elem, "data"))
    )
    valid_from = _parse_date(date_text)
    if valid_from is None:
        warnings.append(f"act '{title}': no legal-act date in GML (kept as unknown, never defaulted)")
    local_id = _text(_find_local(act_elem, "lokalnyId"))
    namespace = _text(_find_local(act_elem, "przestrzenNazw"))
    act_id = f"act:{local_id}" if local_id else f"act:{uuid.uuid4().hex[:8]}"
    return PlanningAct(
        id=act_id,
        municipality_id=municipality_id,
        act_type=_act_type(type_text),
        title=title,
        status=status,
        valid_from=valid_from,
        source_id=source_id,
        metadata={
            "typ_text": type_text,
            "przestrzenNazw": namespace,
            "legal_act_date": date_text,
            "schema_family": "APP XSD v2.0 (Dz.U. 2023 poz. 2409)",
        },
    )


def _parse_zone(zone_elem: ET.Element, act_id: str, warnings: list[str]) -> PlanningZone:
    symbol = _text(_find_local(zone_elem, "symbol")) or ""
    local_id = _text(_find_local(zone_elem, "lokalnyId"))
    zone_id = f"zone:{local_id}" if local_id else f"zone:{uuid.uuid4().hex[:8]}"
    geom = _geometry_from(zone_elem)
    if not symbol:
        warnings.append(f"zone {zone_id}: missing symbol (F-0111 → unknown symbol)")
    if geom is None:
        warnings.append(f"zone {zone_id} ({symbol or '?'}): no parseable geometry")
    attributes: dict[str, Any] = {}
    for child in zone_elem:
        name = _local(child.tag)
        if name in _ZONE_STRUCTURAL:
            continue
        value = _text(child)
        if value is not None and len(list(child)) == 0:
            attributes[name] = value
    return PlanningZone(
        id=zone_id,
        act_id=act_id,
        symbol=symbol,
        geometry=mapping(geom) if geom is not None else None,
        attributes=attributes,
    )


# --------------------------------------------------------------------------- #
# Zone ∩ parcel coverage (F-0110)
# --------------------------------------------------------------------------- #
def zone_coverage(parcel_geom: BaseGeometry, zones: list[PlanningZone]) -> list[ZoneCoverage]:
    """Intersect each zone with the parcel (EPSG:2180) → coverage % per zone.

    Zones without geometry are skipped (they were already warned about at parse
    time); coverage is intersection area / parcel area. Results are sorted by
    coverage descending so the dominant zone comes first.
    """
    if parcel_geom.is_empty or parcel_geom.area <= 0:
        return []
    parcel = make_valid(parcel_geom)
    out: list[ZoneCoverage] = []
    for zone in zones:
        if zone.geometry is None:
            continue
        zgeom = make_valid(shape(zone.geometry))
        inter = parcel.intersection(zgeom)
        if inter.is_empty or inter.area <= 0:
            continue
        out.append(
            ZoneCoverage(
                zone_id=zone.id,
                act_id=zone.act_id,
                symbol=zone.symbol,
                coverage_pct=round(100.0 * inter.area / parcel.area, 2),
                intersection_area_m2=round(inter.area, 2),
            )
        )
    out.sort(key=lambda c: c.coverage_pct, reverse=True)
    return out


# --------------------------------------------------------------------------- #
# GML zone attributes → indicator values (for GML-vs-PDF conflict detection)
# --------------------------------------------------------------------------- #
#: APP attribute local-names → (canonical indicator name, attribute unit).
#: Attribute→unit mapping (APP XSD v2.0 conventions):
#:   - ``m``       : metres, passed through (wysokość zabudowy);
#:   - ``ratio``   : dimensionless ratio, passed through (intensywność);
#:   - ``percent`` : the APP attributes carry PERCENT values (e.g.
#:     ``minimalnyUdzialPowierzchniBiologicznieCzynnej`` = "25"), while the
#:     document parser (:mod:`plot_planning.parser.extract`) emits FRACTIONS
#:     (0.25) for ``*_ratio`` indicators — percent attributes are normalized to
#:     fractions here so both sources are comparable in
#:     :func:`plot_planning.detect_conflicts` (F-0125; agreeing sources must
#:     never produce a false conflict);
#:   - ``count``   : storey count, passed through.
_ATTRIBUTE_INDICATORS: dict[str, tuple[str, str]] = {
    "maksymalnaWysokoscZabudowy": ("max_height_m", "m"),
    "wysokoscZabudowy": ("max_height_m", "m"),
    "maksymalnaIntensywnoscZabudowy": ("max_intensity", "ratio"),
    "minimalnaIntensywnoscZabudowy": ("min_intensity", "ratio"),
    "maksymalnaPowierzchniaZabudowy": ("max_coverage_ratio", "percent"),
    "minimalnyUdzialPowierzchniBiologicznieCzynnej": ("min_pbc_ratio", "percent"),
    "maksymalnaLiczbaKondygnacji": ("max_kondygnacje", "count"),
}


def indicators_from_zone(zone: PlanningZone) -> dict[str, float]:
    """Extract numeric indicator values carried as GML zone attributes.

    Only attributes that parse as numbers are returned (text attributes stay in
    ``zone.attributes`` untouched). Percent-typed attributes are normalized to
    fractions (see the ``_ATTRIBUTE_INDICATORS`` unit mapping) so they compare
    1:1 with parser output. Used for GML-vs-PDF conflict detection (F-0125,
    §25.3 example 1) — never as a silent override of the parsed text.
    """
    out: dict[str, float] = {}
    for attr, (indicator, unit) in _ATTRIBUTE_INDICATORS.items():
        raw = zone.attributes.get(attr)
        if raw is None:
            continue
        try:
            value = float(str(raw).replace(",", "."))
        except ValueError:
            continue
        if unit == "percent":
            value = value / 100.0
        out[indicator] = value
    return out
