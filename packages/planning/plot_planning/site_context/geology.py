"""Geology / landslide / mining context (Phase 12 / v1 Phase 10 §10.1.3).

Consumes ALREADY-FETCHED SOPO landslide geometries (EPSG:2180 shapely). Mining
areas (MIDAS/ROG) and CBDG borehole context have NO connector profile yet — they
are explicit ``source_not_wired`` unknowns (data absence ≠ "no constraint", §21).

Outputs a geotechnical risk class + an investigation brief STUB driven by the
collected :class:`~plot_domain.UnknownItem`s (v1 Phase 10 §10.1.3 "geotechnical
risk score + investigation brief").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from plot_domain import (
    ConfidenceLevel,
    Recommendation,
    RiskItem,
    RiskStatus,
    RiskType,
    Severity,
    UnknownItem,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

#: Geotechnical risk classes, ordered (comparative — not a geotechnical opinion).
GEOTECH_CLASSES: tuple[str, ...] = ("niskie", "umiarkowane", "wysokie")


@dataclass
class GeologyAnalysis:
    """Geology context for one parcel (v1 Phase 10 §10.1.3)."""

    status: str  # "ok" | "no_data"
    landslide_checked: bool = False
    landslide_detected: bool = False
    landslide_coverage_percent: float = 0.0
    mining: dict[str, Any] = field(default_factory=dict)
    geotech_risk_class: str | None = None
    investigation_brief: list[str] = field(default_factory=list)
    risks: list[RiskItem] = field(default_factory=list)
    unknowns: list[UnknownItem] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "landslide_checked": self.landslide_checked,
            "landslide_detected": self.landslide_detected,
            "landslide_coverage_percent": self.landslide_coverage_percent,
            "mining": self.mining,
            "geotech_risk_class": self.geotech_risk_class,
            "investigation_brief": self.investigation_brief,
        }


def analyze_geology(
    parcel: BaseGeometry,
    *,
    landslide_geoms: list[BaseGeometry] | None,
    landslide_status: str = "ok",
) -> GeologyAnalysis:
    """Build the geology context from the fetched SOPO layer (§10.1.3)."""
    analysis = GeologyAnalysis(status="ok")
    parcel_area = float(parcel.area)

    if landslide_status == "source_unavailable" or landslide_geoms is None:
        analysis.unknowns.append(
            UnknownItem(
                id=f"unk:sopo:{uuid.uuid4().hex[:8]}",
                topic="Osuwiska / tereny zagrożone ruchami masowymi (SOPO)",
                severity=Severity.HIGH,
                reason="source_unavailable",
                suggested_action=(
                    "Ponowić pobranie SOPO (PIG-PIB) — ryzyko osuwiskowe pozostaje "
                    "nieznane, nie zakładać jego braku."
                ),
            )
        )
    else:
        analysis.landslide_checked = True
        union = (
            unary_union([g for g in landslide_geoms if not g.is_empty])
            if landslide_geoms
            else None
        )
        if union is not None and not union.is_empty:
            overlap = parcel.intersection(union)
            if not overlap.is_empty and overlap.area > 0:
                analysis.landslide_detected = True
                analysis.landslide_coverage_percent = round(
                    100.0 * float(overlap.area) / parcel_area if parcel_area else 0.0, 2
                )
                analysis.risks.append(
                    RiskItem(
                        id=f"risk:landslide:{uuid.uuid4().hex[:8]}",
                        risk_type=RiskType.GEOLOGY,
                        severity=Severity.HIGH,
                        confidence=ConfidenceLevel.MEDIUM,
                        status=RiskStatus.DETECTED,
                        summary=(
                            f"Teren osuwiskowy/zagrożony ruchami masowymi pokrywa "
                            f"{analysis.landslide_coverage_percent:.1f}% działki (SOPO)."
                        ),
                        mitigation=(
                            "Wymagana dokumentacja geologiczno-inżynierska; zabudowa na "
                            "osuwisku zwykle wykluczona lub silnie ograniczona."
                        ),
                    )
                )

    # Mining/deposits: no MIDAS/ROG connector wired → explicit unknown (§21).
    analysis.mining = {"status": "unknown", "reason": "source_not_wired"}
    analysis.unknowns.append(
        UnknownItem(
            id=f"unk:mining:{uuid.uuid4().hex[:8]}",
            topic="Obszary/tereny górnicze i złoża (MIDAS/ROG)",
            severity=Severity.MEDIUM,
            reason="source_not_wired",
            suggested_action=(
                "Sprawdzić MIDAS (PIG-PIB) / rejestr obszarów górniczych — źródło "
                "niepodłączone w tej wersji."
            ),
        )
    )

    # Risk class + investigation brief (stub DRIVEN by the unknowns, §10.1.3).
    if not analysis.landslide_checked:
        analysis.geotech_risk_class = None  # honestly unknown without SOPO
    elif analysis.landslide_detected:
        analysis.geotech_risk_class = GEOTECH_CLASSES[2]
    else:
        # SOPO clear, but soil/groundwater are unverified — never "niskie" blind.
        analysis.geotech_risk_class = GEOTECH_CLASSES[1]

    analysis.investigation_brief = _investigation_brief(analysis)
    analysis.recommendations.append(
        Recommendation(
            id=f"rec:geotech:{uuid.uuid4().hex[:8]}",
            title="Zlecić wstępne badania geotechniczne",
            detail="; ".join(analysis.investigation_brief),
            priority=Severity.HIGH if analysis.landslide_detected else Severity.MEDIUM,
            addressed_to="geotechnik / geolog inżynierski",
        )
    )
    return analysis


def _investigation_brief(analysis: GeologyAnalysis) -> list[str]:
    """Investigation-brief items (stub) — one per open question/unknown."""
    brief: list[str] = [
        "Określić kategorię geotechniczną obiektu (Dz.U. 2012 poz. 463) i zakres badań.",
        "Ustalić poziom wód gruntowych (min. 3 otwory dla zabudowy kubaturowej — praktyka).",
    ]
    if analysis.landslide_detected:
        brief.insert(
            0,
            "Dokumentacja geologiczno-inżynierska dla terenu osuwiskowego (SOPO) — "
            "stateczność zbocza przed jakąkolwiek decyzją projektową.",
        )
    if not analysis.landslide_checked:
        brief.append("Potwierdzić status osuwiskowy w SOPO/starostwie (źródło niedostępne).")
    if analysis.mining.get("status") == "unknown":
        brief.append("Zweryfikować szkody górnicze / obszary górnicze (MIDAS) dla lokalizacji.")
    return brief
