"""Format renderers over the unified :class:`~plot_reports.model.ReportModel`.

ONE model → four formats (§31 DoD: "PDF/HTML są renderowane z tego samego modelu
raportu; dane liczbowe są spójne z JSON-em"):

* :func:`render_model_markdown` — byte-compatible with the pre-Phase-14 reports
  for the default ``architect`` audience (the §22 screening template from
  ``report.py`` and the nine-section koncepcja template from ``koncepcja.py``);
  other audiences render a different SECTION LIST over the same numbers.
* :func:`render_model_html` — deterministic, single-file HTML with NO external
  assets (inline CSS only). The koncepcja variant mirrors the professional
  chłonność deliverable: title block (inwestor/data), plan-render reference with
  the 4-status legend note, per-stage table (Liczba mieszkań / PUM / PUU / PU +
  SUMA), per-building callouts, assumptions & disclaimers, questions-for-gmina
  annex (v2 plan PHASE 14 item 1).
* :func:`render_model_json` — the model dump (the numbers every other format
  must agree with).
* :func:`render_model_pdf` — weasyprint over the SAME HTML. weasyprint needs the
  system pango/cairo libraries; the import is GUARDED — when they are absent
  (e.g. this WSL2 host has no libpango and no root), :func:`pdf_available`
  reports the reason and :func:`render_model_pdf` raises
  :class:`PdfUnavailableError`. The MCP layer surfaces ``pdf_unavailable``
  honestly instead of faking a PDF (Phase 14 contract).

Number formatting helpers are SHARED between Markdown and HTML so formatted
numbers are byte-equal across formats (the §31 consistency test).

Markdown-injection guard (review fix F2, inherited from koncepcja.py): all
model-controlled free text is whitespace-collapsed (and pipe-escaped in table
cells) in Markdown, and HTML-escaped in HTML.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from plot_reports.model import DISCLAIMER_TOOL, ReportModel


# --------------------------------------------------------------------------- #
# Shared formatting helpers (single source of formatted numbers — §31)
# --------------------------------------------------------------------------- #
def fmt_m2_pl(value: Any) -> str:
    """Koncepcja-family number format: space thousands separator, no unit."""
    if isinstance(value, int | float):
        return f"{float(value):,.0f}".replace(",", " ")
    return "?"


def fmt_m2_screening(value: float | None) -> str:
    """Screening-family (§22) area format: comma thousands + unit."""
    return "brak danych" if value is None else f"{value:,.0f} m²"


def _decision_label(decision: str) -> str:
    return {
        "OK": "OK — brak istotnych przeszkód na poziomie screeningu",
        "OK_WITH_RISKS": "OK_WITH_RISKS — możliwe do rozważenia, z ryzykami",
        "NEEDS_MANUAL_REVIEW": "NEEDS_MANUAL_REVIEW — wymaga weryfikacji eksperckiej",
        "LIKELY_BLOCKED": "LIKELY_BLOCKED — prawdopodobnie zablokowane",
    }.get(decision, decision)


def _inline(text: Any) -> str:
    """Collapse ALL whitespace runs to single spaces (Markdown-injection guard F2)."""
    return " ".join(str(text).split())


def _cell(text: Any) -> str:
    """Table-cell-safe text: inline-collapsed AND pipe-escaped (review fix F2)."""
    return _inline(text).replace("|", "\\|")


def _esc(text: Any) -> str:
    """HTML-escaped, whitespace-collapsed text (HTML-injection guard)."""
    return html.escape(_inline(text))


# =========================================================================== #
# Markdown — section emitters (model → lines). The architect output is byte-
# compatible with the pre-Phase-14 report.py / koncepcja.py renderers.
# =========================================================================== #
# --- screening (§22) sections --------------------------------------------- #
def _md_scr_decision(m: ReportModel, L: list[str]) -> None:
    L.append("## Decyzja screeningowa")
    L.append(_decision_label(m.decision or "?"))
    L.append(f"(status analizy: {m.status})")
    L.append("")


def _md_scr_conclusions(m: ReportModel, L: list[str]) -> None:
    h = m.headline
    L.append("## Najważniejsze wnioski")
    L.append(
        f"- Powierzchnia działki: {fmt_m2_screening(h['parcel_area_m2'])}; "
        f"obszar zabudowy (buildable envelope): {fmt_m2_screening(h['buildable_area_m2'])}"
        + (
            f" ({h['buildable_percent']:.1f}% działki)"
            if h.get("buildable_percent") is not None
            else ""
        )
    )
    L.append(
        f"- Wykryte ograniczenia: {h['constraint_count']}; czerwone flagi: {h['risk_count']}."
    )
    L.append(f"- Pozycje niepewne (unknowns): {h['unknown_count']}.")
    L.append("")


def _md_scr_red_flags(m: ReportModel, L: list[str]) -> None:
    L.append("## Czerwone flagi")
    if m.risks:
        for r in m.risks:
            L.append(
                f"- [{r['severity']}/{r['risk_type']}] {r['summary']} "
                f"(status: {r['status']}, pewność: {r['confidence']}"
                + (
                    f", źródło: {r['source_id']}"
                    if r.get("source_id")
                    else ", źródło: no_source"
                )
                + ")"
            )
    else:
        L.append("- Brak czerwonych flag na poziomie screeningu.")
    L.append("")


def _md_scr_design_opportunities(m: ReportModel, L: list[str]) -> None:
    L.append("## Co można rozważać projektowo")
    env = m.envelope
    if env.get("present") and (env.get("area_m2") or 0.0) > 0.0:
        L.append(
            f"- Realny obszar pod zabudowę: {fmt_m2_screening(env['area_m2'])} "
            f"(pewność envelope: {env['confidence']:.2f})."
        )
        if env.get("has_lir"):
            L.append("- Wyznaczono największy prostokąt wpisany (orientacyjny obrys budynku).")
    else:
        L.append("- Po odsunięciach i strefach wyłączonych brak istotnego obszaru pod zabudowę.")
    L.append("")


def _md_scr_confirmations(m: ReportModel, L: list[str]) -> None:
    L.append("## Co wymaga potwierdzenia")
    if m.unknowns:
        for u in m.unknowns:
            L.append(f"- [{u['severity']}] {u['topic']} — powód: {u['reason']}.")
    else:
        L.append("- Brak otwartych pozycji niepewnych.")
    L.append("")


def _md_scr_parcel_params(m: ReportModel, L: list[str]) -> None:
    L.append("## Parametry działki")
    L.append(f"- powierzchnia: {fmt_m2_screening(m.headline['parcel_area_m2'])}")
    metrics = m.geometry_metrics
    if metrics:
        L.append(f"- obwód: {metrics.get('perimeter_m', 'brak danych')} m")
        L.append(
            f"- kształt: zwartość {metrics.get('compactness', '?')}, "
            f"nieregularność {metrics.get('irregularity', '?')}"
        )
        L.append(f"- oś główna: {metrics.get('main_axis_length_m', '?')} m")
    L.append("")


def _md_scr_planning(m: ReportModel, L: list[str]) -> None:
    L.append("## Planowanie")
    L.append(f"- MPZP/POG/WZ: {m.planning_summary.get('mpzp_pog_wz', 'brak danych')}")
    L.append(
        f"- pokrycie planistyczne: {m.planning_summary.get('coverage_status', 'brak danych')}"
    )
    L.append(f"- gmina: {m.planning_summary.get('municipality') or 'brak danych'}")
    L.append("")


def _md_scr_envelope(m: ReportModel, L: list[str]) -> None:
    L.append("## Buildable envelope")
    L.append(
        f"- powierzchnia potencjalna: {fmt_m2_screening(m.headline['buildable_area_m2'])}"
    )
    main_constraints = ", ".join(m.constraint_types) or "brak"
    L.append(f"- główne ograniczenia: {main_constraints}")
    for tr in m.envelope.get("removed_by", []):
        L.append(
            f"  - {tr.get('label')}: −{tr.get('removed_m2'):,.0f} m² "
            f"({tr.get('removed_percent')}%)"
        )
    L.append("")


def _md_scr_media_access(m: ReportModel, L: list[str]) -> None:
    L.append("## Media i dojazd")
    L.append(f"- media (uzbrojenie): {m.layer_status.get('utilities', 'nie sprawdzono')}")
    L.append(f"- dojazd / drogi: {m.layer_status.get('roads', 'nie sprawdzono')}")
    L.append("")


def _md_scr_environment(m: ReportModel, L: list[str]) -> None:
    L.append("## Środowisko, wody, geologia, zabytki")
    L.append(f"- powódź: {m.layer_status.get('flood', 'nie sprawdzono')}")
    L.append(f"- ochrona przyrody: {m.layer_status.get('protected', 'nie sprawdzono')}")
    L.append(f"- osuwiska / geologia: {m.layer_status.get('landslide', 'nie sprawdzono')}")
    L.append(f"- zabytki: {m.layer_status.get('heritage', 'nie sprawdzono')}")
    L.append(f"- cieki wodne: {m.layer_status.get('watercourses', 'nie sprawdzono')}")
    L.append(f"- las: {m.layer_status.get('forest', 'nie sprawdzono')}")
    L.append("")


def _md_scr_capacity(m: ReportModel, L: list[str]) -> None:
    L.append("## Chłonność")
    L.append("- wariant konserwatywny: nie obliczono (chłonność wchodzi w Fazie 9)")
    L.append("- wariant bazowy: nie obliczono (Faza 9)")
    L.append("- wariant optymistyczny: nie obliczono (Faza 9)")
    L.append("")


def _md_scr_next_steps(m: ReportModel, L: list[str]) -> None:
    L.append("## Następne kroki")
    if m.next_actions:
        for a in m.next_actions:
            who = f" → {a['addressed_to']}" if a.get("addressed_to") else ""
            L.append(f"- [{a['priority']}] {a['title']}: {a['detail']}{who}")
    else:
        L.append("- Brak rekomendacji.")
    L.append("")


def _md_scr_sources(m: ReportModel, L: list[str]) -> None:
    L.append("## Źródła i confidence")
    if m.sources:
        for s in m.sources:
            L.append(
                f"- {s.get('publisher')} ({s.get('source_id')}): "
                f"legal_status={s.get('legal_status')}, "
                f"pobrano={s.get('retrieved_at')}, confidence={s.get('confidence')}"
            )
    else:
        L.append("- Brak źródeł (no_source) — wynik częściowy.")
    L.append(f"- Liczba pozycji evidence: {m.evidence_count}.")
    L.append("")


def _md_scr_executive(m: ReportModel, L: list[str]) -> None:
    L.append("## Synteza (executive summary)")
    for line in m.executive_summary:
        L.append(f"- {_inline(line)}")
    L.append("")


_SCREENING_MD_SECTIONS: dict[str, Callable[[ReportModel, list[str]], None]] = {
    "executive_summary": _md_scr_executive,
    "decision": _md_scr_decision,
    "conclusions": _md_scr_conclusions,
    "red_flags": _md_scr_red_flags,
    "design_opportunities": _md_scr_design_opportunities,
    "confirmations": _md_scr_confirmations,
    "parcel_params": _md_scr_parcel_params,
    "planning": _md_scr_planning,
    "envelope": _md_scr_envelope,
    "media_access": _md_scr_media_access,
    "environment": _md_scr_environment,
    "capacity": _md_scr_capacity,
    "next_steps": _md_scr_next_steps,
    "sources": _md_scr_sources,
}


# --- koncepcja sections (numbered "## n. <title>" like the Phase 11 report) -- #
def _brief_summary_lines(brief: dict[str, Any] | None) -> list[str]:
    if not brief:
        return [
            "- brak wygenerowanego design briefu dla tej analizy "
            "(zasób `analysis://{id}/design-brief`)"
        ]
    lines: list[str] = []
    parcel = brief.get("parcel") or {}
    axes = brief.get("axes") or {}
    if parcel:
        lines.append(
            f"- działka: {fmt_m2_pl(parcel.get('area_m2'))} m², kształt "
            f"**{parcel.get('shape_class', '?')}**, śródmiejska: "
            f"{'tak' if parcel.get('srodmiejska') else 'nie'}"
        )
    if axes:
        lines.append(
            f"- oś kompozycyjna: azymut {axes.get('dominant_azimuth_deg', '?')}° "
            f"({axes.get('method', '?')})"
        )
    heritage = brief.get("heritage") or []
    if heritage:
        # Heritage names arrive from user/model-supplied footprints — neutralized.
        names = ", ".join(_inline(h.get("name") or "obiekt") for h in heritage)
        lines.append(f"- zabytki do zachowania: {names}")
    typologies = brief.get("typologies") or []
    if typologies:
        top = typologies[0]
        lines.append(
            f"- rekomendowana typologia (sugestia, nie walidator): "
            f"**{top.get('title', '?')}**"
        )
    missing = brief.get("missing_indicators") or []
    if missing:
        lines.append(f"- wskaźniki nieznane (do ustalenia): {missing}")
    return lines or ["- brief bez danych"]


def _md_kon_brief(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f"## {n}. Założenia z design briefu")
    L.extend(_brief_summary_lines(m.brief))
    L.append("")


def _md_kon_headline(m: ReportModel, L: list[str], n: int) -> None:
    totals = m.totals
    L.append(f"## {n}. Chłonność — liczby kluczowe")
    L.append(
        f"- budynki: {totals.get('buildings', '?')}; mieszkania (estymacja): "
        f"**{totals.get('mieszkania_estimate', '?')}**"
    )
    L.append(
        f"- PUM: **{fmt_m2_pl(totals.get('pum_m2'))} m²**; PUU: "
        f"{fmt_m2_pl(totals.get('puu_m2'))} m²; PU: {fmt_m2_pl(totals.get('pu_m2'))} m²"
    )
    L.append(
        f"- powierzchnia zabudowy: {fmt_m2_pl(totals.get('powierzchnia_zabudowy_m2'))} m² "
        f"(coverage {totals.get('coverage_ratio', '?')}); intensywność "
        f"{totals.get('intensywnosc', '?')}"
    )
    if m.capacity:
        # The capacity gap vs the base scenario is ALWAYS stated (plan §11.4 —
        # a shortfall must never be hidden). Inline-collapsed for line safety.
        L.append(f"- {_inline(m.capacity.get('message', ''))}")
    L.append("")


def _md_kon_buildings(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f"## {n}. Zestawienie budynków")
    L.append(
        "| Budynek | Kondygnacje | Funkcje | PZ [m²] | PUM [m²] | PUU [m²] | Mieszkania | Etap | Status |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|")
    for b in m.buildings:
        bm = b.get("metrics") or {}
        floors = "/".join(str(f) for f in b.get("floors_by_segment", [])) or "?"
        uses = ", ".join(b.get("uses", [])) or "?"
        stage = b["stage"] if b.get("stage") is not None else "—"
        # Building name (free DSL text) is pipe-escaped + collapsed so it can never
        # add table columns/rows; uses/status are Literal-constrained but get the
        # same cell treatment for defence in depth (review fix F2).
        L.append(
            f"| {_cell(b.get('name'))} | {floors} | {_cell(uses)} "
            f"| {fmt_m2_pl(bm.get('powierzchnia_zabudowy_m2'))} "
            f"| {fmt_m2_pl(bm.get('pum_m2'))} | {fmt_m2_pl(bm.get('puu_m2'))} "
            f"| {bm.get('mieszkania_estimate', '?')} | {stage} | {_cell(b.get('status'))} |"
        )
    L.append("")


def _md_kon_stages(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f"## {n}. Zestawienie etapów (Liczba mieszkań / PUM / PUU / PU)")
    L.append("| Etap | Liczba mieszkań | PUM [m²] | PUU [m²] | PU [m²] |")
    L.append("|---|---|---|---|---|")
    for r in m.stage_table:
        etap = r.get("etap", "?")
        label = f"**{etap}**" if etap == "SUMA" else str(etap)
        L.append(
            f"| {label} | {r.get('liczba_mieszkan', '?')} | {fmt_m2_pl(r.get('pum_m2'))} "
            f"| {fmt_m2_pl(r.get('puu_m2'))} | {fmt_m2_pl(r.get('pu_m2'))} |"
        )
    L.append("")


def _md_kon_compliance(m: ReportModel, L: list[str], n: int) -> None:
    summary = m.compliance
    L.append(f"## {n}. Zgodność WT/ppoż (walidatory między-budynkowe)")
    L.append(
        f"- wyniki reguł: pass: {summary['pass']}, fail: {summary['fail']}, "
        f"warning: {summary['warning']}, unknown: {summary['unknown']}, "
        f"not_applicable: {summary['not_applicable']}"
    )
    if summary["failing_rule_ids"]:
        L.append(f"- reguły naruszone: {summary['failing_rule_ids']}")
    else:
        L.append("- brak naruszonych reguł twardych (zero hard violations)")
    L.append(
        "- pełny ślad reguł (trace + geometria dowodowa): zasób "
        f"`analysis://{m.analysis_id}/masterplan/{m.variant_id}/metrics.json`"
    )
    L.append("")


def _md_kon_staging(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f"## {n}. Etapowanie — kontrole spójności (soft)")
    if m.staging:
        not_pass = [s for s in m.staging if str(s.get("status")) not in ("pass",)]
        L.append(f"- kontrole: {len(m.staging)}; poza statusem pass: {len(not_pass)}")
        for s in m.staging:
            # Server-generated messages may interpolate model-controlled building
            # names — inline-collapsed so a crafted name can't break the line (F2).
            L.append(f"- [{_inline(s.get('status'))}] {_inline(s.get('message', ''))}")
    else:
        L.append("- brak zadeklarowanych etapów (etapowanie nieobjęte kontrolą)")
    L.append("")


def _md_kon_rationale(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f'## {n}. Uzasadnienie projektowe ("dlaczego tak")')
    L.append(
        "_Zapis rozumowania modelu-architekta dla każdej iteracji (chronologicznie). "
        "Tekst dokumentacyjny — nigdy nie był wejściem walidacji (NFR-SEC-003)._"
    )
    entries = [e for e in m.rationales if e.get("rationale")]
    if entries:
        for i, entry in enumerate(entries, start=1):
            stamp = entry.get("timestamp", "?")
            verdict = (
                "zaakceptowana"
                if entry.get("accepted")
                else ("odrzucona" if entry.get("valid") is False else "oceniona")
            )
            # Markdown-injection guard (review fix F2): the model-controlled
            # rationale is whitespace-collapsed to ONE line and rendered inside a
            # blockquote — it can never open a heading/table/section of its own.
            L.append(f"{i}. _{_inline(stamp)}_ ({verdict}):")
            L.append(f"   > {_inline(entry['rationale'])}")
    else:
        L.append("- brak zapisanych uzasadnień iteracji")
    L.append("")


def _md_kon_unknowns(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f"## {n}. Pozycje niepewne i pytania do gminy (aneks)")
    if m.unknowns:
        for u in m.unknowns:
            L.append(
                f"- [{u.get('severity', '?')}] {u.get('topic', '?')} — powód: "
                f"{u.get('reason', '?')}"
            )
        L.append("")
        L.append("Pytania do gminy / urzędu:")
        for u in m.unknowns:
            action = u.get("suggested_action")
            if action:
                L.append(f"- {action}")
    else:
        L.append("- brak otwartych pozycji niepewnych")
    if m.missing_indicators:
        L.append(
            f"- wskaźniki planistyczne do potwierdzenia w MPZP/WZ: {m.missing_indicators} "
            "(nigdy wartości domyślne, §0v2.4)"
        )
    L.append("")


def _md_kon_disclaimers(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f"## {n}. Założenia i zastrzeżenia")
    config_basis = m.config_basis
    if config_basis:
        L.append(
            f"- PUM/PUU/mieszkania: estymacja heurystyczna (basis: "
            f"`{config_basis.get('basis', 'industry_heuristic')}`, pum_efficiency="
            f"{config_basis.get('pum_efficiency', '?')}, avg_mieszkanie_m2="
            f"{config_basis.get('avg_mieszkanie_m2', '?')})."
        )
        if config_basis.get("measurement_standard"):
            L.append(f"- norma odniesienia: {config_basis['measurement_standard']}")
    L.append(
        "- pewność danych: wyniki zależą od kompletności wskaźników MPZP/WZ i danych "
        "źródłowych; pozycje `unknown` pozostają niepewne do potwierdzenia."
    )
    L.append(DISCLAIMER_TOOL)


def _md_kon_executive(m: ReportModel, L: list[str], n: int) -> None:
    L.append(f"## {n}. Synteza (executive summary)")
    for line in m.executive_summary:
        L.append(f"- {_inline(line)}")
    L.append("")


_KONCEPCJA_MD_SECTIONS: dict[str, Callable[[ReportModel, list[str], int], None]] = {
    "executive_summary": _md_kon_executive,
    "brief": _md_kon_brief,
    "headline": _md_kon_headline,
    "buildings": _md_kon_buildings,
    "stages": _md_kon_stages,
    "compliance": _md_kon_compliance,
    "staging": _md_kon_staging,
    "rationale": _md_kon_rationale,
    "unknowns": _md_kon_unknowns,
    "disclaimers": _md_kon_disclaimers,
}


def render_model_markdown(model: ReportModel) -> str:
    """Render Markdown from the unified model (architect output is byte-compatible
    with the pre-Phase-14 ``render_markdown`` / ``render_koncepcja_markdown``)."""
    L: list[str] = []
    if model.kind == "koncepcja":
        L.append(f"# Koncepcja zagospodarowania (chłonność) — analiza {model.analysis_id}")
        L.append("")
        L.append(f"- data wygenerowania: {model.generated_at}")
        L.append(f"- wariant masterplanu: `{model.variant_id}`")
        if model.ruleset_version:
            L.append(f"- wersja rulesetów: `{model.ruleset_version}`")
        if model.plan_png_resource:
            L.append(f"- rysunek planu zagospodarowania: `{model.plan_png_resource}`")
        L.append("")
        for n, key in enumerate(model.sections, start=1):
            _KONCEPCJA_MD_SECTIONS[key](model, L, n)
        return "\n".join(L) + "\n"

    # screening (§22)
    L.append(f"# Analiza działki: {model.parcel_display_id}")
    L.append("")
    for key in model.sections:
        _SCREENING_MD_SECTIONS[key](model, L)
    L.append(DISCLAIMER_TOOL)
    return "\n".join(L) + "\n"


# =========================================================================== #
# JSON — the model dump (the numbers every other format must agree with)
# =========================================================================== #
def render_model_json(model: ReportModel) -> dict[str, Any]:
    """The report model as a JSON-serialisable dict (numbers source of truth)."""
    return model.model_dump(mode="json")


# =========================================================================== #
# HTML — deterministic single-file template, no external assets
# =========================================================================== #
_HTML_CSS = """
body { font-family: 'DejaVu Sans', sans-serif; margin: 2em; color: #1a1a1a; }
h1 { font-size: 1.5em; border-bottom: 2px solid #333; padding-bottom: .3em; }
h2 { font-size: 1.15em; margin-top: 1.4em; }
table { border-collapse: collapse; margin: .6em 0; }
th, td { border: 1px solid #888; padding: .25em .6em; font-size: .9em; }
th { background: #eee; }
.title-block { border: 1px solid #333; padding: .8em 1em; margin-bottom: 1em; }
.title-block .meta { color: #444; font-size: .9em; }
.legend { font-size: .85em; color: #444; }
.disclaimer { font-style: italic; color: #555; }
blockquote { margin: .2em 0 .2em 1.5em; color: #333; border-left: 3px solid #bbb;
             padding-left: .6em; }
.suma td { font-weight: bold; }
""".strip()

#: The 4 status hatches of the plan legend (mirrors the renderer-v2 legend —
#: presentation semantics only; the render PNG itself carries the drawn legend).
_PLAN_LEGEND_NOTE = (
    "Legenda planu (4 statusy): istniejące / zrealizowane / w budowie / "
    "projektowane; zabytek do remontu = istniejące z wyróżnioną krawędzią."
)


def _html_list(items: list[str]) -> list[str]:
    out = ["<ul>"]
    out.extend(f"<li>{item}</li>" for item in items)
    out.append("</ul>")
    return out


def _html_title_block(m: ReportModel) -> list[str]:
    """The chłonność deliverable title block (inwestor / data / wariant)."""
    investor = m.title_block.get("investor")
    rows = [
        ("Inwestor", _esc(investor) if investor else "— (nie podano)"),
        ("Data", _esc(m.generated_at) if m.generated_at else "—"),
        ("Analiza", _esc(m.analysis_id)),
    ]
    if m.variant_id:
        rows.append(("Wariant masterplanu", _esc(m.variant_id)))
    if m.ruleset_version:
        rows.append(("Wersja rulesetów", _esc(m.ruleset_version)))
    rows.append(("Wersja modelu raportu", _esc(m.report_version)))
    if m.analysis_snapshot_hash:
        rows.append(("Snapshot (sha256)", _esc(m.analysis_snapshot_hash[:16]) + "…"))
    out = ['<div class="title-block">']
    title = (
        "Koncepcja zagospodarowania (chłonność)"
        if m.kind == "koncepcja"
        else "Analiza działki (screening)"
    )
    out.append(f"<h1>{title}</h1>")
    out.append('<table class="meta">')
    for label, value in rows:
        out.append(f"<tr><th>{label}</th><td>{value}</td></tr>")
    out.append("</table>")
    if m.executive_summary:
        out.append("<p><strong>Synteza:</strong></p>")
        out.extend(_html_list([_esc(s) for s in m.executive_summary]))
    out.append("</div>")
    return out


# --- koncepcja HTML sections (same numbers as MD via shared fmt helpers) ---- #
def _html_kon_brief(m: ReportModel, n: int) -> list[str]:
    items = [_esc(ln.lstrip("- ")) for ln in _brief_summary_lines(m.brief)]
    return [f"<h2>{n}. Założenia z design briefu</h2>", *_html_list(items)]


def _html_kon_headline(m: ReportModel, n: int) -> list[str]:
    t = m.totals
    items = [
        f"budynki: {_esc(t.get('buildings', '?'))}; mieszkania (estymacja): "
        f"<strong>{_esc(t.get('mieszkania_estimate', '?'))}</strong>",
        f"PUM: <strong>{fmt_m2_pl(t.get('pum_m2'))} m²</strong>; PUU: "
        f"{fmt_m2_pl(t.get('puu_m2'))} m²; PU: {fmt_m2_pl(t.get('pu_m2'))} m²",
        f"powierzchnia zabudowy: {fmt_m2_pl(t.get('powierzchnia_zabudowy_m2'))} m² "
        f"(coverage {_esc(t.get('coverage_ratio', '?'))}); intensywność "
        f"{_esc(t.get('intensywnosc', '?'))}",
    ]
    if m.capacity:
        items.append(_esc(m.capacity.get("message", "")))
    return [f"<h2>{n}. Chłonność — liczby kluczowe</h2>", *_html_list(items)]


def _html_kon_buildings(m: ReportModel, n: int) -> list[str]:
    out = [f"<h2>{n}. Zestawienie budynków</h2>", "<table>"]
    out.append(
        "<tr><th>Budynek</th><th>Kondygnacje</th><th>Funkcje</th><th>PZ [m²]</th>"
        "<th>PUM [m²]</th><th>PUU [m²]</th><th>Mieszkania</th><th>Etap</th>"
        "<th>Status</th></tr>"
    )
    for b in m.buildings:
        bm = b.get("metrics") or {}
        floors = "/".join(str(f) for f in b.get("floors_by_segment", [])) or "?"
        uses = ", ".join(b.get("uses", [])) or "?"
        stage = b["stage"] if b.get("stage") is not None else "—"
        out.append(
            f"<tr><td>{_esc(b.get('name'))}</td><td>{_esc(floors)}</td>"
            f"<td>{_esc(uses)}</td><td>{fmt_m2_pl(bm.get('powierzchnia_zabudowy_m2'))}</td>"
            f"<td>{fmt_m2_pl(bm.get('pum_m2'))}</td><td>{fmt_m2_pl(bm.get('puu_m2'))}</td>"
            f"<td>{_esc(bm.get('mieszkania_estimate', '?'))}</td><td>{_esc(stage)}</td>"
            f"<td>{_esc(b.get('status'))}</td></tr>"
        )
    out.append("</table>")
    return out


def _html_kon_stages(m: ReportModel, n: int) -> list[str]:
    out = [
        f"<h2>{n}. Zestawienie etapów (Liczba mieszkań / PUM / PUU / PU)</h2>",
        "<table>",
        "<tr><th>Etap</th><th>Liczba mieszkań</th><th>PUM [m²]</th>"
        "<th>PUU [m²]</th><th>PU [m²]</th></tr>",
    ]
    for r in m.stage_table:
        etap = r.get("etap", "?")
        cls = ' class="suma"' if etap == "SUMA" else ""
        out.append(
            f"<tr{cls}><td>{_esc(etap)}</td><td>{_esc(r.get('liczba_mieszkan', '?'))}</td>"
            f"<td>{fmt_m2_pl(r.get('pum_m2'))}</td><td>{fmt_m2_pl(r.get('puu_m2'))}</td>"
            f"<td>{fmt_m2_pl(r.get('pu_m2'))}</td></tr>"
        )
    out.append("</table>")
    return out


def _html_kon_compliance(m: ReportModel, n: int) -> list[str]:
    s = m.compliance
    items = [
        f"wyniki reguł: pass: {s['pass']}, fail: {s['fail']}, warning: {s['warning']}, "
        f"unknown: {s['unknown']}, not_applicable: {s['not_applicable']}",
        (
            f"reguły naruszone: {_esc(s['failing_rule_ids'])}"
            if s["failing_rule_ids"]
            else "brak naruszonych reguł twardych (zero hard violations)"
        ),
        "pełny ślad reguł (trace + geometria dowodowa): zasób "
        f"<code>{_esc(m.metrics_resource or '')}</code>",
    ]
    return [f"<h2>{n}. Zgodność WT/ppoż (walidatory między-budynkowe)</h2>", *_html_list(items)]


def _html_kon_staging(m: ReportModel, n: int) -> list[str]:
    if m.staging:
        not_pass = [s for s in m.staging if str(s.get("status")) not in ("pass",)]
        items = [f"kontrole: {len(m.staging)}; poza statusem pass: {len(not_pass)}"]
        items.extend(
            f"[{_esc(s.get('status'))}] {_esc(s.get('message', ''))}" for s in m.staging
        )
    else:
        items = ["brak zadeklarowanych etapów (etapowanie nieobjęte kontrolą)"]
    return [f"<h2>{n}. Etapowanie — kontrole spójności (soft)</h2>", *_html_list(items)]


def _html_kon_rationale(m: ReportModel, n: int) -> list[str]:
    out = [f"<h2>{n}. Uzasadnienie projektowe (&quot;dlaczego tak&quot;)</h2>"]
    out.append(
        '<p class="disclaimer">Zapis rozumowania modelu-architekta dla każdej iteracji '
        "(chronologicznie). Tekst dokumentacyjny — nigdy nie był wejściem walidacji "
        "(NFR-SEC-003).</p>"
    )
    entries = [e for e in m.rationales if e.get("rationale")]
    if not entries:
        out.extend(_html_list(["brak zapisanych uzasadnień iteracji"]))
        return out
    out.append("<ol>")
    for entry in entries:
        verdict = (
            "zaakceptowana"
            if entry.get("accepted")
            else ("odrzucona" if entry.get("valid") is False else "oceniona")
        )
        out.append(
            f"<li><em>{_esc(entry.get('timestamp', '?'))}</em> ({verdict}):"
            f"<blockquote>{_esc(entry['rationale'])}</blockquote></li>"
        )
    out.append("</ol>")
    return out


def _html_kon_unknowns(m: ReportModel, n: int) -> list[str]:
    out = [f"<h2>{n}. Pozycje niepewne i pytania do gminy (aneks)</h2>"]
    if m.unknowns:
        out.extend(
            _html_list(
                [
                    f"[{_esc(u.get('severity', '?'))}] {_esc(u.get('topic', '?'))} — "
                    f"powód: {_esc(u.get('reason', '?'))}"
                    for u in m.unknowns
                ]
            )
        )
        questions = [
            _esc(u["suggested_action"]) for u in m.unknowns if u.get("suggested_action")
        ]
        if questions:
            out.append("<p>Pytania do gminy / urzędu:</p>")
            out.extend(_html_list(questions))
    else:
        out.extend(_html_list(["brak otwartych pozycji niepewnych"]))
    if m.missing_indicators:
        out.extend(
            _html_list(
                [
                    f"wskaźniki planistyczne do potwierdzenia w MPZP/WZ: "
                    f"{_esc(m.missing_indicators)} (nigdy wartości domyślne, §0v2.4)"
                ]
            )
        )
    return out


def _html_kon_disclaimers(m: ReportModel, n: int) -> list[str]:
    items = []
    cb = m.config_basis
    if cb:
        items.append(
            f"PUM/PUU/mieszkania: estymacja heurystyczna (basis: "
            f"<code>{_esc(cb.get('basis', 'industry_heuristic'))}</code>, "
            f"pum_efficiency={_esc(cb.get('pum_efficiency', '?'))}, "
            f"avg_mieszkanie_m2={_esc(cb.get('avg_mieszkanie_m2', '?'))})."
        )
        if cb.get("measurement_standard"):
            items.append(f"norma odniesienia: {_esc(cb['measurement_standard'])}")
    items.append(
        "pewność danych: wyniki zależą od kompletności wskaźników MPZP/WZ i danych "
        "źródłowych; pozycje <code>unknown</code> pozostają niepewne do potwierdzenia."
    )
    out = [f"<h2>{n}. Założenia i zastrzeżenia</h2>", *_html_list(items)]
    out.append(f'<p class="disclaimer">{_esc(DISCLAIMER_TOOL.strip("_"))}</p>')
    return out


def _html_kon_executive(m: ReportModel, n: int) -> list[str]:
    return [
        f"<h2>{n}. Synteza (executive summary)</h2>",
        *_html_list([_esc(s) for s in m.executive_summary]),
    ]


_KONCEPCJA_HTML_SECTIONS: dict[str, Callable[[ReportModel, int], list[str]]] = {
    "executive_summary": _html_kon_executive,
    "brief": _html_kon_brief,
    "headline": _html_kon_headline,
    "buildings": _html_kon_buildings,
    "stages": _html_kon_stages,
    "compliance": _html_kon_compliance,
    "staging": _html_kon_staging,
    "rationale": _html_kon_rationale,
    "unknowns": _html_kon_unknowns,
    "disclaimers": _html_kon_disclaimers,
}


# --- screening HTML sections ------------------------------------------------ #
def _html_screening_section(m: ReportModel, key: str) -> list[str]:
    """Render one screening section as HTML by reusing the MD emitter content.

    The screening sections are simple heading+bullet blocks, so the HTML mirrors
    the Markdown lines 1:1 (same emitters → same numbers by construction), with
    HTML escaping applied per line.
    """
    lines: list[str] = []
    _SCREENING_MD_SECTIONS[key](m, lines)
    out: list[str] = []
    bullets: list[str] = []

    def _flush() -> None:
        nonlocal bullets
        if bullets:
            out.extend(_html_list(bullets))
            bullets = []

    for ln in lines:
        if not ln:
            continue
        if ln.startswith("## "):
            _flush()
            out.append(f"<h2>{_esc(ln[3:])}</h2>")
        elif ln.lstrip().startswith("- "):
            bullets.append(_esc(ln.lstrip()[2:]))
        else:
            _flush()
            out.append(f"<p>{_esc(ln)}</p>")
    _flush()
    return out


def render_model_html(model: ReportModel) -> str:
    """Render deterministic single-file HTML from the unified model (§31).

    No external assets (inline CSS only); same audience-selected section list and
    the SAME formatted numbers as the Markdown render (shared fmt helpers). The
    koncepcja variant carries the deliverable title block, the plan-render
    reference with the 4-status legend note, the stage table and the annex.
    """
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="pl"><head><meta charset="utf-8">',
        f"<title>{_esc(model.analysis_id)} — raport</title>",
        f"<style>{_HTML_CSS}</style>",
        "</head><body>",
    ]
    parts.extend(_html_title_block(model))
    if model.kind == "koncepcja":
        if model.plan_png_resource:
            parts.append(
                f"<p>Rysunek planu zagospodarowania: "
                f"<code>{_esc(model.plan_png_resource)}</code><br>"
                f'<span class="legend">{_esc(_PLAN_LEGEND_NOTE)}</span></p>'
            )
        for n, key in enumerate(model.sections, start=1):
            parts.extend(_KONCEPCJA_HTML_SECTIONS[key](model, n))
    else:
        for key in model.sections:
            parts.extend(_html_screening_section(model, key))
        parts.append(f'<p class="disclaimer">{_esc(DISCLAIMER_TOOL.strip("_"))}</p>')
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


# =========================================================================== #
# PDF — weasyprint over the SAME HTML, behind a guarded import
# =========================================================================== #
class PdfUnavailableError(RuntimeError):
    """PDF rendering is unavailable on this host (missing weasyprint/pango)."""


def _weasyprint_or_reason() -> tuple[Any | None, str | None]:
    """Import weasyprint guardedly; return ``(module, None)`` or ``(None, reason)``.

    weasyprint hard-depends on the SYSTEM pango/cairo shared libraries — on hosts
    without them (e.g. this WSL2 box: no libpango, no root to apt-install) the
    import raises ``OSError`` from ``ffi.dlopen``. We never fake a PDF; the
    reason is surfaced as ``pdf_unavailable`` (Phase 14 contract).
    """
    try:
        import weasyprint  # noqa: PLC0415 — guarded optional dependency
    except (ImportError, OSError) as exc:  # pragma: no cover - host-dependent
        return None, f"weasyprint nieimportowalny na tym hoście: {exc}"
    return weasyprint, None


def pdf_available() -> tuple[bool, str | None]:
    """Whether PDF rendering works here; ``(False, reason)`` when it cannot."""
    module, reason = _weasyprint_or_reason()
    return (module is not None), reason


def render_model_pdf(model: ReportModel) -> bytes:
    """Render PDF via weasyprint from the SAME HTML as :func:`render_model_html`.

    Raises :class:`PdfUnavailableError` (with the import failure reason) when the
    weasyprint system stack is absent — callers surface ``pdf_unavailable``.
    """
    weasyprint, reason = _weasyprint_or_reason()
    if weasyprint is None:  # pragma: no cover - host-dependent
        raise PdfUnavailableError(reason or "weasyprint unavailable")
    html_text = render_model_html(model)
    pdf: bytes = weasyprint.HTML(string=html_text).write_pdf()
    return pdf
