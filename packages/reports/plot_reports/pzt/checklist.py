"""§13–18 compliance checklist for the PZT draft package (Phase 15 Task 4).

Per regulation section (Dz.U. 2020 poz. 1609, t.j. 2022 poz. 1679) every item
is graded HONESTLY:

* ``done`` — produced by the tool from real data,
* ``missing`` — required content the tool could not produce because the data is
  absent (e.g. rzędne without NMT) — never silently dropped (test-enforced),
* ``requires_projektant`` — a statutory duty of the projektant (e.g. obszar
  oddziaływania),
* ``requires_uprawnienia`` — needs licensed professionals / qualified
  signatures (mapa do celów projektowych, podpisy).

The checklist always carries the mandatory draft-not-PB disclaimer
(:data:`~plot_reports.pzt.opisowa.PZT_DISCLAIMER`, anti-pattern §15.4).
"""

from __future__ import annotations

from typing import Any

from plot_reports.pzt.opisowa import PZT_CITATION_BASE, PZT_DISCLAIMER, PztOpisowa

CHECKLIST_STATUSES = ("done", "missing", "requires_projektant", "requires_uprawnienia")


def _item(section: str, item: str, status: str, reason: str) -> dict[str, str]:
    assert status in CHECKLIST_STATUSES
    return {"section": section, "item": item, "status": status, "reason": reason}


