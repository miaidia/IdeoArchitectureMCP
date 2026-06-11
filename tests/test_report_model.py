"""Phase 14 part A — unified ReportModel tests (§31 DoD; F-0392/0398/0407/0413/0414).

* ONE model → MD/HTML/JSON with byte-equal formatted numbers (PUM totals
  extracted from each format and compared — §31 "dane liczbowe są spójne").
* Audience variants differ in SECTIONS, never in numbers (F-0392).
* ``compare_reports`` detects a changed PUM and changed decisions (F-0398/0413).
* Evidence pack artifact contains every SourceRecord id + EvidenceItem id of the
  analysis and is downloadable independently (F-0393/F-0407).
* PDF: rendered from the same HTML when weasyprint's system stack exists; an
  HONEST ``pdf_unavailable`` otherwise (guarded import — never a fake PDF).

ZERO network: the screening result comes from the orchestrator over the pure
mock bundle; the koncepcja model is built over a hand-written MasterplanVariant.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from plot_domain import BuildingRecord, MasterplanVariant
from plot_reports import (
    ReportModel,
    build_koncepcja_model,
    build_screening_model,
    compare_reports,
    pdf_available,
    render_koncepcja_markdown,
    render_model_html,
    render_model_json,
    render_model_markdown,
    render_model_pdf,
)
from shapely.geometry import Polygon, mapping

# --------------------------------------------------------------------------- #
# Hand-written variant (deterministic numbers; no recomputation anywhere)
# --------------------------------------------------------------------------- #
_PUM = 38_660.0
_PUU = 4_120.0
_PU = 42_780.0


def _variant(pum: float = _PUM) -> MasterplanVariant:
    b1 = Polygon([(500030, 250010), (500120, 250010), (500120, 250026), (500030, 250026)])
    b2 = Polygon([(500030, 250074), (500082, 250074), (500082, 250090), (500030, 250090)])
    return MasterplanVariant(
        id="mvar:golden01",
        analysis_id="a-model",
        buildings=[
            BuildingRecord(
                id="b1", name="A1", geometry=dict(mapping(b1)),
                floors_by_segment=[6], uses=["mieszkalny"], stage=1,
                status="projektowany",
                metrics={"pum_m2": pum - 2_000.0, "powierzchnia_zabudowy_m2": 1_440.0,
                         "mieszkania_estimate": 700},
            ),
            BuildingRecord(
                id="b2", name="Hala", geometry=dict(mapping(b2)),
                floors_by_segment=[2], uses=["uslugowy"], stage=2,
                status="zabytek_do_remontu",
                metrics={"pum_m2": 2_000.0, "puu_m2": _PUU,
                         "powierzchnia_zabudowy_m2": 832.0, "mieszkania_estimate": 40},
            ),
        ],
        totals={
            "buildings": 2, "mieszkania_estimate": 740,
            "pum_m2": pum, "puu_m2": _PUU, "pu_m2": _PU,
            "powierzchnia_zabudowy_m2": 2_272.0,
            "coverage_ratio": 0.05, "intensywnosc": 0.86,
        },
        stage_table=[
            {"etap": 1, "liczba_mieszkan": 700, "pum_m2": pum - 2_000.0,
             "puu_m2": 0.0, "pu_m2": pum - 2_000.0},
            {"etap": 2, "liczba_mieszkan": 40, "pum_m2": 2_000.0,
             "puu_m2": _PUU, "pu_m2": 2_000.0 + _PUU},
            {"etap": "SUMA", "liczba_mieszkan": 740, "pum_m2": pum,
             "puu_m2": _PUU, "pu_m2": _PU},
        ],
        metadata={
            "ruleset_version": "rs-test-1",
            "config_basis": {"basis": "industry_heuristic", "pum_efficiency": 0.7,
                             "avg_mieszkanie_m2": 52.0, "floor_height_m": 3.3,
                             "measurement_standard": "PN-ISO 9836 (aproksymacja)"},
            "inter_building_checks": [
                {"rule_id": "PL-WT-12-SETBACKS-001", "status": "pass"},
                {"rule_id": "PL-WT-13-PRZESLANIANIE-001", "status": "unknown"},
            ],
            "staging_checks": [{"status": "pass", "message": "etap 1 ma dojazd"}],
            "unknowns": [
                {"severity": "medium", "topic": "parking_norm",
                 "reason": "brak wskaźnika w MPZP",
                 "suggested_action": "Potwierdzić normę parkingową w gminie."}
            ],
        },
    )


def _model(audience: str = "architect", pum: float = _PUM) -> ReportModel:
    return build_koncepcja_model(
        analysis_id="a-model",
        variant=_variant(pum),
        generated_at="2026-06-10T12:00:00+00:00",
        rationales=[{"timestamp": "t1", "rationale": "Pasma wzdłuż osi.", "accepted": True}],
        unknowns=list(_variant().metadata["unknowns"]),
        capacity={"message": "PUM 38 660 z ~38 660 osiągalne (luka 0%)."},
        plan_png_resource="file:///tmp/plan.png",
        audience=audience,  # type: ignore[arg-type]
    )


_PUM_MD_RE = re.compile(r"PUM: \*\*([\d ]+) m²\*\*")
_PUM_HTML_RE = re.compile(r"PUM: <strong>([\d ]+) m²</strong>")


# --------------------------------------------------------------------------- #
# §31 — one model, consistent numbers across MD/HTML/JSON
# --------------------------------------------------------------------------- #
def test_md_html_json_numbers_byte_equal() -> None:
    model = _model()
    md = render_model_markdown(model)
    html = render_model_html(model)
    js = render_model_json(model)

    pum_md = _PUM_MD_RE.search(md)
    pum_html = _PUM_HTML_RE.search(html)
    assert pum_md and pum_html
    # The same formatted string in MD and HTML (shared formatter — §31)…
    assert pum_md.group(1) == pum_html.group(1) == "38 660"
    # …and it equals the JSON raw number formatted the same way.
    assert float(js["totals"]["pum_m2"]) == _PUM
    # SUMA row identical in JSON and rendered in both formats.
    suma = next(r for r in js["stage_table"] if r["etap"] == "SUMA")
    assert suma["pum_m2"] == _PUM and suma["liczba_mieszkan"] == 740
    assert "| **SUMA** | 740 | 38 660 " in md
    assert "<td>SUMA</td><td>740</td><td>38 660</td>" in html


def test_model_md_byte_compatible_with_phase11_renderer() -> None:
    """The koncepcja wrapper (build model → render) is the public API — its
    architect output must keep the exact nine-section shape (review F2 contract)."""
    variant = _variant()
    md = render_koncepcja_markdown(
        analysis_id="a-model",
        variant=variant,
        generated_at="2026-06-10T12:00:00+00:00",
    )
    h2 = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert len(h2) == 9
    assert h2[0].startswith("## 1. Założenia z design briefu")
    assert h2[4].startswith("## 5. Zgodność WT/ppoż")
    assert h2[8].startswith("## 9. Założenia i zastrzeżenia")


def test_html_is_selfcontained_and_has_title_block() -> None:
    model = _model()
    html = render_model_html(model)
    # no external assets (deterministic single file).
    assert "http://" not in html and "https://" not in html
    assert "<style>" in html
    # the chłonność deliverable title block: investor honestly absent, date, variant.
    assert "Inwestor" in html and "— (nie podano)" in html
    assert "2026-06-10T12:00:00+00:00" in html
    assert "mvar:golden01" in html
    # 4-status legend note next to the plan render reference.
    assert "Legenda planu (4 statusy)" in html
    # questions-for-gmina annex present.
    assert "Pytania do gminy / urzędu:" in html


# --------------------------------------------------------------------------- #
# F-0392 — audience variants: different sections, same numbers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("audience", ["investor", "lawyer", "bank"])
def test_audience_variants_differ_in_sections_not_numbers(audience: str) -> None:
    architect = _model("architect")
    other = _model(audience)
    assert architect.sections != other.sections

    md_a = render_model_markdown(architect)
    md_o = render_model_markdown(other)
    # Sections differ: the architect report carries the rationale section…
    assert "Uzasadnienie projektowe" in md_a
    assert "Uzasadnienie projektowe" not in md_o
    # …non-architect audiences get the executive summary section instead.
    assert "Synteza (executive summary)" in md_o

    # SAME numbers wherever the totals appear (lawyer drops the headline section,
    # but its executive summary still carries the identical formatted PUM).
    assert "38 660" in md_a and "38 660" in md_o
    assert render_model_json(architect)["totals"] == render_model_json(other)["totals"]


def test_unknown_audience_rejected() -> None:
    with pytest.raises(ValueError, match="grupa odbiorców"):
        _model("ceo")


# --------------------------------------------------------------------------- #
# F-0398 / F-0413 / F-0414 — versioning + comparison
# --------------------------------------------------------------------------- #
def test_model_carries_versioning_and_snapshot_hash() -> None:
    model = _model()
    assert model.report_version
    assert model.ruleset_version == "rs-test-1"
    assert model.analysis_snapshot_hash and len(model.analysis_snapshot_hash) == 64
    # Reproducible: the same stored inputs → the same snapshot hash (§31).
    assert _model().analysis_snapshot_hash == model.analysis_snapshot_hash


def test_compare_reports_detects_changed_pum() -> None:
    a = _model()
    b = _model(pum=_PUM + 1_000.0)
    diff = compare_reports(a, b)
    assert diff["identical"] is False
    changed_fields = {c["field"] for c in diff["changed"]}
    assert "totals.pum_m2" in changed_fields
    pum_change = next(c for c in diff["changed"] if c["field"] == "totals.pum_m2")
    assert pum_change == {"field": "totals.pum_m2", "a": _PUM, "b": _PUM + 1_000.0}
    # the snapshot hash changes with the numbers (versioned reports, F-0414).
    assert "analysis_snapshot_hash" in changed_fields
    # identical models compare clean — audience is NOT a difference (same numbers).
    same = compare_reports(_model("architect"), _model("investor"))
    assert same["identical"] is True


# --------------------------------------------------------------------------- #
# Screening model — render_markdown wrapper + §22 audience selection
# --------------------------------------------------------------------------- #
@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _screening_result() -> Any:
    from plot_agent.analysis import run_quick_screening
    from plot_domain import AnalysisInput
    from plot_envelope import RiskKind
    from tests.mocks import mock_connectors

    connectors = mock_connectors(
        parcel_wkt="POLYGON((0 0,40 0,40 30,0 30,0 0))",
        feature_geoms={
            RiskKind.FLOOD: [
                {"type": "Polygon",
                 "coordinates": [[[0, 0], [15, 0], [15, 30], [0, 30], [0, 0]]]}
            ]
        },
    )
    inp = AnalysisInput.model_validate(
        {"input": {"parcel_id": "141201_1.0001.1867/2"}, "analysis_mode": "quick_screening"}
    )
    return await run_quick_screening(inp, connectors=connectors, analysis_id="a-pack")


@pytest.mark.anyio
async def test_screening_model_html_numbers_match_md() -> None:
    result = await _screening_result()
    model = build_screening_model(result)
    md = render_model_markdown(model)
    html = render_model_html(model)
    # the same §22 formatted areas appear in both renders (shared formatter).
    assert "1,200 m²" in md and "1,200 m²" in html
    assert "528 m²" in md and "528 m²" in html
    # audience selection over the same §22 blocks (lawyer keeps planning+flags).
    lawyer = build_screening_model(result, audience="lawyer")
    md_lawyer = render_model_markdown(lawyer)
    assert "## Planowanie" in md_lawyer
    assert "## Media i dojazd" not in md_lawyer
    assert "LIKELY_BLOCKED" in md_lawyer  # decision never dropped


# --------------------------------------------------------------------------- #
# F-0393 / F-0407 — evidence pack downloadable independently
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_evidence_pack_artifact_contains_all_sources_and_evidence() -> None:
    from pathlib import Path

    from plot_agent.analysis import DEFAULT_STORE
    from plot_mcp_server import usecases

    result = await _screening_result()
    DEFAULT_STORE.put(result)
    out = usecases.sources_collect("a-pack")
    assert out["evidence_pack_uri"], "evidence pack must be persisted as an artifact"
    assert out["evidence_pack_resource"] == "analysis://a-pack/evidence"

    path = Path(out["evidence_pack_uri"].removeprefix("file://"))
    assert path.exists()
    pack = json.loads(path.read_text(encoding="utf-8"))
    # EVERY SourceRecord id of the analysis is in the pack…
    expected_sources = {s["source_id"] for s in result.planning["_sources"]}
    assert expected_sources
    assert {s["source_id"] for s in pack["sources"]} == expected_sources
    # …and every EvidenceItem id, each with a citation entry (claim + source).
    expected_evidence = {e.id for e in result.evidence}
    assert expected_evidence
    assert {e["id"] for e in pack["evidence"]} == expected_evidence
    assert len(pack["citations"]) == len(expected_evidence)
    assert all(c["claim"] and c["source_id"] for c in pack["citations"])


# --------------------------------------------------------------------------- #
# PDF — same model/HTML when available; honest pdf_unavailable otherwise
# --------------------------------------------------------------------------- #
_PDF_OK, _PDF_REASON = pdf_available()


@pytest.mark.skipif(not _PDF_OK, reason=f"weasyprint unavailable: {_PDF_REASON}")
def test_pdf_renders_from_same_model() -> None:  # pragma: no cover - host-dependent
    pdf = render_model_pdf(_model())
    assert pdf[:5] == b"%PDF-"


@pytest.mark.skipif(
    _PDF_OK, reason="weasyprint importable here — the unavailable path cannot run"
)
def test_pdf_unavailable_is_reported_honestly() -> None:
    from plot_reports import PdfUnavailableError

    with pytest.raises(PdfUnavailableError):
        render_model_pdf(_model())

    from plot_mcp_server import usecases

    out = usecases.report_generate("a-missing-ok", "pdf", "mvar:none", "architect")
    # an unknown variant id is a not_found; the pdf_unavailable path needs a model —
    # exercised through the screening kind below.
    assert out["status"] == "not_found"


@pytest.mark.anyio
@pytest.mark.skipif(
    _PDF_OK, reason="weasyprint importable here — the unavailable path cannot run"
)
async def test_report_generate_pdf_unavailable_result() -> None:
    from plot_agent.analysis import DEFAULT_STORE
    from plot_mcp_server import usecases

    result = await _screening_result()
    DEFAULT_STORE.put(result)
    out = usecases.report_generate("a-pack", "pdf")
    assert out["status"] == "pdf_unavailable"
    assert out["artifact_uri"] is None
    assert "weasyprint" in out["note"] and "html" in out["note"].lower()
    # the model JSON snapshot is still produced (numbers remain auditable).
    assert out["model_json_uri"]
