"""Water / flood / retention context (Phase 12 / v1 Phase 10 §10.1.2).

Consumes ALREADY-FETCHED geometries (ISOK flood layers + BDOT10k watercourses,
EPSG:2180 shapely) — no network, no ``plot_connectors`` (§9.4). Produces:

* flood overlap (% of parcel) + the union geometry kept for envelope clipping;
* **per-stage envelope clipping** for a masterplan (delta 1: flood zones clip the
  buildable envelope per stage; stages with flooded buildings are flagged);
* watercourse distance + a water-law precheck (pozwolenie/zgłoszenie wodnoprawne
  is a PROCEDURAL question, never auto-decided);
* retention / infiltration class — a documented heuristic (slope + flood
  presence), ``basis: industry_heuristic``, verify-before-prod;
* GZWP / water-intake zones — honest ``source_not_wired`` unknown (no GZWP
  connector profile exists yet; absence of data is NEVER "no constraint", §21).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from plot_domain import ConfidenceLevel, RiskItem, RiskStatus, RiskType, Severity, UnknownItem
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

#: Distance under which a watercourse triggers the water-law precheck question
#: (pas przybrzeżny / prace w pobliżu wód — procedural flag, not a legal value:
#: the actual obligation depends on the works, Prawo wodne art. 389–395).
_WATER_LAW_FLAG_DISTANCE_M = 50.0


@dataclass
class WaterAnalysis:
    """Water/flood context for one parcel (v1 Phase 10 §10.1.2)."""

    status: str  # "ok" | "no_data"
    flood_checked: bool = False
    flood_detected: bool = False
    flood_coverage_percent: float = 0.0
    watercourse_checked: bool = False
    watercourse_distance_m: float | None = None
    water_law_precheck: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)
    gzwp: dict[str, Any] = field(default_factory=dict)
    risks: list[RiskItem] = field(default_factory=list)
    unknowns: list[UnknownItem] = field(default_factory=list)
    #: Union of flood-zone geometries (EPSG:2180) — kept for envelope clipping.
    _flood_geom: BaseGeometry | None = None

    def clip_envelope(self, envelope: BaseGeometry) -> tuple[BaseGeometry, float]:
        """Envelope minus flood zones → (clipped geometry, removed m²)."""
        if self._flood_geom is None or self._flood_geom.is_empty:
            return envelope, 0.0
        clipped = envelope.difference(self._flood_geom)
        return clipped, max(0.0, float(envelope.area - clipped.area))

    def stage_flood_checks(
        self,
        envelope: BaseGeometry | None,
        buildings: list[tuple[str, int | None, BaseGeometry]],
    ) -> list[dict[str, Any]]:
        """Delta 1: per-STAGE flood flags + clipped stage envelope areas.

        ``buildings`` are ``(name, stage, footprint)`` from a masterplan proposal.
        For each stage: which buildings intersect a flood zone, and how much of
        the buildable envelope that stage actually retains after the flood clip.
        Without flood data the check honestly reports ``unknown`` per stage.
        """
        stages = sorted({s if s is not None else 1 for _, s, _ in buildings})
        out: list[dict[str, Any]] = []
        for stage in stages:
            stage_buildings = [
                (name, geom) for name, s, geom in buildings if (s if s is not None else 1) == stage
            ]
            if not self.flood_checked:
                out.append(
                    {
                        "stage": stage,
                        "status": "unknown",
                        "reason": "flood_layer_unavailable",
                        "affected_buildings": [],
                    }
                )
                continue
            affected = [
                name
                for name, geom in stage_buildings
                if self._flood_geom is not None and geom.intersects(self._flood_geom)
            ]
            entry: dict[str, Any] = {
                "stage": stage,
                "status": "flagged" if affected else "clear",
                "affected_buildings": affected,
            }
            if envelope is not None:
                clipped, removed = self.clip_envelope(envelope)
                entry["envelope_area_m2"] = round(float(envelope.area), 1)
                entry["envelope_after_flood_clip_m2"] = round(float(clipped.area), 1)
                entry["envelope_removed_by_flood_m2"] = round(removed, 1)
            out.append(entry)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "flood_checked": self.flood_checked,
            "flood_detected": self.flood_detected,
            "flood_coverage_percent": self.flood_coverage_percent,
            "watercourse_checked": self.watercourse_checked,
            "watercourse_distance_m": self.watercourse_distance_m,
            "water_law_precheck": self.water_law_precheck,
            "retention": self.retention,
            "gzwp": self.gzwp,
        }


def analyze_water(
    parcel: BaseGeometry,
    *,
    flood_geoms: list[BaseGeometry] | None,
    watercourse_geoms: list[BaseGeometry] | None,
    flood_status: str = "ok",
    watercourse_status: str = "ok",
    mean_slope_pct: float | None = None,
) -> WaterAnalysis:
    """Build the water/flood context from fetched layers (v1 Phase 10 §10.1.2).

    ``flood_geoms`` / ``watercourse_geoms`` are the connector-fetched geometries;
    a ``*_status`` of ``source_unavailable`` makes the theme an explicit unknown
    (NEVER "no constraint" — §21). ``mean_slope_pct`` (from the terrain module,
    when available) feeds the retention heuristic.
    """
    analysis = WaterAnalysis(status="ok")
    parcel_area = float(parcel.area)

    # --- flood ------------------------------------------------------------- #
    if flood_status == "source_unavailable" or flood_geoms is None:
        analysis.unknowns.append(
            _unknown(
                "Strefy zagrożenia powodziowego (ISOK MZP/MRP)",
                "source_unavailable",
                "Ponowić pobranie ISOK / potwierdzić w Wodach Polskich — brak danych "
                "NIE oznacza braku zagrożenia.",
                Severity.HIGH,
            )
        )
    else:
        analysis.flood_checked = True
        flood_union = unary_union([g for g in flood_geoms if not g.is_empty]) if flood_geoms else None
        if flood_union is not None and not flood_union.is_empty:
            overlap = parcel.intersection(flood_union)
            if not overlap.is_empty and overlap.area > 0:
                analysis.flood_detected = True
                analysis._flood_geom = flood_union
                analysis.flood_coverage_percent = round(
                    100.0 * float(overlap.area) / parcel_area if parcel_area else 0.0, 2
                )
                analysis.risks.append(
                    RiskItem(
                        id=f"risk:flood:{uuid.uuid4().hex[:8]}",
                        risk_type=RiskType.FLOOD,
                        severity=Severity.HIGH,
                        confidence=ConfidenceLevel.MEDIUM,
                        status=RiskStatus.DETECTED,
                        summary=(
                            f"Strefa zagrożenia powodziowego pokrywa "
                            f"{analysis.flood_coverage_percent:.1f}% działki — obszar "
                            "szczególnego zagrożenia ogranicza zabudowę (Prawo wodne)."
                        ),
                        mitigation=(
                            "Zweryfikować scenariusz (MZP 1%/10%) w ISOK; zabudowa w strefie "
                            "wymaga zgody wodnoprawnej lub jest wykluczona."
                        ),
                    )
                )

    # --- watercourses + water-law precheck ---------------------------------- #
    if watercourse_status == "source_unavailable" or watercourse_geoms is None:
        analysis.unknowns.append(
            _unknown(
                "Cieki i rowy w sąsiedztwie (BDOT10k)",
                "source_unavailable",
                "Ponowić pobranie warstwy cieków — odległości od wód nieznane.",
                Severity.MEDIUM,
            )
        )
    else:
        analysis.watercourse_checked = True
        courses = [g for g in watercourse_geoms if not g.is_empty]
        if courses:
            dist = min(float(parcel.distance(g)) for g in courses)
            analysis.watercourse_distance_m = round(dist, 1)
        flagged = (
            analysis.watercourse_distance_m is not None
            and analysis.watercourse_distance_m <= _WATER_LAW_FLAG_DISTANCE_M
        )
        analysis.water_law_precheck = {
            "procedure_question": bool(flagged or analysis.flood_detected),
            "watercourse_distance_m": analysis.watercourse_distance_m,
            "note": (
                "Bliskość wód / strefa powodziowa — zweryfikować obowiązek pozwolenia/"
                "zgłoszenia wodnoprawnego (Prawo wodne) u Wód Polskich."
                if flagged or analysis.flood_detected
                else "Brak przesłanki wodnoprawnej z próbkowanych warstw."
            ),
        }

    # --- retention / infiltration class heuristic ---------------------------- #
    analysis.retention = _retention_class(mean_slope_pct, analysis.flood_detected)

    # --- GZWP / intake zones: source not wired → explicit unknown ------------- #
    analysis.gzwp = {"status": "unknown", "reason": "source_not_wired"}
    analysis.unknowns.append(
        _unknown(
            "GZWP / strefy ochronne ujęć wody",
            "source_not_wired",
            "Sprawdzić PIG-PIB (GZWP) i wykaz stref ochronnych ujęć — źródło "
            "niepodłączone w tej wersji.",
            Severity.LOW,
        )
    )
    return analysis


def _retention_class(mean_slope_pct: float | None, flood_detected: bool) -> dict[str, Any]:
    """Retention/infiltration class — documented heuristic, verify-before-prod.

    Without soil-permeability data (no SGP source wired) the class derives from
    terrain slope (fast runoff vs ponding) + flood presence; it is a DESIGN HINT
    (blue-green infrastructure sizing), never a legal retention requirement.
    """
    if mean_slope_pct is None:
        return {
            "class": None,
            "status": "unknown",
            "reason": "terrain_slope_unknown",
            "basis": "industry_heuristic",
        }
    if flood_detected:
        cls, note = "ograniczona", "strefa powodziowa — retencja na działce ograniczona"
    elif mean_slope_pct < 2.0:
        cls, note = "dobra", "teren płaski — infiltracja/retencja powierzchniowa możliwa"
    elif mean_slope_pct < 8.0:
        cls, note = "srednia", "umiarkowany spadek — retencja wymaga elementów opóźniających"
    else:
        cls, note = "ograniczona", "duży spadek — szybki spływ, retencja kubaturowa"
    return {
        "class": cls,
        "note": note + " (heurystyka bez danych gruntowych — zweryfikować przed produkcją)",
        "basis": "industry_heuristic",
    }


def _unknown(topic: str, reason: str, action: str, severity: Severity) -> UnknownItem:
    return UnknownItem(
        id=f"unk:water:{uuid.uuid4().hex[:8]}",
        topic=topic,
        severity=severity,
        reason=reason,
        suggested_action=action,
    )
