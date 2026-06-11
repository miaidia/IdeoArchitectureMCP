"""Phase 15 — PZT draft package tests (plan §15.3 verification checklist).

Over the Phase 11 riverside golden masterplan (8 buildings incl. 2 retained
zabytki, road loop, 2 stages — the acceptance fixture):

* opisowa: all §14 sections present; statuses honest (data-driven `done`,
  absent data `missing`, obszar oddziaływania `requires_projektant`);
* zestawienie powierzchni numbers EQUAL the Phase 9 capacity-engine output
  exactly (single source of truth, anti-pattern §15.4 — test-enforced);
* rysunkowa: SVG is vector (paths, no <image> rasters), the declared 1:500
  scale is MEASURED on a known building edge (A1 = 90 m) within tolerance,
  dimension annotations present, file naming per the e-form załącznik
  convention (PZT_rrrr.mm.dd_...); PDF starts %PDF, is small, and embeds NO
  raster XObject (/Subtype /Image absent — the matplotlib PDF backend is
  vector; the bare /ImageB/C/I tokens are the standard ProcSet declaration);
* checklist honesty: rzędne flip missing → done → missing with terrain data
  attached/removed (never silently dropped); fire-check fail → the ppoż section
  contains NIEZGODNOŚĆ (never omitted);
* storeys: 6-floor building → 6 basis-marked StoreyRecords; usługi w parterze
  → storey 0 uslugowy; LokalRecord placeholder lists stay empty (PW horizon);
* the draft-not-projekt-budowlany disclaimer is present and prominent in the
  opisowa MD, the checklist JSON and the tool result (mandatory, §15.4).

ZERO network; all geometry is the local fixture frame.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from tests.masterplan_fixtures import (
    riverside_e2e_indicators,
    riverside_masterplan_payload,
    riverside_parcel,
)
from tests.site_fixtures import synthetic_dem_bytes
from tests.wt_fixtures import X0, Y0

_SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
_PZT_FILENAME_RE = r"PZT_\d{4}\.\d{2}\.\d{2}_.*\.pdf"


def _uri_path(uri: str) -> Path:
    assert uri.startswith("file://")
    return Path(uri.removeprefix("file://"))


@pytest.fixture(scope="module")
def pzt_env():
    """The riverside golden variant stored via the REAL propose_layout path."""
    from plot_agent.context import AnalysisContext
    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT
    from plot_mcp_server import usecases

    DEFAULT_MASTERPLAN_AUDIT.clear()
    old_adhoc = usecases.ADHOC_ANALYSIS_ID
    usecases.ADHOC_ANALYSIS_ID = "analysis-pzt"
    parcel = riverside_parcel()
    context = AnalysisContext.with_loaded_rules(parcel=parcel, buildable_envelope=parcel)
    usecases.set_drawing_context(context)
    try:
        out = usecases.propose_layout_render(
            riverside_masterplan_payload(flawed=False),
            riverside_e2e_indicators(),
            "wariant golden dla pakietu PZT",
        )
        assert out["valid"] is True and out["accepted"] is True
        yield {
            "usecases": usecases,
            "out": out,
            "parcel": parcel,
            "context": context,
            "aid": "analysis-pzt",
        }
    finally:
        usecases.set_drawing_context(None)
        usecases.ADHOC_ANALYSIS_ID = old_adhoc
        DEFAULT_MASTERPLAN_AUDIT.clear()


@pytest.fixture(scope="module")
def pzt_draft(pzt_env) -> dict[str, Any]:
    """The PZT draft generated WITHOUT site context (no terrain/GESUT — honest)."""
    rep = pzt_env["usecases"].report_generate(
        None, "pzt-draft", pzt_env["out"]["variant_id"]
    )
    assert rep["status"] == "rendered"
    return rep


# --------------------------------------------------------------------------- #
# Opisowa — §14 sections + honesty statuses (plan §15.3)
# --------------------------------------------------------------------------- #
def test_opisowa_contains_all_paragraph14_sections(pzt_draft) -> None:
    sections = {s["id"]: s["status"] for s in pzt_draft["opisowa_sections"]}
    assert set(sections) == {
        "przedmiot",
        "stan_istniejacy",
        "projektowane",
        "zestawienie_powierzchni",
        "ochrona_i_zagrozenia",
        "ochrona_ppoz",
        "obszar_oddzialywania",
        "rejestr_zabytkow",
    }
    # Data-driven sections are green for the golden variant…
    assert sections["przedmiot"] == "done"
    assert sections["projektowane"] == "done"
    assert sections["zestawienie_powierzchni"] == "done"
    assert sections["ochrona_ppoz"] == "done"
    # …absent data is EXPLICITLY missing (no site context stored — §21)…
    assert sections["stan_istniejacy"] == "missing"
    assert sections["ochrona_i_zagrozenia"] == "missing"
    # …and the obszar oddziaływania is always the projektant's duty.
    assert sections["obszar_oddzialywania"] == "requires_projektant"


def test_opisowa_markdown_renders_sections_and_honest_gaps(pzt_draft) -> None:
    md = _uri_path(pzt_draft["artifacts"]["opisowa_md"]).read_text(encoding="utf-8")
    for title_part in (
        "Przedmiot zamierzenia budowlanego",
        "Istniejący stan zagospodarowania",
        "Projektowane zagospodarowanie",
        "Zestawienie powierzchni",
        "Ochrona konserwatorska, wpływy górnicze, zagrożenia",
        "Warunki ochrony przeciwpożarowej, w tym drogi pożarowe",
        "obszarze oddziaływania",
        "rejestru zabytków",
    ):
        assert title_part in md, f"missing §14 section: {title_part}"
    assert "Dz.U. 2020 poz. 1609" in md and "2022 poz. 1679" in md
    # Honest data_unavailable for BDOT10k/GESUT (no site context) — never invented.
    assert "data_unavailable" in md
    # The compliant golden has PASSING fire checks → evidence statements.
    assert "Zapewniono wymagania reguły PL-PPOZ-" in md
    assert "NIEZGODNOŚĆ" not in md


def test_zestawienie_equals_capacity_engine_exactly(pzt_env, pzt_draft) -> None:
    """Single source of truth (anti-pattern §15.4): opisowa numbers == engine."""
    from plot_agent.drawing import MasterplanProposal
    from plot_planning import masterplan_metrics

    engine = masterplan_metrics(
        MasterplanProposal.model_validate(riverside_masterplan_payload(flawed=False)),
        pzt_env["parcel"],
        riverside_e2e_indicators(),
        registry=pzt_env["context"].ruleset,
    )
    reported = pzt_draft["zestawienie_powierzchni"]
    assert reported == engine.zestawienie  # EXACT equality, all keys
    # The zabudowa number is the SAME object the totals carry (no recomputation).
    assert reported["zabudowa_m2"] == engine.totals["powierzchnia_zabudowy_m2"]
    # And the opisowa JSON artifact carries the same block verbatim.
    opisowa = json.loads(
        _uri_path(pzt_draft["artifacts"]["opisowa_json"]).read_bytes()
    )
    assert opisowa["zestawienie"] == reported
    section = next(
        s for s in opisowa["sections"] if s["id"] == "zestawienie_powierzchni"
    )
    assert section["data"] == reported


def test_ppoz_fail_renders_niezgodnosc_pass_renders_zapewniono() -> None:
    """RuleChecks → statements: fail is NEVER omitted (plan §15.3 honesty)."""
    from plot_domain import MasterplanVariant
    from plot_reports import build_pzt_opisowa, render_pzt_opisowa_markdown

    variant = MasterplanVariant(id="mvar:test", analysis_id="a-test")
    checks = [
        {"rule_id": "PL-PPOZ-271-273-FIRE-SEPARATION-001", "status": "fail",
         "message": "para A1|B1: 6.0 m < 8.0 m", "source_reference": "Dz.U. 2022 poz. 1225 §271"},
        {"rule_id": "PL-PPOZ-DROGA-POZAROWA-001", "status": "pass",
         "message": "droga pożarowa w pasie 5-15 m", "source_reference": "Dz.U. 2009 nr 124 poz. 1030"},
        {"rule_id": "PL-PPOZ-271-273-FIRE-SEPARATION-001", "status": "unknown",
         "message": "brak danych o ścianie oddzielenia", "source_reference": "Dz.U. 2022 poz. 1225 §271"},
    ]
    opisowa = build_pzt_opisowa(
        analysis_id="a-test", variant=variant, generated_at="2026-06-12T00:00:00",
        inter_building_checks=checks,
    )
    md = render_pzt_opisowa_markdown(opisowa)
    assert "NIEZGODNOŚĆ: reguła PL-PPOZ-271-273-FIRE-SEPARATION-001" in md
    assert "para A1|B1" in md  # evidence travels with the statement
    assert "Zapewniono wymagania reguły PL-PPOZ-DROGA-POZAROWA-001" in md
    assert "Wymaga ustalenia" in md
    ppoz = next(s for s in opisowa.sections if s.id == "ochrona_ppoz")
    assert any("NIEZGODNOŚĆ" in line for line in ppoz.lines)


# --------------------------------------------------------------------------- #
# Rysunkowa — vector SVG/PDF, measured scale, dimensions, naming (plan §15.3)
# --------------------------------------------------------------------------- #
def test_rysunkowa_svg_is_vector_and_scaled(pzt_draft) -> None:
    svg_bytes = _uri_path(pzt_draft["artifacts"]["rysunkowa_svg"]).read_bytes()
    root = ET.fromstring(svg_bytes)
    paths = root.findall(".//svg:path", _SVG_NS)
    assert len(paths) > 50, "vector drawing must consist of path elements"
    assert root.findall(".//svg:image", _SVG_NS) == [], "no raster <image> allowed"

    # Scale correctness: building A1's long edge is 90 m in the fixture; at the
    # declared 1:500 with dpi=72, 1 SVG user unit = 1 pt → metres = units ×
    # 0.0254 × 500 / 72 (documented in plot_reports.pzt.rysunkowa).
    meta = pzt_draft["rysunkowa_metadata"]
    assert meta["scale_denominator"] == 500 and meta["dpi"] == 72
    group = next(g for g in root.iter() if g.get("id") == "pzt-building-A1")
    path = group.find(".//svg:path", _SVG_NS)
    assert path is not None
    coords = [float(v) for v in re.findall(r"[-0-9.]+", path.get("d", ""))]
    pts = list(zip(coords[::2], coords[1::2], strict=True))
    longest_units = max(
        math.hypot(x2 - x1, y2 - y1)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False)
    )
    measured_m = longest_units * 0.0254 * meta["scale_denominator"] / meta["dpi"]
    assert measured_m == pytest.approx(90.0, rel=0.005)

    # Dimension annotations (wymiary zewnętrzne) present for buildings.
    dim_ids = [g.get("id") for g in root.iter() if (g.get("id") or "").startswith("pzt-dim-")]
    assert meta["dimension_count"] > 0
    assert len(dim_ids) >= meta["dimension_count"]  # arrow + text per edge


def test_rysunkowa_pdf_is_vector_and_named_per_convention(pzt_draft) -> None:
    pdf_bytes = _uri_path(pzt_draft["artifacts"]["rysunkowa_pdf"]).read_bytes()
    assert pdf_bytes[:5] == b"%PDF-"
    # No whole-canvas raster: a vector PDF of this drawing stays far below the
    # 5 MB heuristic AND contains no raster XObject (/Subtype /Image — the bare
    # /ImageB/C/I tokens are the standard ProcSet declaration, not an image).
    assert len(pdf_bytes) < 5 * 1024 * 1024
    assert b"/Subtype /Image" not in pdf_bytes
    # File naming per the e-form załącznik convention: PZT + rrrr.mm.dd + suffix.
    assert re.fullmatch(_PZT_FILENAME_RE, pzt_draft["pdf_filename"])
    assert pzt_draft["pdf_filename"].startswith("PZT_")
    # The artifact key starts with the convention stem and carries the variant
    # slug (review F1: keys must not collide across variants of the same day).
    stored_name = pzt_draft["artifacts"]["rysunkowa_pdf"].rsplit("/", 1)[-1]
    assert stored_name.startswith(pzt_draft["pdf_filename"].removesuffix(".pdf"))
    assert stored_name.endswith(".pdf")
    assert pzt_draft["variant_id"].replace(":", "-") in stored_name


def test_pzt_artifact_keys_distinct_per_variant_same_day(pzt_env) -> None:
    """Review F1: two variants of the SAME analysis on the SAME day must land
    under DISTINCT artifact keys — ``LocalArtifactStore.put`` silently
    overwrites, so a shared ``PZT_{date}_{aid}`` stem would make the second
    draft destroy the first while the first response's links serve wrong bytes.
    """
    usecases = pzt_env["usecases"]
    rep1 = usecases.report_generate(None, "pzt-draft", pzt_env["out"]["variant_id"])
    assert rep1["status"] == "rendered"
    svg1_path = _uri_path(rep1["artifacts"]["rysunkowa_svg"])
    svg1_bytes = svg1_path.read_bytes()

    out2 = usecases.propose_layout_render(
        riverside_masterplan_payload(flawed=False),
        riverside_e2e_indicators(),
        "drugi wariant tego samego dnia (regresja F1)",
    )
    assert out2["accepted"] is True
    assert out2["variant_id"] != pzt_env["out"]["variant_id"]
    rep2 = usecases.report_generate(None, "pzt-draft", out2["variant_id"])
    assert rep2["status"] == "rendered"

    # ALL five artifact keys (and resource links) differ between the variants.
    assert set(rep1["artifacts"]) == set(rep2["artifacts"])
    for name in rep1["artifacts"]:
        assert rep1["artifacts"][name] != rep2["artifacts"][name], name
        assert rep1["resource_links"][name] != rep2["resource_links"][name], name
        # The resource link's file name matches the stored key's file name.
        assert (
            rep2["resource_links"][name].rsplit("/", 1)[-1]
            == rep2["artifacts"][name].rsplit("/", 1)[-1]
        )

    # Both drafts stay retrievable with the CORRECT per-variant contents.
    op1 = json.loads(_uri_path(rep1["artifacts"]["opisowa_json"]).read_bytes())
    op2 = json.loads(_uri_path(rep2["artifacts"]["opisowa_json"]).read_bytes())
    assert op1["variant_id"] == pzt_env["out"]["variant_id"]
    assert op2["variant_id"] == out2["variant_id"]
    # The first variant's drawing bytes survived the second generation.
    assert svg1_path.read_bytes() == svg1_bytes
    # The response's pdf_filename keeps the pure e-form convention name.
    assert re.fullmatch(_PZT_FILENAME_RE, rep2["pdf_filename"])


def test_collinear_edges_merged_in_wymiary() -> None:
    """Review F3: a building drawn as two touching rectangles has collinear ring
    vertices — the 90 m elevation must carry ONE 90 m dimension, not 40+50."""
    import matplotlib.pyplot as plt
    from plot_reports.pzt.rysunkowa import _draw_dimensions
    from shapely.geometry import box
    from shapely.ops import unary_union

    footprint = unary_union([box(0, 0, 40, 16), box(40, 0, 90, 16)])
    # Precondition: the union ring really keeps the collinear x=40 vertices
    # (otherwise this fixture would not exercise the merge).
    assert len(footprint.exterior.coords) > 5
    fig, ax = plt.subplots()
    try:
        count = _draw_dimensions(ax, "T", footprint)
        values = sorted(
            float(t.get_text())
            for t in ax.texts
            if (t.get_gid() or "").startswith("pzt-dim-T-")
            and (t.get_gid() or "").endswith("-text")
        )
    finally:
        plt.close(fig)
    # One dimension per merged side: 2× 90 m (long) + 2× 16 m (short).
    assert count == 4
    assert values == [16.0, 16.0, 90.0, 90.0]


def test_huge_parcel_falls_back_to_next_standard_scale() -> None:
    """Review F4: a ~2.6 km parcel at 1:500 → >14 400 pt (200 in) page, above the
    PDF viewer MediaBox limit — the drawing must fall back to 1:1000 and record
    the adjustment in the metadata and the checklist."""
    from datetime import date as _date

    from plot_domain import BuildingRecord, MasterplanVariant
    from plot_reports import build_pzt_checklist, build_pzt_opisowa, render_pzt_rysunkowa
    from shapely.geometry import box, mapping

    parcel = box(0.0, 0.0, 2600.0, 400.0)
    variant = MasterplanVariant(
        id="mvar:huge",
        analysis_id="a-huge",
        buildings=[
            BuildingRecord(
                id="b:1",
                name="A1",
                geometry=mapping(box(100.0, 100.0, 190.0, 116.0)),
                floors_by_segment=[4],
            )
        ],
    )
    drawing = render_pzt_rysunkowa(
        variant, parcel, analysis_id="a-huge", generated_on=_date(2026, 6, 12)
    )
    meta = drawing.metadata
    assert meta["scale_adjusted"] is True
    assert meta["requested_scale_denominator"] == 500
    assert meta["scale_denominator"] == 1000  # next standard scale
    assert max(meta["figsize_in"]) <= 200.0  # page within the MediaBox limit
    assert any("skala" in n for n in meta["notes"])  # honest note on the sheet
    # The checklist's §17 scale item names the ADJUSTED scale + the reason.
    opisowa = build_pzt_opisowa(
        analysis_id="a-huge", variant=variant, generated_at="2026-06-12T00:00:00"
    )
    checklist = build_pzt_checklist(opisowa, meta)
    scale_item = next(
        i for i in checklist["items"] if i["item"].startswith("skala rysunku")
    )
    assert "1:1000" in scale_item["item"]
    assert "dostosowan" in scale_item["reason"]


def test_normal_parcel_keeps_declared_scale(pzt_draft) -> None:
    """Review F4 (control): the riverside parcel fits at 1:500 — no adjustment."""
    meta = pzt_draft["rysunkowa_metadata"]
    assert meta["scale_denominator"] == 500
    assert meta["scale_adjusted"] is False
    assert meta["requested_scale_denominator"] == 500


def test_rysunkowa_honest_omissions_without_data(pzt_draft) -> None:
    meta = pzt_draft["rysunkowa_metadata"]
    assert meta["spot_elevation_count"] == 0  # no NMT attached → nothing invented
    assert meta["network_count"] == 0  # no GESUT → no sieci drawn
    notes = " ".join(meta["notes"])
    assert "rzędne terenu: brak danych NMT" in notes
    assert "sieci/przyłącza: brak danych GESUT" in notes
    assert "linia zabudowy: brak danych" in notes


# --------------------------------------------------------------------------- #
# Checklist — §13–18 semantics + the rzędne honesty flip (plan §15.3)
# --------------------------------------------------------------------------- #
def test_checklist_statuses_and_statutory_items(pzt_draft) -> None:
    from plot_reports.pzt import CHECKLIST_STATUSES

    checklist = pzt_draft["checklist"]
    items = checklist["items"]
    assert all(i["status"] in CHECKLIST_STATUSES for i in items)
    assert sum(checklist["summary"].values()) == len(items)
    assert checklist["is_projekt_budowlany"] is False

    by_item = {i["item"]: i for i in items}
    # Statutory duties are NEVER claimed done by the tool.
    mapa = by_item["aktualna mapa do celów projektowych jako podkład"]
    assert mapa["status"] == "requires_uprawnienia"
    obszar = by_item["obszar oddziaływania obiektu"]
    assert obszar["status"] == "requires_projektant"
    podpis = by_item[
        "podpis kwalifikowany/zaufany/osobisty + uprawnienia projektanta"
    ]
    assert podpis["status"] == "requires_uprawnienia"
    # Data gaps are explicit `missing`, not dropped.
    rzedne = by_item["rzędne terenu (ukształtowanie)"]
    assert rzedne["status"] == "missing" and "NMT" in rzedne["reason"]
    sieci = by_item["sieci uzbrojenia terenu / przyłącza (GESUT)"]
    assert sieci["status"] == "missing"
    # The tool-produced content is done (scale, legend, dimensions, kondygnacje).
    assert by_item["oznaczenia graficzne i legenda"]["status"] == "done"
    assert by_item["obiekty budowlane z wymiarami zewnętrznymi"]["status"] == "done"
    assert by_item["liczba kondygnacji budynków"]["status"] == "done"
    assert by_item["skala rysunku (1:500)"]["status"] == "done"
    # §14 rows mirror the opisowa section statuses 1:1.
    section_rows = [i for i in items if i["section"] == "§14"]
    assert len(section_rows) == len(pzt_draft["opisowa_sections"])


def test_rzedne_flip_with_terrain_attached_and_removed(pzt_env) -> None:
    """Attach NMT → rzędne done + spot elevations drawn; remove → missing again."""
    from plot_agent.analysis import DEFAULT_SITE_CONTEXT_STORE
    from plot_planning.site_context import SiteContext, analyze_terrain

    aid = pzt_env["aid"]
    dem = synthetic_dem_bytes(
        west=X0 - 30, north=Y0 + 220, width=470, height=280,
        base_elevation=120.0, east_slope_pct=1.0,
    )
    terrain = analyze_terrain(dem, pzt_env["parcel"])
    assert terrain.status == "ok"
    DEFAULT_SITE_CONTEXT_STORE.put(aid, SiteContext(analysis_id=aid, terrain=terrain))
    try:
        rep = pzt_env["usecases"].report_generate(
            None, "pzt-draft", pzt_env["out"]["variant_id"]
        )
        meta = rep["rysunkowa_metadata"]
        assert meta["spot_elevation_count"] > 0  # parcel + building corners sampled
        assert not any("rzędne terenu: brak danych" in n for n in meta["notes"])
        rzedne = next(
            i for i in rep["checklist"]["items"]
            if i["item"] == "rzędne terenu (ukształtowanie)"
        )
        assert rzedne["status"] == "done"
        # Real elevations from the synthetic DEM (~120 m band), drawn in the SVG.
        svg = _uri_path(rep["artifacts"]["rysunkowa_svg"]).read_bytes()
        root = ET.fromstring(svg)
        rzedne_ids = [
            g.get("id") for g in root.iter()
            if (g.get("id") or "").startswith("pzt-rzedna-")
        ]
        assert len(rzedne_ids) == meta["spot_elevation_count"]
    finally:
        DEFAULT_SITE_CONTEXT_STORE._contexts.pop(aid, None)

    # Terrain removed → the item flips BACK to missing (never silently dropped).
    rep2 = pzt_env["usecases"].report_generate(
        None, "pzt-draft", pzt_env["out"]["variant_id"]
    )
    rzedne2 = next(
        i for i in rep2["checklist"]["items"]
        if i["item"] == "rzędne terenu (ukształtowanie)"
    )
    assert rzedne2["status"] == "missing"
    assert rep2["rysunkowa_metadata"]["spot_elevation_count"] == 0


# --------------------------------------------------------------------------- #
# Storeys (Phase 15 Task 1) — DSL floors → basis-marked StoreyRecords
# --------------------------------------------------------------------------- #
def test_variant_storeys_filled_with_basis(pzt_env) -> None:
    from plot_agent.drawing import DEFAULT_VARIANT_STORE

    variant = DEFAULT_VARIANT_STORE.get(pzt_env["out"]["variant_id"])
    a1 = next(b for b in variant.buildings if b.name == "A1")
    # 6-floor building → 6 StoreyRecords, levels 0..5 (plan §15.3).
    assert len(a1.storeys) == 6
    assert [s.level for s in a1.storeys] == list(range(6))
    for storey in a1.storeys:
        assert storey.height_m == pytest.approx(3.3)  # CapacityConfig.floor_height_m
        assert storey.basis["height_m"] == "industry_heuristic"  # basis-marked
        assert storey.basis["area_m2"] == "geometry_measured"
        assert storey.area_m2 == pytest.approx(90.0 * 16.0)  # A1 footprint
        assert storey.lokale == []  # PW-horizon placeholder stays empty

    # Usługi w parterze (A2 has ground_floor_use="uslugowy") → storey 0 uslugowy.
    a2 = next(b for b in variant.buildings if b.name == "A2")
    assert a2.storeys[0].use == "uslugowy"
    assert a2.storeys[1].use == "mieszkalny"

    # Heritage hall (1 kondygnacja, usługowy) → exactly one storey.
    hala = next(b for b in variant.buildings if b.name == "Hala elektrowni")
    assert len(hala.storeys) == 1 and hala.storeys[0].use == "uslugowy"


def test_building_storeys_underground_levels() -> None:
    from plot_agent.drawing import MasterplanProposal
    from plot_planning import building_storeys

    payload = riverside_masterplan_payload(flawed=False)
    payload["buildings"][0]["underground_floors"] = 2
    proposal = MasterplanProposal.model_validate(payload)
    storeys = building_storeys(proposal.buildings[0])
    assert [s.level for s in storeys][:2] == [-2, -1]
    assert all(s.use == "garaz" for s in storeys[:2])
    assert len(storeys) == 2 + 6


def test_lokal_record_is_pw_placeholder() -> None:
    from plot_domain import LokalRecord, StoreyRecord

    lokal = LokalRecord(id="lok:1", storey_level=0, use="mieszkanie", area_m2=48.5)
    storey = StoreyRecord(level=0, use="mieszkalny", lokale=[lokal])
    assert storey.lokale[0].area_m2 == 48.5
    # Default stays empty — nothing in v2 invents lokale.
    assert StoreyRecord(level=1, use="mieszkalny").lokale == []


# --------------------------------------------------------------------------- #
# Disclaimer — mandatory + prominent (anti-pattern §15.4, test-enforced)
# --------------------------------------------------------------------------- #
def test_disclaimer_present_and_prominent_everywhere(pzt_draft) -> None:
    from plot_reports import PZT_DISCLAIMER

    assert "NIE JEST projektem budowlanym" in PZT_DISCLAIMER
    # Tool result carries it top-level.
    assert pzt_draft["disclaimer"] == PZT_DISCLAIMER
    assert pzt_draft["is_projekt_budowlany"] is False
    # Opisowa MD: prominent — bold, within the first lines of the document.
    md = _uri_path(pzt_draft["artifacts"]["opisowa_md"]).read_text(encoding="utf-8")
    head = "\n".join(md.splitlines()[:5])
    assert PZT_DISCLAIMER in head and f"**{PZT_DISCLAIMER}**" in md
    # Checklist JSON artifact carries it too.
    checklist = json.loads(
        _uri_path(pzt_draft["artifacts"]["checklist_json"]).read_bytes()
    )
    assert checklist["disclaimer"] == PZT_DISCLAIMER
    assert checklist["is_projekt_budowlany"] is False


def test_pzt_draft_not_found_without_variant() -> None:
    from plot_mcp_server import usecases

    rep = usecases.report_generate("no-such-analysis", "pzt-draft", "mvar:missing")
    assert rep["status"] == "not_found"
    assert "propose_layout" in rep["note"]
