"""Markdown + JSON report rendering for an AnalysisResult (Phase 7 §7.1.5 / §22).

* :func:`render_markdown` — the §22 template, filled from an
  :class:`~plot_domain.AnalysisResult`. Headline numbers (decision, parcel area,
  buildable area, counts) are derived from the SAME result object as :func:`render_json`,
  so MD and JSON agree by construction (§7.3 / NFR-AUD: "numbers in MD must match JSON").
* :func:`render_json` — the AnalysisResult as a JSON-mode dict (the §10.7 contract).
* :func:`render_envelope_map` — a deterministic buildable-envelope PNG via the Phase 3
  :func:`render_map` (parcel + constraints + no-build + envelope layers), so the model can
  visually verify the envelope (Phase 4 tie-in).

This module renders an already-computed result — it does NO analysis, no network, and does
not import plot_connectors / plot_rules (§3.4 decoupling holds: it stays within plot_reports).
"""

from __future__ import annotations

from typing import Any

from plot_domain import AnalysisResult

from plot_reports.render import Layer, LayerRole, RenderResult, render_map


def render_json(result: AnalysisResult) -> dict[str, Any]:
    """Return the AnalysisResult as a JSON-serialisable dict (the §10.7 contract)."""
    return result.model_dump(mode="json")


def _fmt_m2(value: float | None) -> str:
    return "brak danych" if value is None else f"{value:,.0f} m²"


def _decision_label(decision: str) -> str:
    return {
        "OK": "OK — brak istotnych przeszkód na poziomie screeningu",
        "OK_WITH_RISKS": "OK_WITH_RISKS — możliwe do rozważenia, z ryzykami",
        "NEEDS_MANUAL_REVIEW": "NEEDS_MANUAL_REVIEW — wymaga weryfikacji eksperckiej",
        "LIKELY_BLOCKED": "LIKELY_BLOCKED — prawdopodobnie zablokowane",
    }.get(decision, decision)


def headline_numbers(result: AnalysisResult) -> dict[str, Any]:
    """The headline numbers shown in BOTH MD and JSON (single source of truth, §7.3).

    A test asserts the MD text contains exactly these values, so they cannot drift.
    """
    parcel_area = result.parcel.area_m2 if result.parcel else None
    env = result.buildable_envelope
    env_area = env.area_m2 if env else None
    env_pct = None
    if env and parcel_area:
        env_pct = (env_area or 0.0) / parcel_area * 100.0 if parcel_area else None
    return {
        "decision": result.decision.value,
        "status": result.status.value,
        "parcel_area_m2": parcel_area,
        "buildable_area_m2": env_area,
        "buildable_percent": round(env_pct, 1) if env_pct is not None else None,
        "envelope_confidence": env.confidence if env else None,
        "risk_count": len(result.risks),
        "unknown_count": len(result.unknowns),
        "constraint_count": len(result.constraints),
        "next_action_count": len(result.next_actions),
        "evidence_count": len(result.evidence),
    }


