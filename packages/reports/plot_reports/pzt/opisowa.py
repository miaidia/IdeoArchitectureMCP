"""PZT część opisowa generator (Phase 15 Task 2 — §14 rozporządzenia o projekcie
budowlanym, Dz.U. 2020 poz. 1609, t.j. Dz.U. 2022 poz. 1679).

ONE structured model (:class:`PztOpisowa`) assembled from ALREADY-COMPUTED data
(stored masterplan variant + Phase 9 capacity zestawienie + Phase 12 site context
+ Phase 10 fire RuleChecks) — Markdown and JSON render FROM IT (the ReportModel
idiom; §31 "one model, all formats"). No recomputation, no network.

Honesty contract (§21 / plan §15.4 anti-patterns):

* the output is a DRAFT PACKAGE for a projektant — :data:`PZT_DISCLAIMER` is
  mandatory and prominent (test-enforced); it is NEVER a projekt budowlany;
* existing-state data (BDOT10k buildings, GESUT uzbrojenie) renders honest
  ``data_unavailable`` lines when the site context is absent — never invented;
* the zestawienie powierzchni numbers come EXACTLY from the Phase 9 capacity
  engine (``MasterplanMetrics.zestawienie`` stored with the variant) — this
  module only renders them (one computation, two renderings; test-enforced);
* fire RuleChecks become statements with evidence: pass → "zapewniono…",
  fail → "NIEZGODNOŚĆ: …" (never omitted), unknown → "wymaga ustalenia";
* the obszar-oddziaływania draft is a rule-based heuristic, always flagged
  ``requires_projektant`` (determining it is the projektant's statutory duty).

Citation note (the §0v2.2 ⚠ idiom): section citations reference §14 of the
regulation; the exact ust./pkt numbering of the consolidated text must be
verified against the ISAP PDF before any production claim — the per-section
``citation`` strings mark the intended anchor, not a verified quote.

This module depends only on ``plot_domain`` shapes passed in as data (Phase 3
§3.4 decoupling holds: no plot_planning / plot_rules / plot_connectors imports).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Legal anchor of the whole PZT package (plan §0v2.2).
PZT_CITATION_BASE = "Dz.U. 2020 poz. 1609 (t.j. Dz.U. 2022 poz. 1679)"

#: MANDATORY draft-not-PB disclaimer (plan §15.4 anti-pattern guard, test-enforced).
PZT_DISCLAIMER = (
    "UWAGA: To opracowanie jest SZKICEM ROBOCZYM (draft) pakietu PZT dla "
    "projektanta — NIE JEST projektem budowlanym w rozumieniu ustawy Prawo "
    "budowlane. Sporządzenie projektu budowlanego wymaga projektanta z "
    "uprawnieniami budowlanymi, aktualnej mapy do celów projektowych "
    "(geodeta uprawniony) oraz podpisu kwalifikowanego/zaufanego/osobistego."
)

SectionStatus = Literal["done", "missing", "requires_projektant"]

#: Fire-protection rule ids whose checks feed section 6 (Phase 10 validators).
FIRE_RULE_PREFIX = "PL-PPOZ-"


class PztSection(BaseModel):
    """One §14 section of the część opisowa (data-driven or honestly missing)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable section id (e.g. 'zestawienie_powierzchni').")
    title: str = Field(description="Section title (Polish, §14 wording).")
    citation: str = Field(description="Per-section legal anchor (Dz.U. + §14 pkt).")
    status: SectionStatus = Field(
        description="done (data-driven) | missing (data unavailable) | requires_projektant."
    )
    status_reason: str = Field(default="", description="Why the status is what it is.")
    lines: list[str] = Field(default_factory=list, description="Rendered content lines.")
    data: dict[str, Any] = Field(
        default_factory=dict, description="Structured section data (JSON rendering)."
    )


