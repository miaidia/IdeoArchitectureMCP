"""§14.1 site scores — explainable, confidence-weighted (Phase 12 / v1 §10.1.7).

Each score is a :class:`SiteScore`: 0–1 value (1.0 = favourable), positive +
negative factor lists, a confidence, and an honest ``unknown`` state with a
reason when the underlying SOURCE data is genuinely absent (§20.10 — unknowns
are never faked into numbers). The §14.2 rules are respected at the consumer:

* hard-blocker dominance lives in ``plot_envelope.risk.decision`` — scores are
  COMPARATIVE inputs and never override a LIKELY_BLOCKED decision;
* low confidence raises procedural/data risk (the procedural score counts open
  unknowns as procedural burden).

The dataclass is intentionally framework-neutral so ``plot_agent``'s
``ScoreEvaluator`` (the ``PHASE10_SCORE_HOOKS`` consumer) can map it 1:1 onto
its ``Score`` model without ``plot_planning`` importing ``plot_agent`` (§9.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from plot_planning.site_context import SiteContext


@dataclass
class SiteScore:
    """One explainable §14.1 score (NFR-AUD-006)."""

    name: str
    value: float | None = None
    unknown: bool = False
    unknown_reason: str | None = None
    confidence: float = 0.0
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)

    @classmethod
    def not_computable(cls, name: str, reason: str) -> SiteScore:
        return cls(name=name, value=None, unknown=True, unknown_reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unknown": self.unknown,
            "unknown_reason": self.unknown_reason,
            "confidence": self.confidence,
            "positive_factors": self.positive_factors,
            "negative_factors": self.negative_factors,
        }


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def compute_site_scores(
    site: SiteContext,
    *,
    planning_block: dict[str, Any] | None = None,
    investment_goal: dict[str, Any] | None = None,
    envelope_area_m2: float | None = None,
    parcel_area_m2: float | None = None,
) -> dict[str, SiteScore]:
    """All §14.1 site scores from the collected :class:`SiteContext` (§10.1.7).

    A module that did not run / whose source failed yields an UNKNOWN score with
    the reason — never a default number (§21/§20.10).
    """
    scores: dict[str, SiteScore] = {}
    scores["terrain_score"] = _terrain_score(site)
    scores["environmental_risk_score"] = _environmental_score(site)
    scores["geotechnical_risk_score"] = _geotechnical_score(site)
    scores["heritage_risk_score"] = _heritage_score(site)
    scores["infrastructure_score"] = _infrastructure_score(site)
    scores["procedural_risk_score"] = _procedural_score(site, scores)
    scores["cost_driver_score"] = _cost_driver_score(site)
    scores["planning_certainty_score"] = _planning_certainty_score(planning_block)
    scores["investment_fit_score"] = _investment_fit_score(
        investment_goal, envelope_area_m2, parcel_area_m2
    )
    return scores


# --------------------------------------------------------------------------- #
# Individual scores (1.0 = favourable / low risk)
# --------------------------------------------------------------------------- #
def _terrain_score(site: SiteContext) -> SiteScore:
    t = site.terrain
    if t is None or t.status != "ok":
        return SiteScore.not_computable("terrain_score", "terrain_data_unavailable")
    pos: list[str] = []
    neg: list[str] = []
    mean_slope = float(t.slope.get("mean_pct", 0.0))
    value = 1.0
    cls = t.earthworks.get("parcel_class")
    if cls == "niski":
        pos.append(f"teren płaski (śr. spadek {mean_slope:.1f}%) — minimalna niwelacja")
    elif cls == "umiarkowany":
        value -= 0.2
        neg.append(f"umiarkowany spadek ({mean_slope:.1f}%) — stopniowanie terenu")
    elif cls == "wysoki":
        value -= 0.45
        neg.append(f"duży spadek ({mean_slope:.1f}%) — prawdopodobne mury oporowe")
    else:
        value -= 0.7
        neg.append(f"bardzo duży spadek ({mean_slope:.1f}%) — poważne roboty ziemne")
    if t.depressions.get("detected"):
        value -= 0.1
        neg.append("lokalne zagłębienia — ryzyko stagnacji wód opadowych")
    else:
        pos.append("brak lokalnych zagłębień w próbkowanym oknie")
    return SiteScore(
        name="terrain_score",
        value=round(_clamp(value), 4),
        confidence=0.7,  # raster_derived precision (§5)
        positive_factors=pos,
        negative_factors=neg,
    )


def _environmental_score(site: SiteContext) -> SiteScore:
    env = site.environment
    water = site.water
    if env is None or not env.protected_checked:
        return SiteScore.not_computable(
            "environmental_risk_score", "protected_areas_source_unavailable"
        )
    pos: list[str] = []
    neg: list[str] = []
    value = 1.0
    if env.protected_detected:
        value -= 0.4
        neg.append(
            f"forma ochrony przyrody pokrywa {env.protected_coverage_percent:.1f}% działki"
        )
    else:
        pos.append("brak formy ochrony przyrody na działce (CRFOP sprawdzony)")
    eia = env.eia_screening
    if eia.get("required") is True:
        value -= 0.25
        neg.append(f"screening OOŚ wymagany (grupa II, próg {eia.get('threshold_ha')} ha)")
    elif eia.get("required") is False:
        pos.append("poniżej progu screeningu OOŚ wg rulesetu")
    else:
        neg.append(f"screening OOŚ nieobliczalny ({eia.get('reason')})")
    if water is not None and water.flood_checked:
        if water.flood_detected:
            value -= 0.35
            neg.append(
                f"strefa powodziowa pokrywa {water.flood_coverage_percent:.1f}% działki"
            )
        else:
            pos.append("brak strefy zagrożenia powodziowego (ISOK sprawdzony)")
    else:
        neg.append("warstwa powodziowa niedostępna — składowa pominięta w wartości")
    return SiteScore(
        name="environmental_risk_score",
        value=round(_clamp(value), 4),
        confidence=0.65 if (water is not None and water.flood_checked) else 0.45,
        positive_factors=pos,
        negative_factors=neg,
    )


def _geotechnical_score(site: SiteContext) -> SiteScore:
    g = site.geology
    if g is None or not g.landslide_checked:
        return SiteScore.not_computable("geotechnical_risk_score", "sopo_source_unavailable")
    pos: list[str] = []
    neg: list[str] = []
    value = 1.0
    if g.landslide_detected:
        value -= 0.7
        neg.append(
            f"osuwisko/teren zagrożony pokrywa {g.landslide_coverage_percent:.1f}% działki (SOPO)"
        )
    else:
        pos.append("brak osuwisk w SOPO dla działki")
    # Soil/groundwater are unverified everywhere until badania — capped optimism.
    value -= 0.15
    neg.append("warunki gruntowo-wodne niezbadane (brief badań wygenerowany)")
    if g.mining.get("status") == "unknown":
        neg.append("obszary górnicze niezweryfikowane (źródło niepodłączone)")
    return SiteScore(
        name="geotechnical_risk_score",
        value=round(_clamp(value), 4),
        confidence=0.5,  # geometry says little about soil — honesty in confidence
        positive_factors=pos,
        negative_factors=neg,
    )


def _heritage_score(site: SiteContext) -> SiteScore:
    env = site.environment
    if env is None or not env.heritage_checked:
        return SiteScore.not_computable("heritage_risk_score", "nid_source_unavailable")
    pos: list[str] = []
    neg: list[str] = []
    value = 1.0
    if env.heritage_detected:
        value -= 0.5
        neg.append(
            f"strefa/obiekt zabytkowy pokrywa {env.heritage_coverage_percent:.1f}% działki "
            "— uzgodnienia z WKZ"
        )
    else:
        pos.append("brak wpisów NID na działce (rejestr sprawdzony)")
        neg.append("GEZ gminna i strefy z MPZP wymagają osobnej weryfikacji")
    return SiteScore(
        name="heritage_risk_score",
        value=round(_clamp(value), 4),
        confidence=0.7,
        positive_factors=pos,
        negative_factors=neg,
    )


def _infrastructure_score(site: SiteContext) -> SiteScore:
    a = site.access
    if a is None or a.road_access is None or a.road_access.has_public_road_access is None:
        return SiteScore.not_computable("infrastructure_score", "roads_source_unavailable")
    pos: list[str] = []
    neg: list[str] = []
    value = 0.5
    ra = a.road_access
    if ra.has_public_road_access:
        value += 0.3
        pos.append(f"styk z drogą publiczną (front {ra.frontage_length_m or 0:.0f} m)")
        if a.zjazd.get("feasibility") == "mozliwy":
            value += 0.1
            pos.append("zjazd geometrycznie możliwy (decyzja: zarządca drogi)")
    else:
        value -= 0.4
        neg.append("brak bezpośredniego dostępu do drogi publicznej w próbkowanych danych")
    if a.utilities:
        # Review M2: distance 0.0 (network crossing the parcel) is the BEST case —
        # a falsy-zero ``or`` would push it past the near band; only None (unknown
        # distance) is excluded.
        near = [
            u
            for u in a.utilities
            if (u.distance_m if u.distance_m is not None else 1e9) <= 100.0
        ]
        types = sorted({u.network_type for u in near})
        if types:
            value += min(0.2, 0.05 * len(types))
            pos.append(f"sieci w zasięgu 100 m: {', '.join(types)}")
        if a.technical_zones:
            value -= 0.1
            neg.append(f"{len(a.technical_zones)} strefa/strefy techniczne na działce")
    else:
        neg.append("brak danych GESUT o sieciach (warunki przyłączenia nieznane)")
    return SiteScore(
        name="infrastructure_score",
        value=round(_clamp(value), 4),
        confidence=0.6,
        positive_factors=pos,
        negative_factors=neg,
    )


def _procedural_score(site: SiteContext, computed: dict[str, SiteScore]) -> SiteScore:
    """Procedural burden: each required procedure / open unknown lowers the score.

    Computable whenever ANY site module ran (procedures are counted from what we
    KNOW); confidence reflects how many themes could actually be checked.
    """
    ran = [
        m
        for m in (site.terrain, site.water, site.geology, site.environment, site.access)
        if m is not None
    ]
    if not ran:
        return SiteScore.not_computable("procedural_risk_score", "no_site_modules_ran")
    pos: list[str] = []
    neg: list[str] = []
    procedures = 0
    env = site.environment
    if env is not None and env.eia_screening.get("required") is True:
        procedures += 1
        neg.append("decyzja środowiskowa / KIP (screening OOŚ)")
    if env is not None and env.heritage_detected:
        procedures += 1
        neg.append("uzgodnienie z konserwatorem zabytków")
    water = site.water
    if water is not None and water.water_law_precheck.get("procedure_question"):
        procedures += 1
        neg.append("pozwolenie/zgłoszenie wodnoprawne do zweryfikowania")
    access = site.access
    if access is not None and access.zjazd.get("question"):
        procedures += 1
        neg.append("decyzja zjazdowa zarządcy drogi")
    geology = site.geology
    if geology is not None and geology.landslide_detected:
        procedures += 1
        neg.append("dokumentacja geologiczno-inżynierska (teren osuwiskowy)")
    unknown_count = len(site.all_unknowns())
    if unknown_count:
        neg.append(f"{unknown_count} otwartych niewiadomych podnosi ryzyko proceduralne")
    if procedures == 0:
        pos.append("brak zidentyfikowanych dodatkowych procedur ponad standard PnB")
    value = 1.0 - 0.15 * procedures - 0.03 * min(unknown_count, 10)
    checked = sum(1 for m in ran)
    return SiteScore(
        name="procedural_risk_score",
        value=round(_clamp(value), 4),
        confidence=round(0.3 + 0.08 * checked, 2),
        positive_factors=pos,
        negative_factors=neg,
    )


def _cost_driver_score(site: SiteContext) -> SiteScore:
    """Cost drivers: earthworks, network relocations, retention, access works."""
    drivers: list[str] = []
    pos: list[str] = []
    known_any = False
    value = 1.0
    t = site.terrain
    if t is not None and t.status == "ok":
        known_any = True
        cls = t.earthworks.get("parcel_class")
        if cls in ("wysoki", "bardzo_wysoki"):
            value -= 0.35
            drivers.append(f"roboty ziemne klasy '{cls}' (mury oporowe/niwelacja)")
        elif cls == "umiarkowany":
            value -= 0.15
            drivers.append("umiarkowane roboty ziemne")
        else:
            pos.append("płaski teren — niskie koszty robót ziemnych")
    a = site.access
    if a is not None and (a.utilities or a.technical_zones):
        known_any = True
        if a.technical_zones:
            value -= 0.1 * min(len(a.technical_zones), 3)
            drivers.append(
                f"kolizje ze strefami technicznymi sieci ({len(a.technical_zones)})"
            )
        far = [u for u in a.utilities if (u.distance_m or 0.0) > 100.0]
        if far:
            value -= 0.1
            drivers.append("przyłącza > 100 m (koszt uzbrojenia)")
        if not a.technical_zones and not far:
            pos.append("sieci blisko, bez kolizji — standardowe koszty przyłączy")
    w = site.water
    if w is not None and w.flood_checked:
        known_any = True
        if w.flood_detected:
            value -= 0.25
            drivers.append("zabezpieczenia przeciwpowodziowe / ograniczenia zabudowy")
        if w.retention.get("class") == "ograniczona":
            value -= 0.1
            drivers.append("retencja kubaturowa zamiast powierzchniowej")
    if not known_any:
        return SiteScore.not_computable("cost_driver_score", "no_cost_relevant_data")
    return SiteScore(
        name="cost_driver_score",
        value=round(_clamp(value), 4),
        confidence=0.5,  # cost classes are heuristic bands, not a kosztorys
        positive_factors=pos,
        negative_factors=drivers,
    )


def _planning_certainty_score(planning_block: dict[str, Any] | None) -> SiteScore:
    """Planning certainty from the Phase 8 stability trace (plan delta 2).

    Reads the ``acts`` (+ per-act ``stability`` from
    :func:`plot_planning.stability_score`) of the analysis planning block. No
    parsed acts → honestly unknown (coverage pending ≠ low certainty).
    """
    if not planning_block or planning_block.get("coverage_status") not in ("parsed", "computed"):
        return SiteScore.not_computable(
            "planning_certainty_score",
            "no_parsed_planning_acts_for_parcel",
        )
    acts = planning_block.get("acts") or []
    stabilities: list[float] = []
    pos: list[str] = []
    neg: list[str] = []
    for act in acts:
        stab = act.get("stability") or {}
        val = stab.get("score")
        if isinstance(val, int | float):
            stabilities.append(float(val))
            label = act.get("name") or act.get("id") or "akt"
            (pos if val >= 0.6 else neg).append(
                f"{label}: stabilność planistyczna {val:.2f}"
            )
    if not stabilities:
        return SiteScore.not_computable(
            "planning_certainty_score", "acts_without_stability_trace"
        )
    value = sum(stabilities) / len(stabilities)
    pos.append(f"{len(stabilities)} akt(y) planistyczne pokrywają działkę (APP/GML)")
    return SiteScore(
        name="planning_certainty_score",
        value=round(_clamp(value), 4),
        confidence=0.7,
        positive_factors=pos,
        negative_factors=neg,
    )


def _investment_fit_score(
    investment_goal: dict[str, Any] | None,
    envelope_area_m2: float | None,
    parcel_area_m2: float | None,
) -> SiteScore:
    """Fit of the investor's stated goal vs the buildable envelope (§14.1).

    Needs an explicit goal (type ≠ unknown AND a target) + a computed envelope;
    otherwise honestly unknown. The capacity proxy is envelope_area × a coarse
    achievable-intensity band (heuristic, marked — full chłonność lives in the
    Phase 9 engine and refines this per scenario).
    """
    goal = investment_goal or {}
    goal_type = goal.get("type", "unknown")
    target_gfa = goal.get("target_gfa_m2")
    if goal_type == "unknown" or not isinstance(target_gfa, int | float) or target_gfa <= 0:
        return SiteScore.not_computable(
            "investment_fit_score", "no_explicit_investment_goal"
        )
    if not envelope_area_m2 or envelope_area_m2 <= 0:
        return SiteScore.not_computable("investment_fit_score", "no_buildable_envelope")
    # Coarse achievable GFA: envelope × 4 storeys equivalent (heuristic band —
    # basis: industry_heuristic; the Phase 9 capacity engine is the real answer).
    achievable = envelope_area_m2 * 4.0
    ratio = achievable / float(target_gfa)
    pos: list[str] = []
    neg: list[str] = []
    if ratio >= 1.5:
        value = 1.0
        pos.append(
            f"cel {target_gfa:,.0f} m² GFA mieści się z zapasem w przybliżonej "
            f"chłonności ({achievable:,.0f} m²)"
        )
    elif ratio >= 1.0:
        value = 0.7
        pos.append("cel osiągalny, ale blisko górnej granicy przybliżonej chłonności")
    else:
        value = max(0.0, 0.5 * ratio)
        neg.append(
            f"cel {target_gfa:,.0f} m² przekracza przybliżoną chłonność "
            f"({achievable:,.0f} m²) — zweryfikować scenariuszami chłonności"
        )
    neg.append("przybliżenie 4 kondygnacji × envelope (industry_heuristic) — nie chłonność Phase 9")
    return SiteScore(
        name="investment_fit_score",
        value=round(_clamp(value), 4),
        confidence=0.4,
        positive_factors=pos,
        negative_factors=neg,
    )