def render_markdown(result: AnalysisResult) -> str:
    """Render the §22 Markdown report from ``result`` (numbers == :func:`render_json`)."""
    h = headline_numbers(result)
    parcel_id = (
        (result.parcel.external_id or result.parcel.teryt or result.parcel.id)
        if result.parcel
        else result.analysis_id
    )
    metrics = result.planning.get("_geometry_metrics", {}) if isinstance(result.planning, dict) else {}

    lines: list[str] = []
    lines.append(f"# Analiza działki: {parcel_id}")
    lines.append("")
    lines.append("## Decyzja screeningowa")
    lines.append(_decision_label(h["decision"]))
    lines.append(f"(status analizy: {h['status']})")
    lines.append("")

    # Najważniejsze wnioski
    lines.append("## Najważniejsze wnioski")
    lines.append(
        f"- Powierzchnia działki: {_fmt_m2(h['parcel_area_m2'])}; "
        f"obszar zabudowy (buildable envelope): {_fmt_m2(h['buildable_area_m2'])}"
        + (f" ({h['buildable_percent']:.1f}% działki)" if h["buildable_percent"] is not None else "")
    )
    lines.append(f"- Wykryte ograniczenia: {h['constraint_count']}; czerwone flagi: {h['risk_count']}.")
    lines.append(f"- Pozycje niepewne (unknowns): {h['unknown_count']}.")
    lines.append("")

    # Czerwone flagi
    lines.append("## Czerwone flagi")
    if result.risks:
        for r in result.risks:
            lines.append(
                f"- [{r.severity.value}/{r.risk_type.value}] {r.summary} "
                f"(status: {r.status.value}, pewność: {r.confidence.value}"
                + (f", źródło: {r.source_id}" if r.source_id else ", źródło: no_source")
                + ")"
            )
    else:
        lines.append("- Brak czerwonych flag na poziomie screeningu.")
    lines.append("")

    # Co można rozważać projektowo
    lines.append("## Co można rozważać projektowo")
    env = result.buildable_envelope
    if env and (env.area_m2 or 0.0) > 0.0:
        lines.append(
            f"- Realny obszar pod zabudowę: {_fmt_m2(env.area_m2)} "
            f"(pewność envelope: {env.confidence:.2f})."
        )
        if env.largest_inscribed_rectangle is not None:
            lines.append("- Wyznaczono największy prostokąt wpisany (orientacyjny obrys budynku).")
    else:
        lines.append("- Po odsunięciach i strefach wyłączonych brak istotnego obszaru pod zabudowę.")
    lines.append("")

    # Co wymaga potwierdzenia
    lines.append("## Co wymaga potwierdzenia")
    if result.unknowns:
        for u in result.unknowns:
            lines.append(f"- [{u.severity.value}] {u.topic} — powód: {u.reason}.")
    else:
        lines.append("- Brak otwartych pozycji niepewnych.")
    lines.append("")

    # Parametry działki
    lines.append("## Parametry działki")
    lines.append(f"- powierzchnia: {_fmt_m2(h['parcel_area_m2'])}")
    if metrics:
        lines.append(f"- obwód: {metrics.get('perimeter_m', 'brak danych')} m")
        lines.append(
            f"- kształt: zwartość {metrics.get('compactness', '?')}, "
            f"nieregularność {metrics.get('irregularity', '?')}"
        )
        lines.append(f"- oś główna: {metrics.get('main_axis_length_m', '?')} m")
    lines.append("")

    # Planowanie
    lines.append("## Planowanie")
    planning = result.planning if isinstance(result.planning, dict) else {}
    lines.append(f"- MPZP/POG/WZ: {planning.get('mpzp_pog_wz', 'brak danych')}")
    lines.append(f"- pokrycie planistyczne: {planning.get('coverage_status', 'brak danych')}")
    lines.append(f"- gmina: {planning.get('municipality') or 'brak danych'}")
    lines.append("")

    # Buildable envelope
    lines.append("## Buildable envelope")
    lines.append(f"- powierzchnia potencjalna: {_fmt_m2(h['buildable_area_m2'])}")
    main_constraints = ", ".join(
        sorted({c.constraint_type for c in result.constraints})
    ) or "brak"
    lines.append(f"- główne ograniczenia: {main_constraints}")
    if env and isinstance(env.metadata, dict):
        for tr in env.metadata.get("removed_by", []):
            lines.append(
                f"  - {tr.get('label')}: −{tr.get('removed_m2'):,.0f} m² "
                f"({tr.get('removed_percent')}%)"
            )
    lines.append("")

    # Media i dojazd
    lines.append("## Media i dojazd")
    layer_status = planning.get("_risk_layer_status", {}) if isinstance(planning, dict) else {}
    lines.append(f"- media (uzbrojenie): {layer_status.get('utilities', 'nie sprawdzono')}")
    lines.append(f"- dojazd / drogi: {layer_status.get('roads', 'nie sprawdzono')}")
    lines.append("")

    # Środowisko, wody, geologia, zabytki
    lines.append("## Środowisko, wody, geologia, zabytki")
    lines.append(f"- powódź: {layer_status.get('flood', 'nie sprawdzono')}")
    lines.append(f"- ochrona przyrody: {layer_status.get('protected', 'nie sprawdzono')}")
    lines.append(f"- osuwiska / geologia: {layer_status.get('landslide', 'nie sprawdzono')}")
    lines.append(f"- zabytki: {layer_status.get('heritage', 'nie sprawdzono')}")
    lines.append(f"- cieki wodne: {layer_status.get('watercourses', 'nie sprawdzono')}")
    lines.append(f"- las: {layer_status.get('forest', 'nie sprawdzono')}")
    lines.append("")

    # Chłonność
    lines.append("## Chłonność")
    lines.append("- wariant konserwatywny: nie obliczono (chłonność wchodzi w Fazie 9)")
    lines.append("- wariant bazowy: nie obliczono (Faza 9)")
    lines.append("- wariant optymistyczny: nie obliczono (Faza 9)")
    lines.append("")

    # Następne kroki
    lines.append("## Następne kroki")
    if result.next_actions:
        for a in result.next_actions:
            who = f" → {a.addressed_to}" if a.addressed_to else ""
            lines.append(f"- [{a.priority.value}] {a.title}: {a.detail}{who}")
    else:
        lines.append("- Brak rekomendacji.")
    lines.append("")

    # Źródła i confidence
    lines.append("## Źródła i confidence")
    sources = planning.get("_sources", []) if isinstance(planning, dict) else []
    if sources:
        for s in sources:
            lines.append(
                f"- {s.get('publisher')} ({s.get('source_id')}): "
                f"legal_status={s.get('legal_status')}, "
                f"pobrano={s.get('retrieved_at')}, confidence={s.get('confidence')}"
            )
    else:
        lines.append("- Brak źródeł (no_source) — wynik częściowy.")
    lines.append(f"- Liczba pozycji evidence: {h['evidence_count']}.")
    lines.append("")
    lines.append(
        "_Wynik jest narzędziem priorytetyzującym, nie decyzją prawną. "
        "Każda teza ma źródło albo oznaczenie braku źródła (no_source)._"
    )

    return "\n".join(lines) + "\n"


