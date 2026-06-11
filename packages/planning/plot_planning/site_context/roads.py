"""Roads + utilities context (Phase 12 / v1 Phase 10 §10.1.5).

Consumes ALREADY-FETCHED BDOT10k road geometries + GESUT utility features
(EPSG:2180 shapely + properties). Produces:

* :class:`~plot_domain.RoadAccess` — public-road adjacency, frontage length,
  access chain (direct or honestly unknown — servitude tracing needs EGiB
  neighbour parcels, not wired), zjazd feasibility precheck + zarządca question;
* :class:`~plot_domain.UtilityNetwork` records with nearest distances, technical
  zones (buffer per network type — CONFIG defaults, ruleset-overridable like the
  ``plot_envelope`` layer policy) and collision warnings vs proposal geometry;
* **delta 1**: :func:`check_zjazd_kdw` — the masterplan's KDW road must connect
  to the parcel boundary segment that fronts a public road (the zjazd point),
  else the whole internal layout has no legal access.

Geometric tolerances here are sampling/topology parameters (``geometry_sampling``),
not legal values. No network, no ``plot_connectors`` (§9.4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from plot_domain import (
    Recommendation,
    RiskItem,
    RoadAccess,
    Severity,
    UnknownItem,
    UtilityNetwork,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

#: Topology tolerance: BDOT10k road axes are not snapped to cadastral boundaries;
#: a road within this distance of the boundary counts as ADJACENT (sampling
#: parameter, documented — geometry_sampling, not law).
ADJACENCY_TOL_M = 5.0

#: Minimal frontage to consider a standard zjazd geometrically plausible
#: (industry_heuristic — the legal decision belongs to the zarządca drogi).
MIN_ZJAZD_FRONTAGE_M = 4.5

#: Technical-zone half-widths per network type (CONFIG defaults, marked basis;
#: gestor-specific values override these in practice — same discipline as
#: plot_envelope.LAYER_POLICY buffers).
TECHNICAL_ZONE_M: dict[str, float] = {
    "water": 1.5,
    "sewer": 1.5,
    "gas": 1.5,
    "power": 1.0,
    "heat": 2.0,
    "telecom": 0.5,
    "unknown": 1.5,
}

#: GESUT property keys → canonical network type (representative attribute names;
#: real schemas vary per powiat — unknown keys fall back to "unknown").
_NETWORK_TYPE_KEYS = ("network_type", "rodzajSieci", "rodzaj_siec", "RODZAJ")
_NETWORK_TYPE_MAP: dict[str, str] = {
    "wodociagowa": "water",
    "woda": "water",
    "kanalizacyjna": "sewer",
    "kanalizacja": "sewer",
    "gazowa": "gas",
    "gaz": "gas",
    "elektroenergetyczna": "power",
    "energetyczna": "power",
    "cieplownicza": "heat",
    "cieplo": "heat",
    "telekomunikacyjna": "telecom",
}


@dataclass
class AccessAnalysis:
    """Roads + utilities context for one parcel (v1 Phase 10 §10.1.5)."""

    status: str  # "ok" | "no_data"
    road_access: RoadAccess | None = None
    zjazd: dict[str, Any] = field(default_factory=dict)
    utilities: list[UtilityNetwork] = field(default_factory=list)
    utility_collisions: list[dict[str, Any]] = field(default_factory=list)
    technical_zones: list[dict[str, Any]] = field(default_factory=list)
    risks: list[RiskItem] = field(default_factory=list)
    unknowns: list[UnknownItem] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    #: Union of public-road geometries — kept for the KDW zjazd check (delta 1).
    _road_geom: BaseGeometry | None = None
    #: (network_type, geometry) pairs — kept for proposal collision checks.
    _networks: list[tuple[str, BaseGeometry]] = field(default_factory=list)

    def network_collisions(
        self, elements: list[tuple[str, BaseGeometry]]
    ) -> list[dict[str, Any]]:
        """Collision warnings: proposal elements crossing a network's technical zone.

        ``elements`` are ``(label, geometry)`` (building footprints / road polys).
        Returns soft warnings — relocation/przebudowa sieci is a cost driver, the
        legal arbiter is the gestor (questions generated upstream).
        """
        out: list[dict[str, Any]] = []
        for label, geom in elements:
            for net_type, net_geom in self._networks:
                zone = net_geom.buffer(TECHNICAL_ZONE_M.get(net_type, 1.5))
                if geom.intersects(zone):
                    out.append(
                        {
                            "element": label,
                            "network_type": net_type,
                            "status": "warning",
                            "overlap_m2": round(float(geom.intersection(zone).area), 2),
                            "message": (
                                f"{label}: kolizja ze strefą techniczną sieci "
                                f"'{net_type}' — możliwa przebudowa/przełożenie sieci "
                                "(uzgodnienie z gestorem)."
                            ),
                        }
                    )
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "road_access": (
                self.road_access.model_dump(mode="json") if self.road_access else None
            ),
            "zjazd": self.zjazd,
            "utilities": [u.model_dump(mode="json") for u in self.utilities],
            "utility_collisions": self.utility_collisions,
            "technical_zones": self.technical_zones,
        }


def analyze_access(
    parcel: BaseGeometry,
    *,
    road_geoms: list[BaseGeometry] | None,
    utility_features: list[dict[str, Any]] | None,
    roads_status: str = "ok",
    utilities_status: str = "ok",
    road_source_id: str | None = None,
    utility_source_id: str | None = None,
) -> AccessAnalysis:
    """Build the roads/utilities context from fetched layers (§10.1.5).

    ``utility_features`` are GeoJSON-Feature-shaped dicts (``geometry`` +
    ``properties``) so the GESUT network type can be read from attributes.
    """
    from shapely.geometry import shape

    analysis = AccessAnalysis(status="ok")

    # --- public-road adjacency + frontage + access chain ---------------------- #
    if roads_status == "source_unavailable" or road_geoms is None:
        analysis.unknowns.append(
            _unknown(
                "Dostęp do drogi publicznej (BDOT10k)",
                "source_unavailable",
                "Ponowić pobranie warstwy dróg / potwierdzić dostęp w zarządzie dróg — "
                "brak danych NIE oznacza braku (ani istnienia) dostępu.",
                Severity.HIGH,
            )
        )
        analysis.road_access = RoadAccess(
            id=f"roadaccess:{uuid.uuid4().hex[:8]}",
            has_public_road_access=None,  # honestly unknown (§21)
            source_id=road_source_id,
        )
        analysis.zjazd = {"feasibility": None, "status": "unknown", "reason": "roads_unavailable"}
    else:
        roads_union = (
            unary_union([g for g in road_geoms if not g.is_empty]) if road_geoms else None
        )
        if roads_union is not None and not roads_union.is_empty:
            analysis._road_geom = roads_union
            adjacent = bool(parcel.distance(roads_union) <= ADJACENCY_TOL_M)
            frontage = 0.0
            if adjacent:
                frontage = float(
                    parcel.boundary.intersection(roads_union.buffer(ADJACENCY_TOL_M)).length
                )
            analysis.road_access = RoadAccess(
                id=f"roadaccess:{uuid.uuid4().hex[:8]}",
                has_public_road_access=adjacent,
                frontage_length_m=round(frontage, 1) if adjacent else 0.0,
                access_chain=["droga_publiczna:bezposredni"] if adjacent else [],
                source_id=road_source_id,
            )
            if not adjacent:
                # No DIRECT adjacency — servitude/internal-road tracing needs EGiB
                # neighbour parcels (not wired) → unknown chain, NOT "no access".
                analysis.unknowns.append(
                    _unknown(
                        "Łańcuch dostępu (służebność / droga wewnętrzna)",
                        "no_direct_adjacency",
                        "Sprawdzić KW (służebności) i działki drogowe pośrednie — brak "
                        "bezpośredniego styku z drogą publiczną w próbkowanych danych.",
                        Severity.HIGH,
                    )
                )
            analysis.zjazd = _zjazd_precheck(adjacent, frontage)
            if analysis.zjazd.get("question"):
                analysis.recommendations.append(
                    Recommendation(
                        id=f"rec:zjazd:{uuid.uuid4().hex[:8]}",
                        title="Zapytanie do zarządcy drogi o zjazd",
                        detail=str(analysis.zjazd["question"]),
                        priority=Severity.MEDIUM,
                        addressed_to="zarządca drogi (gmina/powiat/GDDKiA)",
                    )
                )
        else:
            # Layer queried OK but no road feature in the analysis window: that is
            # a checked-and-clear answer for the WINDOW, still a HIGH risk flag.
            analysis.road_access = RoadAccess(
                id=f"roadaccess:{uuid.uuid4().hex[:8]}",
                has_public_road_access=False,
                frontage_length_m=0.0,
                source_id=road_source_id,
            )
            analysis.zjazd = {
                "feasibility": "brak_przeslanek",
                "status": "checked",
                "note": "Brak drogi w oknie analizy — dostęp wymaga służebności/poszerzenia okna.",
            }

    # --- utilities (GESUT) ----------------------------------------------------- #
    if utilities_status == "source_unavailable" or utility_features is None:
        analysis.unknowns.append(
            _unknown(
                "Uzbrojenie terenu (GESUT/KIUT)",
                "source_unavailable",
                "Ponowić pobranie GESUT / zapytać gestorów — sieci i strefy techniczne "
                "nieznane.",
                Severity.MEDIUM,
            )
        )
    else:
        for feat in utility_features:
            geom_json = feat.get("geometry")
            if not isinstance(geom_json, dict):
                continue
            try:
                geom = shape(geom_json)
            except (ValueError, TypeError, KeyError):
                continue
            if geom.is_empty:
                continue
            net_type = _network_type(feat.get("properties") or {})
            distance = round(float(parcel.distance(geom)), 1)
            analysis._networks.append((net_type, geom))
            analysis.utilities.append(
                UtilityNetwork(
                    id=f"util:{uuid.uuid4().hex[:8]}",
                    network_type=net_type,
                    distance_m=distance,
                    operator=_operator(feat.get("properties") or {}),
                    source_id=utility_source_id,
                )
            )
            zone_m = TECHNICAL_ZONE_M.get(net_type, 1.5)
            crossing = geom.buffer(zone_m).intersection(parcel)
            if not crossing.is_empty and crossing.area > 0:
                analysis.technical_zones.append(
                    {
                        "network_type": net_type,
                        "zone_m": zone_m,
                        "area_m2": round(float(crossing.area), 1),
                        "basis": "config_default_gestor_overridable",
                        "note": (
                            f"Sieć '{net_type}' przecina działkę — strefa techniczna "
                            f"±{zone_m} m ogranicza posadowienie (uzgodnić z gestorem)."
                        ),
                    }
                )
        # Connection capacity is a gestor answer, never derivable from geometry.
        analysis.unknowns.append(
            _unknown(
                "Warunki przyłączenia (moc/przepustowość)",
                "requires_gestor_statement",
                "Wystąpić o warunki techniczne przyłączenia do gestorów sieci.",
                Severity.MEDIUM,
            )
        )
    return analysis


def check_zjazd_kdw(
    kdw_roads: list[tuple[str, BaseGeometry]],
    parcel: BaseGeometry,
    public_road_geom: BaseGeometry | None,
    *,
    tol_m: float = ADJACENCY_TOL_M,
) -> dict[str, Any]:
    """Delta 1: validate the masterplan's KDW connection point (zjazd).

    The internal-road network (function ``kdw``) must reach the parcel-boundary
    segment that FRONTS a public road — that segment is where a legal zjazd can
    exist. ``kdw_roads`` are ``(name, centerline geometry)``. Returns a
    validator-style dict: ``pass`` / ``fail`` / ``unknown`` with evidence.
    """
    if not kdw_roads:
        return {
            "check": "zjazd_kdw",
            "status": "not_applicable",
            "message": "Masterplan nie zawiera dróg wewnętrznych (kdw).",
        }
    if public_road_geom is None or public_road_geom.is_empty:
        return {
            "check": "zjazd_kdw",
            "status": "unknown",
            "reason": "public_road_layer_unavailable",
            "message": (
                "Nie można zweryfikować punktu włączenia KDW — warstwa dróg "
                "publicznych niedostępna (brak danych ≠ brak wymogu)."
            ),
        }
    # The frontage strip: the part of the parcel boundary adjacent to the road.
    frontage = parcel.boundary.intersection(public_road_geom.buffer(tol_m))
    if frontage.is_empty or frontage.length <= 0:
        return {
            "check": "zjazd_kdw",
            "status": "fail",
            "message": (
                "Działka nie styka się z drogą publiczną w oknie analizy — układ KDW "
                "nie ma legalnego punktu włączenia (zjazdu)."
            ),
        }
    connected = [
        name for name, geom in kdw_roads if geom.distance(frontage) <= tol_m
    ]
    if connected:
        return {
            "check": "zjazd_kdw",
            "status": "pass",
            "connected_roads": connected,
            "frontage_length_m": round(float(frontage.length), 1),
            "message": (
                f"Droga wewnętrzna ({', '.join(connected)}) dochodzi do frontu działki "
                "przy drodze publicznej — punkt zjazdu geometrycznie możliwy "
                "(decyzja zjazdowa: zarządca drogi)."
            ),
        }
    return {
        "check": "zjazd_kdw",
        "status": "fail",
        "frontage_length_m": round(float(frontage.length), 1),
        "message": (
            "Żadna droga wewnętrzna (kdw) nie dochodzi do frontu działki przy drodze "
            "publicznej — układ komunikacyjny masterplanu nie ma punktu zjazdu."
        ),
    }


def _zjazd_precheck(adjacent: bool, frontage_m: float) -> dict[str, Any]:
    if not adjacent:
        return {
            "feasibility": "watpliwy",
            "status": "checked",
            "note": "Brak bezpośredniego styku z drogą — zjazd wymaga służebności/dojazdu.",
            "question": "Czy istnieje prawnie zabezpieczony dostęp (służebność/zjazd) do działki?",
        }
    if frontage_m >= MIN_ZJAZD_FRONTAGE_M:
        return {
            "feasibility": "mozliwy",
            "status": "checked",
            "frontage_length_m": round(frontage_m, 1),
            "basis": "industry_heuristic",
            "note": "Front wystarczający geometrycznie; lokalizację zjazdu uzgadnia zarządca.",
            "question": "Czy zarządca drogi dopuszcza zjazd (lokalizacja/parametry) na froncie działki?",
        }
    return {
        "feasibility": "watpliwy",
        "status": "checked",
        "frontage_length_m": round(frontage_m, 1),
        "basis": "industry_heuristic",
        "note": f"Front {frontage_m:.1f} m poniżej praktycznego minimum dla zjazdu.",
        "question": "Czy zarządca drogi dopuści zjazd przy tak krótkim froncie działki?",
    }


def _network_type(props: dict[str, Any]) -> str:
    for key in _NETWORK_TYPE_KEYS:
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            low = val.strip().lower()
            if low in TECHNICAL_ZONE_M:
                return low
            for token, canonical in _NETWORK_TYPE_MAP.items():
                if token in low:
                    return canonical
    return "unknown"


def _operator(props: dict[str, Any]) -> str | None:
    for key in ("operator", "gestor", "wladajacy"):
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _unknown(topic: str, reason: str, action: str, severity: Severity) -> UnknownItem:
    return UnknownItem(
        id=f"unk:access:{uuid.uuid4().hex[:8]}",
        topic=topic,
        severity=severity,
        reason=reason,
        suggested_action=action,
    )
