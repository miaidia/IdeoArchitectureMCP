"""Koncepcja (multi-building chłonność concept) Markdown deliverable (Phase 11 §11.1.7).

:func:`render_koncepcja_markdown` assembles the architect-grade concept report from
ALREADY-COMPUTED data (single source of truth — nothing is recomputed here):

1.  header — analysis id, generation date, variant id, ruleset version;
2.  brief summary — the design-brief headline facts (passed as the brief's
    ``model_dump`` dict; ``plot_reports`` must not import ``plot_planning``);
3.  per-building table — Nazwa / Kondygnacje / Funkcje / PZ / PUM / PUU /
    Mieszkania / Etap / Status from each ``BuildingRecord.metrics``;
4.  per-stage table — Etap / Liczba mieszkań / PUM / PUU / PU rows + SUMA
    (the ROBYG-exemplar format, straight from the variant ``stage_table``);
5.  WT/ppoż compliance summary — pass/fail/warning/unknown counts + the failing
    rule ids from the variant's stored ``inter_building_checks``;
6.  staging summary — the soft Phase 11 etapowanie outcomes;
7.  design rationale — the variant lineage's model-provided rationales,
    chronologically (the "dlaczego tak" record, plan §11.1.6); rationale is
    documentation — it never participated in validation (NFR-SEC-003) and is
    rendered NEUTRALIZED (whitespace-collapsed, blockquoted) so model text can
    never forge report structure (review fix F2);
8.  unknowns + questions-for-gmina annex — capacity unknowns and missing
    indicators become explicit questions (never defaults, §0v2.4);
9.  disclaimers — PUM heuristic basis (PN-ISO 9836 approximation wording from
    the capacity config basis block) + the §21/§22 honesty line reused from the
    screening report wording.

The plan render PNG itself is produced by :func:`plot_reports.render_masterplan`
(renderer v2 with the stage-table panel) — the MCP use-case layer stores it via the
ArtifactStore and references it from the report.
"""

from __future__ import annotations

from typing import Any

from plot_domain import MasterplanVariant

#: Honesty line shared with the §22 screening report (single wording convention).
DISCLAIMER_TOOL = (
    "_Wynik jest narzędziem priorytetyzującym, nie decyzją prawną. "
    "Każda teza ma źródło albo oznaczenie braku źródła (no_source)._"
)