def render_envelope_map(
    result: AnalysisResult, *, fmt: str = "png", title: str | None = None
) -> RenderResult:
    """Render the buildable-envelope map from ``result`` via the Phase 3 renderer.

    Layers (deterministic z-order from each role): parcel → soft constraints → hard
    constraints → no-build → buildable envelope. The caption shows which constraint
    removed which area (§30), read from the envelope ``metadata.removed_by`` trace.
    """
    from shapely.geometry import shape

    layers: list[Layer] = []
    if result.parcel and result.parcel.geometry:
        layers.append(Layer(name="Działka", geometries=[result.parcel.geometry], role=LayerRole.PARCEL))

    env = result.buildable_envelope
    removed_by = env.metadata.get("removed_by", []) if env and isinstance(env.metadata, dict) else []
    removed_index = {str(tr.get("ref")): tr for tr in removed_by}

    for con in result.constraints:
        if con.geometry is None:
            continue
        hard = bool(con.machine_summary.get("hard"))
        tr = removed_index.get(con.constraint_id)
        layers.append(
            Layer(
                name=con.constraint_type,
                geometries=[con.geometry],
                role=LayerRole.CONSTRAINT_HARD if hard else LayerRole.CONSTRAINT_SOFT,
                removed_area_m2=(tr.get("removed_m2") if tr else con.applies_to_area_m2),
                removed_area_percent=(tr.get("removed_percent") if tr else con.applies_to_percent),
            )
        )

    if env and env.geometry:
        layers.append(
            Layer(name="Buildable envelope", geometries=[env.geometry], role=LayerRole.BUILDABLE_ENVELOPE)
        )

    if not layers:
        # Nothing to draw — render a tiny placeholder so callers still get a valid image.
        from shapely.geometry import Polygon

        layers = [Layer(name="brak geometrii", geometries=[Polygon()], role=LayerRole.OTHER)]
    # touch shape import so a constraint geom that arrives as a Feature still renders
    _ = shape
    return render_map(
        layers,
        crs="EPSG:2180",
        fmt="png" if fmt != "svg" else "svg",
        basemap=False,
        title=title or f"Buildable envelope — {result.analysis_id}",
    )
