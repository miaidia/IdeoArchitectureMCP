"""Environment + heritage context (Phase 12 / v1 Phase 10 §10.1.4).

* **Protected-nature overlay** (GDOŚ/CRFOP geometries, already fetched).
* **EIA screening by investment type** — RULESET-DRIVEN: thresholds come from
  ``rulesets/PL/environmental/eia-screening.yaml`` (rough rozporządzenie OOŚ
  values marked verify-before-prod there); NO legal threshold lives in this code
  (anti-pattern guard v1 §10.4).
* **Heritage zones** (NID geometries) → constraints + konserwator questions.
* **Delta 1 masterplan integration**: :func:`heritage_interventions` — proposals
  touching a heritage zone get a WARNING + a konserwator question, and every
  ``zabytek_do_remontu`` building gets the uzgodnienie-konserwatorskie note
  (an extension of the heritage handling, validator-style soft entries).

Tree detection (orthophoto/LiDAR) has no source wired → explicit unknown.
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
from plot_rules import RulesetRegistry
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

#: The EIA-screening rule id (thresholds live in its YAML, never here).
RULE_EIA_SCREENING = "PL-EIA-SCREENING-001"

#: InvestmentType → eia-screening threshold key prefix. ``unknown`` → no screening
#: claim (the result stays unknown — never a default, §0v2.4).
_EIA_TYPE_KEYS: dict[str, str] = {
    "single_family": "mieszkaniowa",
    "multifamily": "mieszkaniowa",
    "services": "uslugowa",
    "warehouse": "przemyslowa",
    "mixed": "mieszkaniowa",  # dominant-residential simplification (documented)
}


@dataclass
class EnvironmentAnalysis:
    """Environment + heritage context for one parcel (v1 Phase 10 §10.1.4)."""

    status: str  # "ok" | "no_data"
    protected_checked: bool = False
    protected_detected: bool = False
    protected_coverage_percent: float = 0.0
    eia_screening: dict[str, Any] = field(default_factory=dict)
    heritage_checked: bool = False
    heritage_detected: bool = False
    heritage_coverage_percent: float = 0.0
    trees: dict[str, Any] = field(default_factory=dict)
    risks: list[RiskItem] = field(default_factory=list)
    unknowns: list[UnknownItem] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    #: Union of heritage-zone geometries — kept for masterplan intervention checks.
    _heritage_geom: BaseGeometry | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "protected_checked": self.protected_checked,
            "protected_detected": self.protected_detected,
            "protected_coverage_percent": self.protected_coverage_percent,
            "eia_screening": self.eia_screening,
            "heritage_checked": self.heritage_checked,
            "heritage_detected": self.heritage_detected,
            "heritage_coverage_percent": self.heritage_coverage_percent,
            "trees": self.trees,
        }


def eia_screening(
    registry: RulesetRegistry,
    *,
    investment_type: str,
    investment_area_m2: float | None,
    in_protected_area: bool,
    area_basis: str | None = None,
) -> dict[str, Any]:
    """EIA screening by investment type — thresholds READ from the ruleset YAML.

    Returns an explainable block: required (bool) / unknown with reason, the
    threshold + rule citation, and the verify-before-prod marker carried from
    the rule itself. ``investment_type`` is the §10.6 ``InvestmentType`` value.

    The ruleset thresholds are POWIERZCHNIA ZABUDOWY/TERENU (ha) — never compare
    raw GFA against them (review M4). ``area_basis`` documents what
    ``investment_area_m2`` actually is (e.g. ``masterplan_footprint``,
    ``gfa_capped_proxy_conservative``, ``parcel_area_upper_bound``) and is
    carried into the output so consumers see the basis.
    """
    rule = registry.get(RULE_EIA_SCREENING)
    if rule is None:
        return {
            "required": None,
            "status": "unknown",
            "reason": "eia_ruleset_missing",
            "note": "Brak rulesetu eia-screening.yaml — screening nieobliczalny.",
        }
    thresholds = rule.raw.get("thresholds") or {}
    key_prefix = _EIA_TYPE_KEYS.get(investment_type)
    if key_prefix is None:
        return {
            "required": None,
            "status": "unknown",
            "reason": "investment_type_unknown",
            "rule_id": rule.id,
            "note": "Typ inwestycji nieokreślony — screening OOŚ wymaga typu przedsięwzięcia.",
        }
    if investment_area_m2 is None or investment_area_m2 <= 0:
        return {
            "required": None,
            "status": "unknown",
            "reason": "investment_area_unknown",
            "rule_id": rule.id,
            "note": "Powierzchnia przedsięwzięcia nieznana — próg OOŚ nieporównywalny.",
        }
    key = f"{key_prefix}_protected_ha" if in_protected_area else f"{key_prefix}_ha"
    threshold_ha = thresholds.get(key)
    if not isinstance(threshold_ha, int | float):
        return {
            "required": None,
            "status": "unknown",
            "reason": f"threshold_missing:{key}",
            "rule_id": rule.id,
        }
    area_ha = investment_area_m2 / 10_000.0
    required = area_ha >= float(threshold_ha)
    return {
        "required": required,
        "status": "screened",
        "group": "II" if required else None,
        "investment_type": investment_type,
        "investment_area_ha": round(area_ha, 4),
        "area_basis": area_basis,
        "threshold_ha": float(threshold_ha),
        "in_protected_area": in_protected_area,
        "rule_id": rule.id,
        "source_reference": rule.source_reference,
        "verification": rule.raw.get("verification"),
        "note": (
            "Przekroczony próg grupy II — karta informacyjna przedsięwzięcia / decyzja "
            "środowiskowa do potwierdzenia z RDOŚ (progi: verify-before-prod)."
            if required
            else "Poniżej progu grupy II wg rulesetu (progi: verify-before-prod)."
        ),
    }


def analyze_environment(
    parcel: BaseGeometry,
    registry: RulesetRegistry,
    *,
    protected_geoms: list[BaseGeometry] | None,
    heritage_geoms: list[BaseGeometry] | None,
    protected_status: str = "ok",
    heritage_status: str = "ok",
    investment_type: str = "unknown",
    investment_area_m2: float | None = None,
    investment_area_basis: str | None = None,
) -> EnvironmentAnalysis:
    """Build the environment/heritage context from fetched layers (§10.1.4).

    ``investment_area_m2`` must be a FOOTPRINT-basis area (powierzchnia
    zabudowy/terenu — the EIA thresholds' unit, review M4); the caller labels it
    via ``investment_area_basis`` (carried into the screening output).
    """
    analysis = EnvironmentAnalysis(status="ok")
    parcel_area = float(parcel.area)

    # --- protected nature (GDOŚ/CRFOP) -------------------------------------- #
    if protected_status == "source_unavailable" or protected_geoms is None:
        analysis.unknowns.append(
            _unknown(
                "Formy ochrony przyrody (GDOŚ/CRFOP)",
                "source_unavailable",
                "Ponowić pobranie GDOŚ — status ochronny nieznany, nie zakładać braku ochrony.",
                Severity.HIGH,
            )
        )
    else:
        analysis.protected_checked = True
        union = (
            unary_union([g for g in protected_geoms if not g.is_empty])
            if protected_geoms
            else None
        )
        if union is not None and not union.is_empty:
            overlap = parcel.intersection(union)
            if not overlap.is_empty and overlap.area > 0:
                analysis.protected_detected = True
                analysis.protected_coverage_percent = round(
                    100.0 * float(overlap.area) / parcel_area if parcel_area else 0.0, 2
                )
                analysis.risks.append(
                    RiskItem(
                        id=f"risk:protected:{uuid.uuid4().hex[:8]}",
                        risk_type=RiskType.ENVIRONMENTAL,
                        severity=Severity.MEDIUM,
                        confidence=ConfidenceLevel.MEDIUM,
                        status=RiskStatus.DETECTED,
                        summary=(
                            f"Forma ochrony przyrody pokrywa "
                            f"{analysis.protected_coverage_percent:.1f}% działki "
                            "(Natura 2000 / park / rezerwat / OChK wg CRFOP)."
                        ),
                        mitigation=(
                            "Zweryfikować formę i zakazy w CRFOP/RDOŚ; możliwa inwentaryzacja "
                            "przyrodnicza i ocena habitatowa."
                        ),
                    )
                )
                analysis.recommendations.append(
                    Recommendation(
                        id=f"rec:rdos:{uuid.uuid4().hex[:8]}",
                        title="Zapytanie do RDOŚ o uwarunkowania formy ochrony",
                        detail=(
                            "Potwierdzić nazwę/typ formy ochrony i obowiązujące zakazy dla "
                            "zamierzenia inwestycyjnego."
                        ),
                        priority=Severity.MEDIUM,
                        addressed_to="RDOŚ",
                    )
                )

    # --- EIA screening (ruleset-driven; needs protected status when checked) -- #
    analysis.eia_screening = eia_screening(
        registry,
        investment_type=investment_type,
        investment_area_m2=investment_area_m2,
        in_protected_area=analysis.protected_detected,
        area_basis=investment_area_basis,
    )
    if not analysis.protected_checked and analysis.eia_screening.get("status") == "screened":
        # The protected flag could not be verified — the LOWER protected threshold
        # may actually apply; mark the screening as provisional (§21 honesty).
        analysis.eia_screening["note"] = (
            str(analysis.eia_screening.get("note", ""))
            + " UWAGA: status formy ochrony niezweryfikowany (źródło niedostępne) — "
            "próg obniżony może mieć zastosowanie."
        )

    # --- heritage (NID) ------------------------------------------------------ #
    if heritage_status == "source_unavailable" or heritage_geoms is None:
        analysis.unknowns.append(
            _unknown(
                "Zabytki / strefy ochrony konserwatorskiej (NID)",
                "source_unavailable",
                "Ponowić pobranie NID / zapytać WKZ — status zabytkowy nieznany.",
                Severity.HIGH,
            )
        )
    else:
        analysis.heritage_checked = True
        union = (
            unary_union([g for g in heritage_geoms if not g.is_empty]) if heritage_geoms else None
        )
        if union is not None and not union.is_empty:
            overlap = parcel.intersection(union)
            if not overlap.is_empty and overlap.area > 0:
                analysis.heritage_detected = True
                analysis._heritage_geom = union
                analysis.heritage_coverage_percent = round(
                    100.0 * float(overlap.area) / parcel_area if parcel_area else 0.0, 2
                )
                analysis.risks.append(
                    RiskItem(
                        id=f"risk:heritage:{uuid.uuid4().hex[:8]}",
                        risk_type=RiskType.HERITAGE,
                        severity=Severity.MEDIUM,
                        confidence=ConfidenceLevel.MEDIUM,
                        status=RiskStatus.DETECTED,
                        summary=(
                            f"Strefa/obiekt ochrony zabytków pokrywa "
                            f"{analysis.heritage_coverage_percent:.1f}% działki (NID)."
                        ),
                        mitigation=(
                            "Rozbiórki, przebudowy, wysokość, materiały i dach wymagają "
                            "uzgodnienia z wojewódzkim konserwatorem zabytków."
                        ),
                    )
                )
                analysis.recommendations.append(
                    Recommendation(
                        id=f"rec:wkz:{uuid.uuid4().hex[:8]}",
                        title="Uzgodnienie z konserwatorem zabytków (WKZ)",
                        detail=(
                            "Potwierdzić wpis (rejestr/GEZ), strefy ochrony i archeologię; "
                            "ustalić dopuszczalny zakres ingerencji w obiekty zabytkowe."
                        ),
                        priority=Severity.HIGH,
                        addressed_to="Wojewódzki Konserwator Zabytków",
                    )
                )

    # --- trees: orthophoto/LiDAR detection not wired → explicit unknown ------- #
    analysis.trees = {"status": "unknown", "reason": "source_not_wired"}
    analysis.unknowns.append(
        _unknown(
            "Zadrzewienie działki (ortofoto/LiDAR)",
            "source_not_wired",
            "Wykonać wizję lokalną / analizę ortofoto — wycinka może wymagać zezwolenia.",
            Severity.LOW,
        )
    )
    return analysis


def heritage_interventions(
    analysis: EnvironmentAnalysis | None,
    buildings: list[tuple[str, str, BaseGeometry]],
) -> list[dict[str, Any]]:
    """Delta 1: heritage constraints on masterplan interventions (§10.1.4).

    ``buildings`` are ``(name, status, footprint)`` from a masterplan proposal.
    Validator-style soft entries (extension of the heritage handling):

    * every ``zabytek_do_remontu`` building → konserwator-uzgodnienie note
      (remont/przebudowa zabytku ALWAYS needs WKZ — Dz.U. 2022 poz. 840 art. 36,
      procedural fact, no threshold involved);
    * any NEW building intersecting a detected heritage zone → WARNING + WKZ
      question;
    * heritage layer unavailable → honest ``unknown`` per zabytek building.
    """
    out: list[dict[str, Any]] = []
    heritage_geom = analysis._heritage_geom if analysis is not None else None
    heritage_checked = bool(analysis is not None and analysis.heritage_checked)
    for name, status, footprint in buildings:
        if status == "zabytek_do_remontu":
            entry = {
                "building": name,
                "kind": "zabytek_do_remontu",
                "status": "warning",
                "requires_konserwator": True,
                "message": (
                    f"{name}: remont/przebudowa obiektu zabytkowego wymaga pozwolenia "
                    "wojewódzkiego konserwatora zabytków (art. 36 ustawy o ochronie "
                    "zabytków) — zakres ingerencji do uzgodnienia."
                ),
                "question": (
                    f"WKZ: jaki zakres remontu/adaptacji budynku '{name}' jest dopuszczalny "
                    "(elewacje, dach, układ wnętrz, detale)?"
                ),
            }
            if not heritage_checked:
                entry["note"] = (
                    "Warstwa NID niedostępna — wpis do rejestru/GEZ niezweryfikowany "
                    "(status przyjęty z deklaracji propozycji)."
                )
            out.append(entry)
        elif (
            status in ("projektowany", "w_budowie")
            and heritage_geom is not None
            and footprint.intersects(heritage_geom)
        ):
            out.append(
                {
                    "building": name,
                    "kind": "new_in_heritage_zone",
                    "status": "warning",
                    "requires_konserwator": True,
                    "message": (
                        f"{name}: nowy budynek w strefie ochrony konserwatorskiej — "
                        "forma, wysokość i materiały podlegają uzgodnieniu z WKZ."
                    ),
                    "question": (
                        f"WKZ: jakie warunki kompozycyjne obowiązują nową zabudowę "
                        f"('{name}') w strefie ochrony?"
                    ),
                }
            )
    return out


def _unknown(topic: str, reason: str, action: str, severity: Severity) -> UnknownItem:
    return UnknownItem(
        id=f"unk:env:{uuid.uuid4().hex[:8]}",
        topic=topic,
        severity=severity,
        reason=reason,
        suggested_action=action,
    )