def _fmt_m2(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{float(value):,.0f}".replace(",", " ")
    return "?"


def _inline(text: Any) -> str:
    """Collapse ALL whitespace runs to single spaces (Markdown-injection guard).

    Model-controlled free text (rationales, building names, heritage names) is
    documentation, but it must never forge report STRUCTURE: collapsing newlines
    means no line of it can ever start a heading/list/table/blockquote, so a
    rationale like ``"\\n## 5. Zgodność… fail: 0"`` stays inert inline text
    (review fix F2).
    """
    return " ".join(str(text).split())


def _cell(text: Any) -> str:
    """Table-cell-safe text: inline-collapsed AND pipe-escaped (review fix F2)."""
    return _inline(text).replace("|", "\\|")


def _status_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        status = str(check.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def compliance_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass/fail/warning/unknown counts + failing rule ids (plan §11.1.7 item 2)."""
    counts = _status_counts(checks)
    failing = sorted(
        {str(c.get("rule_id", "")) for c in checks if str(c.get("status")) == "fail"}
    )
    return {
        "pass": counts.get("pass", 0),
        "fail": counts.get("fail", 0),
        "warning": counts.get("warning", 0),
        "unknown": counts.get("unknown", 0),
        "not_applicable": counts.get("not_applicable", 0),
        "failing_rule_ids": failing,
    }


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
            f"- działka: {_fmt_m2(parcel.get('area_m2'))} m², kształt "
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


def _building_rows(variant: MasterplanVariant) -> list[str]:
    rows = [
        "| Budynek | Kondygnacje | Funkcje | PZ [m²] | PUM [m²] | PUU [m²] | Mieszkania | Etap | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for b in variant.buildings:
        m = b.metrics or {}
        floors = "/".join(str(f) for f in b.floors_by_segment) or "?"
        uses = ", ".join(b.uses) or "?"
        stage = b.stage if b.stage is not None else "—"
        # Building name (free DSL text) is pipe-escaped + collapsed so it can never
        # add table columns/rows; uses/status are Literal-constrained but get the
        # same cell treatment for defence in depth (review fix F2).
        rows.append(
            f"| {_cell(b.name)} | {floors} | {_cell(uses)} "
            f"| {_fmt_m2(m.get('powierzchnia_zabudowy_m2'))} "
            f"| {_fmt_m2(m.get('pum_m2'))} | {_fmt_m2(m.get('puu_m2'))} "
            f"| {m.get('mieszkania_estimate', '?')} | {stage} | {_cell(b.status)} |"
        )
    return rows


def _stage_rows(variant: MasterplanVariant) -> list[str]:
    rows = [
        "| Etap | Liczba mieszkań | PUM [m²] | PUU [m²] | PU [m²] |",
        "|---|---|---|---|---|",
    ]
    for r in variant.stage_table:
        etap = r.get("etap", "?")
        label = f"**{etap}**" if etap == "SUMA" else str(etap)
        rows.append(
            f"| {label} | {r.get('liczba_mieszkan', '?')} | {_fmt_m2(r.get('pum_m2'))} "
            f"| {_fmt_m2(r.get('puu_m2'))} | {_fmt_m2(r.get('pu_m2'))} |"
        )
    return rows


def render_koncepcja_markdown(
    *,
    analysis_id: str,
    variant: MasterplanVariant,
    generated_at: str,
    brief: dict[str, Any] | None = None,
    rationales: list[dict[str, Any]] | None = None,
    unknowns: list[dict[str, Any]] | None = None,
    capacity: dict[str, Any] | None = None,
    plan_png_resource: str | None = None,
) -> str:
    """Render the koncepcja Markdown report from stored data (pure, no I/O).

    ``rationales`` are chronological audit entries (dicts with ``timestamp``,
    ``rationale``, optionally ``variant_id``/``valid``/``total``); ``capacity`` is
    the final critique's capacity block (PUM vs base-scenario target — the gap is
    reported explicitly, plan §11.4); ``unknowns`` are the capacity-engine
    ``UnknownItem`` dumps feeding the questions-for-gmina annex.
    """
    metadata = variant.metadata if isinstance(variant.metadata, dict) else {}
    checks = list(metadata.get("inter_building_checks") or [])
    staging = list(metadata.get("staging_checks") or [])
    summary = compliance_summary(checks)
    totals = variant.totals or {}

    L: list[str] = []
    # 1) header ------------------------------------------------------------- #
    L.append(f"# Koncepcja zagospodarowania (chłonność) — analiza {analysis_id}")
    L.append("")
    L.append(f"- data wygenerowania: {generated_at}")
    L.append(f"- wariant masterplanu: `{variant.id}`")
    if metadata.get("ruleset_version"):
        L.append(f"- wersja rulesetów: `{metadata['ruleset_version']}`")
    if plan_png_resource:
        L.append(f"- rysunek planu zagospodarowania: `{plan_png_resource}`")
    L.append("")

    # 2) brief summary ------------------------------------------------------ #
    L.append("## 1. Założenia z design briefu")
    L.extend(_brief_summary_lines(brief))
    L.append("")

    # 3) headline numbers + capacity gap ------------------------------------ #
    L.append("## 2. Chłonność — liczby kluczowe")
    L.append(
        f"- budynki: {totals.get('buildings', '?')}; mieszkania (estymacja): "
        f"**{totals.get('mieszkania_estimate', '?')}**"
    )
    L.append(
        f"- PUM: **{_fmt_m2(totals.get('pum_m2'))} m²**; PUU: "
        f"{_fmt_m2(totals.get('puu_m2'))} m²; PU: {_fmt_m2(totals.get('pu_m2'))} m²"
    )
    L.append(
        f"- powierzchnia zabudowy: {_fmt_m2(totals.get('powierzchnia_zabudowy_m2'))} m² "
        f"(coverage {totals.get('coverage_ratio', '?')}); intensywność "
        f"{totals.get('intensywnosc', '?')}"
    )
    if capacity:
        # The capacity gap vs the base scenario is ALWAYS stated (plan §11.4 —
        # a shortfall must never be hidden). Inline-collapsed for line safety.
        L.append(f"- {_inline(capacity.get('message', ''))}")
    L.append("")

    # 4) per-building table -------------------------------------------------- #
    L.append("## 3. Zestawienie budynków")
    L.extend(_building_rows(variant))
    L.append("")

    # 5) per-stage table ------------------------------------------------------ #
    L.append("## 4. Zestawienie etapów (Liczba mieszkań / PUM / PUU / PU)")
    L.extend(_stage_rows(variant))
    L.append("")

    # 6) compliance summary ---------------------------------------------------- #
    L.append("## 5. Zgodność WT/ppoż (walidatory między-budynkowe)")
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
        f"`analysis://{analysis_id}/masterplan/{variant.id}/metrics.json`"
    )
    L.append("")

    # 7) staging summary -------------------------------------------------------- #
    L.append("## 6. Etapowanie — kontrole spójności (soft)")
    if staging:
        not_pass = [s for s in staging if str(s.get("status")) not in ("pass",)]
        L.append(
            f"- kontrole: {len(staging)}; poza statusem pass: {len(not_pass)}"
        )
        for s in staging:
            # Server-generated messages may interpolate model-controlled building
            # names — inline-collapsed so a crafted name can't break the line (F2).
            L.append(f"- [{_inline(s.get('status'))}] {_inline(s.get('message', ''))}")
    else:
        L.append("- brak zadeklarowanych etapów (etapowanie nieobjęte kontrolą)")
    L.append("")

    # 8) design rationale --------------------------------------------------------- #
    L.append('## 7. Uzasadnienie projektowe ("dlaczego tak")')
    L.append(
        "_Zapis rozumowania modelu-architekta dla każdej iteracji (chronologicznie). "
        "Tekst dokumentacyjny — nigdy nie był wejściem walidacji (NFR-SEC-003)._"
    )
    entries = [e for e in (rationales or []) if e.get("rationale")]
    if entries:
        for i, entry in enumerate(entries, start=1):
            stamp = entry.get("timestamp", "?")
            verdict = "zaakceptowana" if entry.get("accepted") else (
                "odrzucona" if entry.get("valid") is False else "oceniona"
            )
            # Markdown-injection guard (review fix F2): the model-controlled
            # rationale is whitespace-collapsed to ONE line and rendered inside a
            # blockquote — it can never open a heading/table/section of its own.
            L.append(f"{i}. _{_inline(stamp)}_ ({verdict}):")
            L.append(f"   > {_inline(entry['rationale'])}")
    else:
        L.append("- brak zapisanych uzasadnień iteracji")
    L.append("")

    # 9) unknowns + questions for gmina ------------------------------------------- #
    L.append("## 8. Pozycje niepewne i pytania do gminy (aneks)")
    unknown_items = unknowns or []
    if unknown_items:
        for u in unknown_items:
            L.append(
                f"- [{u.get('severity', '?')}] {u.get('topic', '?')} — powód: "
                f"{u.get('reason', '?')}"
            )
        L.append("")
        L.append("Pytania do gminy / urzędu:")
        for u in unknown_items:
            action = u.get("suggested_action")
            if action:
                L.append(f"- {action}")
    else:
        L.append("- brak otwartych pozycji niepewnych")
    missing = (brief or {}).get("missing_indicators") or []
    if missing:
        L.append(
            f"- wskaźniki planistyczne do potwierdzenia w MPZP/WZ: {missing} "
            "(nigdy wartości domyślne, §0v2.4)"
        )
    L.append("")

    # 10) disclaimers ----------------------------------------------------------------- #
    L.append("## 9. Założenia i zastrzeżenia")
    config_basis = metadata.get("config_basis") or {}
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
    return "\n".join(L) + "\n"