class PztOpisowa(BaseModel):
    """The PZT część opisowa model — MD and JSON render from THIS (Phase 15)."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    variant_id: str
    generated_at: str
    citation_base: str = PZT_CITATION_BASE
    disclaimer: str = PZT_DISCLAIMER
    sections: list[PztSection] = Field(default_factory=list)
    zestawienie: dict[str, Any] = Field(
        default_factory=dict,
        description="The Phase 9 capacity-engine zestawienie powierzchni (verbatim).",
    )


# --------------------------------------------------------------------------- #
# Helpers (pure formatting over data passed in)
# --------------------------------------------------------------------------- #
def _fmt(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{float(value):,.2f}".replace(",", " ")
    return str(value)


def _building_rows(variant: Any) -> list[dict[str, Any]]:
    rows = []
    for b in getattr(variant, "buildings", []):
        rows.append(
            {
                "name": str(getattr(b, "name", "?")),
                "status": str(getattr(b, "status", "?")),
                "floors_by_segment": list(getattr(b, "floors_by_segment", []) or []),
                "uses": list(getattr(b, "uses", []) or []),
                "storeys": len(getattr(b, "storeys", []) or []),
            }
        )
    return rows


def _sec_przedmiot(variant: Any) -> PztSection:
    totals = getattr(variant, "totals", {}) or {}
    rows = _building_rows(variant)
    planned = [r for r in rows if r["status"] == "projektowany"]
    heritage = [r for r in rows if r["status"] == "zabytek_do_remontu"]
    lines = [
        f"Przedmiotem zamierzenia budowlanego jest zespół zabudowy: "
        f"{totals.get('buildings', len(rows))} budynków "
        f"(projektowane: {len(planned)}, zabytki do remontu: {len(heritage)}), "
        f"wraz z układem drogowym wewnętrznym, parkingami i zielenią urządzoną.",
        f"Szacowana liczba mieszkań (estymacja heurystyczna): "
        f"{totals.get('mieszkania_estimate', 'brak danych')}.",
    ]
    return PztSection(
        id="przedmiot",
        title="Przedmiot zamierzenia budowlanego",
        citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 1",
        status="done",
        status_reason="wyprowadzone z zaakceptowanego wariantu masterplanu",
        lines=lines,
        data={"totals": dict(totals), "buildings": rows},
    )


def _sec_stan_istniejacy(variant: Any, site_context: dict[str, Any] | None) -> PztSection:
    """Existing state from the Phase 12 site context — honest when absent (§21)."""
    rows = _building_rows(variant)
    existing = [r for r in rows if r["status"] in ("istniejacy", "zabytek_do_remontu")]
    lines: list[str] = []
    data: dict[str, Any] = {"existing_on_parcel": existing}
    if existing:
        names = ", ".join(r["name"] for r in existing)
        lines.append(f"Obiekty istniejące na terenie (z wariantu): {names}.")
    if site_context is None:
        lines.append(
            "Zabudowa sąsiednia (BDOT10k): data_unavailable — brak kontekstu "
            "terenowego dla tej analizy (nie zmyślono stanu istniejącego, §21)."
        )
        lines.append(
            "Uzbrojenie terenu (GESUT): data_unavailable — brak kontekstu "
            "terenowego dla tej analizy."
        )
        return PztSection(
            id="stan_istniejacy",
            title="Istniejący stan zagospodarowania działki lub terenu",
            citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 2",
            status="missing",
            status_reason="brak zapisanego kontekstu terenowego (Phase 12) dla analizy",
            lines=lines,
            data=data,
        )
    neighbors = list(site_context.get("neighbors") or [])
    if neighbors:
        described = ", ".join(
            f"{n.get('name', 'budynek')} (h={n.get('height_m', '?')} m)" for n in neighbors[:10]
        )
        more = f" (+{len(neighbors) - 10} dalszych)" if len(neighbors) > 10 else ""
        lines.append(f"Zabudowa sąsiednia (BDOT10k): {len(neighbors)} obiektów — {described}{more}.")
    else:
        lines.append(
            "Zabudowa sąsiednia (BDOT10k): nie wykryto obiektów w buforze analizy "
            "(lub warstwa niedostępna — patrz unknowns analizy)."
        )
    access = site_context.get("access") or {}
    utilities = list(access.get("utilities") or [])
    if utilities:
        kinds = sorted({str(u.get("network_type", "?")) for u in utilities})
        lines.append(
            f"Uzbrojenie terenu (GESUT): sieci w zasięgu — {', '.join(kinds)} "
            f"({len(utilities)} obiektów sieciowych)."
        )
    else:
        lines.append(
            "Uzbrojenie terenu (GESUT): data_unavailable — warstwa uzbrojenia nie "
            "została pobrana/wykryta (nie zmyślono sieci, §21)."
        )
    data.update({"neighbors_count": len(neighbors), "utilities": utilities})
    return PztSection(
        id="stan_istniejacy",
        title="Istniejący stan zagospodarowania działki lub terenu",
        citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 2",
        status="done",
        status_reason="z zapisanego kontekstu terenowego (Phase 12)",
        lines=lines,
        data=data,
    )


def _sec_projektowane(variant: Any) -> PztSection:
    rows = _building_rows(variant)
    roads = list(getattr(variant, "roads", []) or [])
    parking = list(getattr(variant, "parking", []) or [])
    greenery = list(getattr(variant, "greenery", []) or [])
    playgrounds = list(getattr(variant, "playgrounds", []) or [])
    functions = sorted({str(r.get("function", "?")) for r in roads if isinstance(r, dict)})
    spaces = sum(int(p.get("spaces", 0) or 0) for p in parking if isinstance(p, dict))
    lines = [
        "Projektowane zagospodarowanie obejmuje:",
        f"- budynki: {len(rows)} ("
        + "; ".join(
            f"{r['name']}: {'/'.join(str(f) for f in r['floors_by_segment']) or '?'} kond., "
            f"{', '.join(r['uses']) or '?'}"
            for r in rows
        )
        + "),",
        f"- układ komunikacyjny: {len(roads)} odcinków dróg wewnętrznych "
        f"(funkcje: {', '.join(functions) or 'brak'}),",
        f"- parkingi: {len(parking)} elementów, łącznie {spaces} miejsc postojowych,",
        f"- zieleń urządzona (PBC): {len(greenery)} terenów; place zabaw: {len(playgrounds)}.",
    ]
    return PztSection(
        id="projektowane",
        title="Projektowane zagospodarowanie działki lub terenu",
        citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 3",
        status="done",
        status_reason="z wariantu masterplanu",
        lines=lines,
        data={
            "buildings": rows,
            "roads_functions": functions,
            "parking_spaces_total": spaces,
            "greenery_count": len(greenery),
            "playgrounds_count": len(playgrounds),
        },
    )


def _sec_zestawienie(zestawienie: dict[str, Any] | None) -> PztSection:
    """Zestawienie powierzchni — VERBATIM Phase 9 capacity-engine numbers.

    The numbers are NOT recomputed here (anti-pattern §15.4: one computation in
    ``plot_planning.capacity.masterplan_metrics``, this is the second rendering;
    a test asserts exact equality against the engine output).
    """
    if not zestawienie:
        return PztSection(
            id="zestawienie_powierzchni",
            title="Zestawienie powierzchni (zabudowa / drogi i utwardzenia / PBC / inne)",
            citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 4",
            status="missing",
            status_reason=(
                "wariant nie ma zapisanego bloku zestawienia z silnika chłonności "
                "(Phase 9) — liczby nie są przeliczane w raporcie (jedno obliczenie)"
            ),
            lines=["Brak bloku zestawienia powierzchni przy wariancie — data_unavailable."],
        )
    lines = [
        f"- powierzchnia działki/terenu: {_fmt(zestawienie.get('parcel_area_m2'))} m²",
        f"- powierzchnia zabudowy: {_fmt(zestawienie.get('zabudowa_m2'))} m²",
        f"- powierzchnia dróg i utwardzeń: {_fmt(zestawienie.get('drogi_i_utwardzenia_m2'))} m²",
        f"- powierzchnia biologicznie czynna (PBC): {_fmt(zestawienie.get('pbc_m2'))} m²",
        f"- powierzchnie inne (rezydualne): {_fmt(zestawienie.get('inne_m2'))} m²",
        f"Źródło liczb: silnik chłonności Phase 9 (jedno obliczenie); "
        f"{zestawienie.get('note', '')}",
    ]
    return PztSection(
        id="zestawienie_powierzchni",
        title="Zestawienie powierzchni (zabudowa / drogi i utwardzenia / PBC / inne)",
        citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 4",
        status="done",
        status_reason="liczby z silnika chłonności Phase 9 (single source of truth)",
        lines=lines,
        data=dict(zestawienie),
    )


def _sec_constraints(site_context: dict[str, Any] | None) -> PztSection:
    """Zabytki / wpływy górnicze / zagrożenia — from the Phase 12 site context."""
    if site_context is None:
        return PztSection(
            id="ochrona_i_zagrozenia",
            title="Ochrona konserwatorska, wpływy górnicze, zagrożenia",
            citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 5–7",
            status="missing",
            status_reason="brak zapisanego kontekstu terenowego (Phase 12) dla analizy",
            lines=[
                "Dane o ochronie konserwatorskiej, wpływach górniczych i zagrożeniach: "
                "data_unavailable — wymagana analiza pełna (full due diligence)."
            ],
        )
    env = site_context.get("environment") or {}
    geo = site_context.get("geology") or {}
    water = site_context.get("water") or {}
    lines: list[str] = []
    if env.get("heritage_checked"):
        detected = bool(env.get("heritage_detected"))
        lines.append(
            "Ochrona konserwatorska: "
            + (
                f"wykryto strefy/obiekty ochrony (pokrycie "
                f"{env.get('heritage_coverage_percent', '?')}% działki)."
                if detected
                else "nie wykryto stref ochrony konserwatorskiej w sprawdzonych warstwach."
            )
        )
    else:
        lines.append("Ochrona konserwatorska: warstwa nie została sprawdzona (unknown).")
    mining = geo.get("mining")
    if isinstance(mining, dict) and mining:
        lines.append(
            f"Wpływy eksploatacji górniczej: {mining.get('status', 'unknown')}"
            + (f" — {mining.get('note')}" if mining.get("note") else "")
        )
    else:
        lines.append(
            "Wpływy eksploatacji górniczej: brak danych źródłowych (unknown) — "
            "do potwierdzenia w OG/UG."
        )
    hazards: list[str] = []
    if water.get("flood_checked"):
        hazards.append(
            "powódź: wykryto strefy zagrożenia"
            if water.get("flood_detected")
            else "powódź: nie wykryto stref w sprawdzonych warstwach"
        )
    else:
        hazards.append("powódź: nie sprawdzono")
    if geo.get("landslide_checked"):
        hazards.append(
            "osuwiska: wykryto"
            if geo.get("landslide_detected")
            else "osuwiska: nie wykryto"
        )
    else:
        hazards.append("osuwiska: nie sprawdzono")
    lines.append("Istniejące i przewidywane zagrożenia: " + "; ".join(hazards) + ".")
    return PztSection(
        id="ochrona_i_zagrozenia",
        title="Ochrona konserwatorska, wpływy górnicze, zagrożenia",
        citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 5–7",
        status="done",
        status_reason="z zapisanego kontekstu terenowego (Phase 12)",
        lines=lines,
        data={"environment": env, "geology": geo, "water": water},
    )


def _sec_ppoz(fire_checks: list[dict[str, Any]]) -> PztSection:
    """Ochrona ppoż incl. drogi pożarowe — Phase 10 RuleChecks become statements.

    pass → "zapewniono…", fail → an explicit "NIEZGODNOŚĆ: …" (never omitted —
    plan §15.3 honesty test), unknown → "wymaga ustalenia"; each statement cites
    the rule id + Dz.U. source and carries the validator message as evidence.
    """
    if not fire_checks:
        return PztSection(
            id="ochrona_ppoz",
            title="Warunki ochrony przeciwpożarowej, w tym drogi pożarowe",
            citation=f"{PZT_CITATION_BASE}, §14 (warunki ochrony przeciwpożarowej)",
            status="missing",
            status_reason="brak wyników walidatorów ppoż (Phase 10) przy wariancie",
            lines=["Brak ocen reguł ppoż przy wariancie — data_unavailable."],
        )
    lines: list[str] = []
    has_fail = False
    for check in fire_checks:
        status = str(check.get("status", "unknown"))
        rule_id = str(check.get("rule_id", "?"))
        message = str(check.get("message", "")).strip()
        cite = check.get("source_reference") or "?"
        if status == "pass":
            lines.append(
                f"Zapewniono wymagania reguły {rule_id} ({cite}) — dowód: {message}"
            )
        elif status == "fail":
            has_fail = True
            lines.append(f"NIEZGODNOŚĆ: reguła {rule_id} ({cite}) — {message}")
        elif status == "not_applicable":
            lines.append(f"Nie dotyczy: reguła {rule_id} ({cite}) — {message}")
        else:
            lines.append(
                f"Wymaga ustalenia: reguła {rule_id} ({cite}) — status '{status}': {message}"
            )
    if has_fail:
        lines.append(
            "UWAGA: wariant zawiera niezgodności ppoż — pakiet PZT nie może być "
            "podstawą dalszych prac bez ich usunięcia."
        )
    return PztSection(
        id="ochrona_ppoz",
        title="Warunki ochrony przeciwpożarowej, w tym drogi pożarowe",
        citation=f"{PZT_CITATION_BASE}, §14 (warunki ochrony przeciwpożarowej)",
        status="done",
        status_reason="wyprowadzone z wyników walidatorów ppoż (Phase 10) z dowodami",
        lines=lines,
        data={"checks": fire_checks},
    )


def _sec_obszar_oddzialywania(variant: Any, all_checks: list[dict[str, Any]]) -> PztSection:
    """Obszar oddziaływania DRAFT — rule-based buffer heuristic, requires_projektant.

    Heuristic reading (documented, NOT a legal determination): the reach of the
    §12 setback band (4 m windowed walls) and the §13 przesłanianie reach
    (≈ building height) bound the area a projektant will analyse — the draft
    buffer is ``max(4 m, max building height estimate)``. Determining the obszar
    oddziaływania is the projektant's statutory duty (Prawo budowlane art. 20)
    — the section is ALWAYS ``requires_projektant``.
    """
    heights = []
    for b in getattr(variant, "buildings", []):
        metrics = getattr(b, "metrics", {}) or {}
        h = metrics.get("height_estimate_m")
        if isinstance(h, int | float):
            heights.append(float(h))
    buffer_m = max([4.0, *heights]) if heights else 4.0
    considered = sorted(
        {
            str(c.get("rule_id"))
            for c in all_checks
            if str(c.get("rule_id", "")).startswith(("PL-WT-12", "PL-WT-13"))
        }
    )
    lines = [
        f"Szkic obszaru oddziaływania (heurystyka reguł §12/§13 WT): bufor "
        f"~{buffer_m:.1f} m od projektowanych budynków (max wysokość szacowana / "
        f"pas odległości §12).",
        "Podstawa heurystyki: reguły " + (", ".join(considered) if considered else "—")
        + " (zasięg przesłaniania ≈ wysokość obiektu; pas §12 = 4 m).",
        "WYMAGA PROJEKTANTA: określenie obszaru oddziaływania obiektu jest "
        "obowiązkiem projektanta (art. 20 Prawa budowlanego) — niniejszy bufor "
        "jest wyłącznie szkicem roboczym.",
    ]
    return PztSection(
        id="obszar_oddzialywania",
        title="Informacja o obszarze oddziaływania obiektu (szkic)",
        citation=f"{PZT_CITATION_BASE}, §14 (informacja o obszarze oddziaływania obiektu)",
        status="requires_projektant",
        status_reason="określenie obszaru oddziaływania to ustawowy obowiązek projektanta",
        lines=lines,
        data={"draft_buffer_m": round(buffer_m, 1), "basis_rules": considered},
    )


def _sec_rejestr_zabytkow(variant: Any, site_context: dict[str, Any] | None) -> PztSection:
    rows = _building_rows(variant)
    heritage_buildings = [r["name"] for r in rows if r["status"] == "zabytek_do_remontu"]
    lines: list[str] = []
    if heritage_buildings:
        lines.append(
            "Obiekty oznaczone w wariancie jako zabytki do remontu: "
            + ", ".join(heritage_buildings)
            + "."
        )
    env = (site_context or {}).get("environment") or {}
    if site_context is not None and env.get("heritage_checked"):
        lines.append(
            "Warstwy ochrony konserwatorskiej sprawdzone: "
            + (
                "wykryto obszary/obiekty chronione — formalny wpis do rejestru "
                "zabytków wymaga potwierdzenia u WKZ."
                if env.get("heritage_detected")
                else "nie wykryto wpisów w sprawdzonych warstwach; potwierdzenie "
                "u WKZ zalecane."
            )
        )
        status: SectionStatus = "done"
        reason = "z warstw konserwatorskich kontekstu terenowego (Phase 12)"
    else:
        lines.append(
            "Dane o wpisie do rejestru zabytków: data_unavailable — wymagane "
            "zapytanie do WKZ / gminnej ewidencji zabytków."
        )
        status = "missing"
        reason = "brak sprawdzonych warstw konserwatorskich dla analizy"
    return PztSection(
        id="rejestr_zabytkow",
        title="Informacja o wpisie do rejestru zabytków / gminnej ewidencji zabytków",
        citation=f"{PZT_CITATION_BASE}, §14 ust. 1 pkt 5",
        status=status,
        status_reason=reason,
        lines=lines,
        data={"heritage_buildings": heritage_buildings, "environment": env},
    )


# --------------------------------------------------------------------------- #
# Builder + renderers (one model → MD + JSON)
# --------------------------------------------------------------------------- #
def build_pzt_opisowa(
    *,
    analysis_id: str,
    variant: Any,
    generated_at: str,
    zestawienie: dict[str, Any] | None = None,
    site_context: dict[str, Any] | None = None,
    inter_building_checks: list[dict[str, Any]] | None = None,
) -> PztOpisowa:
    """Assemble the §14 część opisowa from STORED data (no recomputation).

    ``zestawienie`` is the capacity-engine block stored with the variant
    (``variant.metadata['zestawienie_powierzchni']``); ``site_context`` is the
    serialized Phase 12 ``SiteContext.to_dict()`` (or ``None`` — honest
    ``data_unavailable``); ``inter_building_checks`` are the Phase 10 RuleCheck
    dumps stored with the variant (fire rules feed section 6).
    """
    checks = list(inter_building_checks or [])
    fire_checks = [c for c in checks if str(c.get("rule_id", "")).startswith(FIRE_RULE_PREFIX)]
    sections = [
        _sec_przedmiot(variant),
        _sec_stan_istniejacy(variant, site_context),
        _sec_projektowane(variant),
        _sec_zestawienie(zestawienie),
        _sec_constraints(site_context),
        _sec_ppoz(fire_checks),
        _sec_obszar_oddzialywania(variant, checks),
        _sec_rejestr_zabytkow(variant, site_context),
    ]
    return PztOpisowa(
        analysis_id=analysis_id,
        variant_id=str(getattr(variant, "id", "?")),
        generated_at=generated_at,
        sections=sections,
        zestawienie=dict(zestawienie or {}),
    )


def render_pzt_opisowa_markdown(opisowa: PztOpisowa) -> str:
    """Markdown rendering of the część opisowa (disclaimer FIRST — prominent)."""
    L: list[str] = [
        f"# Projekt zagospodarowania terenu — CZĘŚĆ OPISOWA (szkic) — analiza "
        f"{opisowa.analysis_id}",
        "",
        f"**{opisowa.disclaimer}**",
        "",
        f"- podstawa układu: {opisowa.citation_base}, §14",
        f"- wariant masterplanu: `{opisowa.variant_id}`",
        f"- data wygenerowania: {opisowa.generated_at}",
        "",
    ]
    status_label = {
        "done": "dane",
        "missing": "BRAK DANYCH",
        "requires_projektant": "WYMAGA PROJEKTANTA",
    }
    for n, sec in enumerate(opisowa.sections, start=1):
        L.append(f"## {n}. {sec.title}")
        L.append(f"_{sec.citation}_ — status: **{status_label[sec.status]}** "
                 f"({sec.status_reason})")
        L.append("")
        for line in sec.lines:
            text = " ".join(str(line).split())  # injection guard (formats.py F2 idiom)
            L.append(text if text.startswith("- ") else f"- {text}")
        L.append("")
    L.append(f"_{opisowa.disclaimer}_")
    return "\n".join(L) + "\n"


def render_pzt_opisowa_json(opisowa: PztOpisowa) -> dict[str, Any]:
    """The opisowa model dump — the numbers every other rendering must agree with."""
    return opisowa.model_dump(mode="json")