def build_pzt_checklist(
    opisowa: PztOpisowa, rysunkowa_meta: dict[str, Any]
) -> dict[str, Any]:
    """Assemble the §13–18 checklist from the opisowa statuses + drawing metadata."""
    items: list[dict[str, str]] = [
        _item(
            "§13",
            "struktura PZT: część opisowa + część rysunkowa",
            "done",
            "obie części zestawione w pakiecie szkicu (draft)",
        )
    ]

    # §14 — one checklist row per opisowa section (statuses flow through 1:1,
    # so a missing data layer flips the row to 'missing', never disappears).
    for sec in opisowa.sections:
        items.append(_item("§14", sec.title, sec.status, sec.status_reason))

    # §15 — część rysunkowa content items, graded from the drawing metadata.
    meta = rysunkowa_meta
    items.append(
        _item("§15", "granice działki lub terenu (pogrubione)", "done",
              "granice naniesione warstwą działki z pogrubioną krawędzią")
    )
    items.append(
        _item(
            "§15",
            "linia zabudowy",
            "done" if int(meta.get("building_lines_count", 0)) > 0 else "missing",
            (
                "naniesiona z danych planistycznych"
                if int(meta.get("building_lines_count", 0)) > 0
                else "brak geometrii linii zabudowy we wskaźnikach — nie naniesiono "
                "(uczciwe pominięcie)"
            ),
        )
    )
    items.append(
        _item(
            "§15",
            "obiekty budowlane z wymiarami zewnętrznymi",
            "done" if int(meta.get("dimension_count", 0)) > 0 else "missing",
            f"adnotacje wymiarowe: {meta.get('dimension_count', 0)} "
            f"(krawędzie zewnętrzne > 2 m)",
        )
    )
    items.append(
        _item("§15", "liczba kondygnacji budynków", "done",
              "etykiety kondygnacji w centroidach segmentów (renderer v2)")
    )
    items.append(
        _item(
            "§15",
            "sieci uzbrojenia terenu / przyłącza (GESUT)",
            "done" if int(meta.get("network_count", 0)) > 0 else "missing",
            (
                f"naniesiono {meta.get('network_count', 0)} sieci z GESUT"
                if int(meta.get("network_count", 0)) > 0
                else "brak danych GESUT — sieci nie naniesiono (nigdy nie są zmyślane)"
            ),
        )
    )
    fire_tagged = bool(meta.get("fire_road_tagged"))
    road_functions = list(meta.get("road_functions", []))
    items.append(
        _item(
            "§15",
            "układ komunikacyjny, w tym drogi pożarowe",
            "done" if road_functions else "missing",
            (
                f"drogi z etykietami funkcji ({', '.join(road_functions)}); "
                + (
                    "droga pożarowa oznaczona"
                    if fire_tagged
                    else "brak odcinka zadeklarowanego jako 'pozarowa' — kandydatów "
                    "ocenia walidator ppoż (sekcja opisowej)"
                )
                if road_functions
                else "wariant nie zawiera dróg"
            ),
        )
    )
    items.append(
        _item(
            "§15",
            "ukształtowanie zieleni",
            "done" if bool(meta.get("greenery_drawn")) else "missing",
            (
                "warstwa zieleni (PBC) naniesiona"
                if bool(meta.get("greenery_drawn"))
                else "wariant nie zawiera terenów zieleni"
            ),
        )
    )
    items.append(
        _item(
            "§15",
            "rzędne terenu (ukształtowanie)",
            "done" if int(meta.get("spot_elevation_count", 0)) > 0 else "missing",
            (
                f"naniesiono {meta.get('spot_elevation_count', 0)} rzędnych z NMT "
                "(narożniki budynków + działki)"
                if int(meta.get("spot_elevation_count", 0)) > 0
                else "brak danych NMT — rzędne nie naniesione (nigdy nie są zmyślane)"
            ),
        )
    )

    # §16/§17/§18 + statutory duties — beyond what a tool may claim.
    items.append(
        _item(
            "§16",
            "aktualna mapa do celów projektowych jako podkład",
            "requires_uprawnienia",
            "szkic narysowano na geometrii analitycznej (EGiB/analiza), NIE na mapie "
            "do celów projektowych — wymaga geodety uprawnionego",
        )
    )
    scale_reason = (
        "skala zadeklarowana i mierzalna w wektorowym SVG/PDF (dpi=72, "
        "jednostki SVG = punkty)"
    )
    if bool(meta.get("scale_adjusted")):
        # Review F4: the sheet was rescaled to stay under the PDF viewer
        # MediaBox ceiling — the checklist names the adjustment + reason.
        scale_reason += (
            f"; skala dostosowana z 1:{meta.get('requested_scale_denominator', '?')} "
            f"— {meta.get('scale_adjustment_reason', 'limit strony PDF')}"
        )
    items.append(
        _item(
            "§17",
            f"skala rysunku (1:{meta.get('scale_denominator', '?')})",
            "done",
            scale_reason,
        )
    )
    items.append(
        _item("§18", "oznaczenia graficzne i legenda", "done",
              "legenda statusów budynków + warstw (renderer v2)")
    )
    items.append(
        _item(
            "PB art. 20",
            "obszar oddziaływania obiektu",
            "requires_projektant",
            "szkic heurystyczny w opisowej — określenie obszaru jest ustawowym "
            "obowiązkiem projektanta",
        )
    )
    items.append(
        _item(
            "e-forma",
            "PDF wektorowy ≤ 150 MB, nazewnictwo PZT_rrrr.mm.dd",
            "done",
            f"PDF {meta.get('pdf_bytes', '?')} B (limit {meta.get('pdf_max_bytes', '?')} B), "
            "nazwa pliku wg konwencji załącznika",
        )
    )
    items.append(
        _item(
            "e-forma",
            "podpis kwalifikowany/zaufany/osobisty + uprawnienia projektanta",
            "requires_uprawnienia",
            "narzędzie nie składa podpisów — projekt budowlany podpisuje projektant "
            "z uprawnieniami",
        )
    )

    summary: dict[str, int] = {status: 0 for status in CHECKLIST_STATUSES}
    for entry in items:
        summary[entry["status"]] += 1
    return {
        "citation_base": PZT_CITATION_BASE,
        "disclaimer": PZT_DISCLAIMER,
        "items": items,
        "summary": summary,
        "is_projekt_budowlany": False,  # explicit, machine-readable (anti-pattern §15.4)
    }
