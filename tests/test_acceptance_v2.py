"""Phase 16 — the v2 full-version acceptance gate (§18.3 + v2 plan PHASE 16.2).

``test_full_version_gate`` is the CANONICAL recorded session: one executable
test that replays, over the REAL in-memory MCP server (zero network, mocked
sources), the exact workflow the user runs live in Claude Code on the
riverside corpus member:

 1. ``parcel_analyze`` in **full_due_diligence** (mock ULDK returns the ~5 ha
    riverside parcel; synthetic DEM terrain) → stored result with envelope;
 2. ``planning_parse_document`` → indicators WITH verbatim citations and
    §25.1 confidence components (the parse demonstrates the evidence channel;
    the session then pins the corpus indicators for determinism);
 3. ``capacity_generate_scenarios`` → the BASE-scenario PUM target;
 4. design brief generated and read back through the
    ``analysis://{id}/design-brief`` resource;
 5. ``propose_layout`` iteration 1 (the HAND-WRITTEN flawed corpus plan, bound
    to the analysis) → rejected with a critique citing rule ids + capacity gap;
 6. ``propose_layout`` iteration 2 (the hand-written fix) → valid, accepted,
    ZERO hard violations;
 7. ``report_generate`` koncepcja (Markdown + plan render PNG) and the SAME
    model as **html** and the §10.7 **json** contract; **pdf** answers
    honestly (rendered or pdf_unavailable);
 8. ``export_layers`` → **dxf + ifc + geojson** artifacts;
 9. ``report_generate(format="pzt-draft")`` → the §13–18 checklist + mandatory
    draft-not-projekt-budowlany disclaimer;
10. **ruleset edit without a code change**: a tmp copy of ``rulesets/PL`` with
    one YAML threshold edited flips a rule outcome through the same loader and
    changes the content-hash ruleset version (§18.3);
11. **reproducibility**: the same stored analysis renders to the SAME
    ``analysis_snapshot_hash`` before and after everything else;
12. **audit completeness**: both design iterations are in the masterplan audit
    with inputs-hash, rationale and the accept/reject gate decisions.

Every step asserts; a failure names the workflow step that broke the gate.
"""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import mcp.types as types
import pytest
import yaml
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl
from tests.corpus import fixture_riverside_irregular
from tests.masterplan_fixtures import (
    riverside_context_edges,
    riverside_e2e_indicators,
    riverside_heritage_footprints,
    riverside_masterplan_payload,
    riverside_parcel,
)
from tests.mocks import mock_connectors

pytest.importorskip("rasterio")

from tests.site_fixtures import synthetic_dem_bytes  # noqa: E402
from tests.wt_fixtures import X0, Y0  # noqa: E402

RULE_WT13 = "PL-WT-13-PRZESLANIANIE-001"
RULE_PPOZ_271 = "PL-PPOZ-271-273-FIRE-SEPARATION-001"

RATIONALE_1 = "Iteracja 1: pasma wschód-zachód; B1 dosunięty do A1; 4 kondygnacje."
RATIONALE_2 = (
    "Iteracja 2: B1 odsunięty na 40 m (par. 13 + par. 271), 6 kondygnacji — "
    "domyka lukę chłonności do scenariusza bazowego."
)

#: A synthetic uchwała excerpt the deterministic parser can cite verbatim.
PLAN_TEXT = (
    "Uchwala nr X/2026 w sprawie MPZP. Dla terenu MW ustala sie: "
    "maksymalna wysokosc zabudowy: 25 m; "
    "minimalny udzial powierzchni biologicznie czynnej: 25%."
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _load_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    import plot_mcp_server.runtime as runtime
    import plot_mcp_server.server as server

    importlib.reload(runtime)
    server = importlib.reload(server)
    return server.mcp, runtime


def _riverside_dem() -> bytes:
    # 500 × 500 m at 2 m/px around the riverside parcel (X0..X0+351, Y0-24..Y0+181).
    return synthetic_dem_bytes(
        west=X0 - 60, north=Y0 + 250, width=250, height=250, res_m=2.0
    )


@pytest.mark.anyio
async def test_full_version_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mcp_server, runtime = _load_server(monkeypatch)
    usecases = runtime._usecases_module
    usecases.set_connectors(
        mock_connectors(parcel_wkt=riverside_parcel().wkt, terrain_raster=_riverside_dem())
    )
    indicators = riverside_e2e_indicators()
    corpus = fixture_riverside_irregular()

    from plot_agent.drawing import DEFAULT_MASTERPLAN_AUDIT

    DEFAULT_MASTERPLAN_AUDIT.clear()
    try:
        async with create_connected_server_and_client_session(mcp_server) as client:
            # -- 1. full due diligence over mocked sources -------------------- #
            analyze = await client.call_tool(
                "parcel_analyze",
                {
                    "input": {"parcel_id": "acc-riverside"},
                    "analysis_mode": "full_due_diligence",
                    "investment_goal": {"type": "multifamily"},
                },
            )
            assert analyze.isError is False, "step 1: full_due_diligence failed"
            full = analyze.structuredContent
            aid = full["analysis_id"]
            assert full["status"] in ("complete", "partial")
            assert full["buildable_envelope"] is not None
            assert full["scores"] is not None, "step 1: §14 scores missing in full DD"

            # -- 11a. reproducibility baseline hash --------------------------- #
            md_first = await client.call_tool(
                "report_generate", {"analysis_id": aid, "format": "md"}
            )
            hash_before = md_first.structuredContent["analysis_snapshot_hash"]
            assert hash_before and len(hash_before) == 64

            # -- 2. planning parse with citations ----------------------------- #
            parsed = await client.call_tool(
                "planning_parse_document", {"text": PLAN_TEXT}
            )
            assert parsed.isError is False, "step 2: planning parse failed"
            doc = parsed.structuredContent
            assert doc["status"] == "parsed"
            assert doc["indicators"], "step 2: no indicators extracted"
            for indicator, evidence in zip(
                doc["indicators"], doc["evidence"], strict=True
            ):
                fragment = evidence["value_json"]["fragment"]["text"]
                assert fragment in PLAN_TEXT, "step 2: citation must be verbatim"
                assert indicator["confidence_components"]["band"] in (
                    "high",
                    "moderate",
                    "low",
                    "hint",
                ), "step 2: §25.1 components missing"

            # -- 3. capacity scenarios → base PUM target ---------------------- #
            cap = await client.call_tool(
                "capacity_generate_scenarios",
                {"analysis_id": aid, "indicators": indicators},
            )
            assert cap.structuredContent["status"] == "computed", "step 3"
            base = next(
                s
                for s in cap.structuredContent["scenarios"]
                if s["scenario_type"] == "base"
            )
            target_pum = float(base["metrics"]["pum_m2"])
            lo, hi = corpus.expected.pum_m2
            assert lo <= target_pum <= hi, "step 3: base PUM outside the corpus range"

            # -- 4. design brief resource ------------------------------------- #
            brief_out = usecases.design_brief_for_analysis(
                aid,
                context_edges=riverside_context_edges(),
                heritage_footprints=riverside_heritage_footprints(),
            )
            assert brief_out["status"] == "ok", "step 4: brief generation failed"
            res = await client.read_resource(AnyUrl(f"analysis://{aid}/design-brief"))
            brief_md = res.contents[0].text  # type: ignore[union-attr]
            assert "Hala elektrowni" in brief_md, "step 4: heritage missing from brief"

            # -- 5. iteration 1: flawed corpus plan → cited critique ----------- #
            it1 = await client.call_tool(
                "propose_layout",
                {
                    "proposal": riverside_masterplan_payload(flawed=True),
                    "indicators": indicators,
                    "rationale": RATIONALE_1,
                    "analysis_id": aid,
                },
            )
            assert it1.isError is False, "step 5: propose_layout failed"
            sc1 = it1.structuredContent
            assert sc1["valid"] is False and sc1["accepted"] is False, "step 5"
            assert any(isinstance(c, types.ImageContent) for c in it1.content)
            fail_ids = {
                f["rule_id"]
                for f in sc1["critique"]["rule_findings"]
                if f["status"] == "fail"
            }
            assert {RULE_WT13, RULE_PPOZ_271} <= fail_ids, "step 5: critique must cite rules"
            assert sc1["critique"]["capacity"]["within_target_tolerance"] is False
            assert sc1["analysis_id"] == aid, "step 5: iteration not analysis-bound"

            # -- 6. iteration 2: hand-written fix → zero hard violations ------- #
            it2 = await client.call_tool(
                "propose_layout",
                {
                    "proposal": riverside_masterplan_payload(flawed=False),
                    "indicators": indicators,
                    "rationale": RATIONALE_2,
                    "analysis_id": aid,
                },
            )
            assert it2.isError is False, "step 6: propose_layout failed"
            sc2 = it2.structuredContent
            assert sc2["valid"] is True and sc2["accepted"] is True, "step 6"
            assert sc2["violations"] == [], "step 6: hard violations must be zero"
            assert all(
                c["status"] != "fail" for c in sc2["inter_building_checks"]
            ), "step 6: zero failing WT/ppoż checks required"
            pum = float(sc2["metrics"]["totals"]["pum_m2"])
            assert abs(pum - target_pum) / target_pum <= 0.10, "step 6: ±10% PUM"
            variant_id = sc2["variant_id"]

            # -- 7. koncepcja report in every format + render ------------------ #
            kon = await client.call_tool(
                "report_generate",
                {"analysis_id": aid, "format": "koncepcja", "variant_id": variant_id},
            )
            assert kon.structuredContent["status"] == "rendered", "step 7: koncepcja"
            md = kon.structuredContent["content"]
            assert "fail: 0" in md and RATIONALE_2 in md
            from plot_reports import get_artifact_store

            png_key = f"analysis/{aid}/koncepcja-{variant_id.replace(':', '-')}.png"
            assert get_artifact_store().get(png_key)[:8] == b"\x89PNG\r\n\x1a\n", (
                "step 7: plan render PNG missing"
            )
            html = await client.call_tool(
                "report_generate",
                {"analysis_id": aid, "format": "html", "variant_id": variant_id},
            )
            assert html.structuredContent["status"] == "rendered", "step 7: html"
            assert html.structuredContent["resource_link"], "step 7: html artifact"
            js = await client.call_tool(
                "report_generate", {"analysis_id": aid, "format": "json"}
            )
            assert js.structuredContent["status"] == "rendered", "step 7: json"
            assert js.structuredContent["content"]["analysis_id"] == aid
            pdf = await client.call_tool(
                "report_generate",
                {"analysis_id": aid, "format": "pdf", "variant_id": variant_id},
            )
            assert pdf.structuredContent["status"] in ("rendered", "pdf_unavailable"), (
                "step 7: pdf must answer honestly"
            )

            # -- 8. exports: dxf + ifc + geojson -------------------------------- #
            for fmt in ("dxf", "ifc", "geojson"):
                export = await client.call_tool(
                    "export_layers",
                    {"analysis_id": aid, "format": fmt, "variant_id": variant_id},
                )
                out = export.structuredContent
                assert out["status"] in ("exported", "rendered"), f"step 8: {fmt}"
                assert out["artifact_uri"], f"step 8: {fmt} artifact missing"

            # -- 9. PZT draft with checklist ----------------------------------- #
            pzt = await client.call_tool(
                "report_generate",
                {"analysis_id": aid, "format": "pzt-draft", "variant_id": variant_id},
            )
            out = pzt.structuredContent
            assert out["status"] == "rendered", "step 9: pzt-draft"
            assert out["is_projekt_budowlany"] is False and out["disclaimer"], "step 9"
            assert out["checklist"]["items"], "step 9: §13–18 checklist missing"
            assert out["artifacts"]["rysunkowa_pdf"], "step 9: rysunkowa missing"

            # -- 10. ruleset edit WITHOUT a code change (tmp override) ---------- #
            from plot_rules import RuleStatus, evaluate, load_rulesets

            repo_rules = Path(__file__).resolve().parents[1] / "rulesets" / "PL"
            tmp_rules = tmp_path / "rulesets-PL"
            shutil.copytree(repo_rules, tmp_rules)
            wt12_path = tmp_rules / "building-technical" / "wt-12-setbacks.yaml"
            doc12 = yaml.safe_load(wt12_path.read_text(encoding="utf-8"))
            assert doc12["thresholds"]["setback_windows_m"] == 4.0
            doc12["thresholds"]["setback_windows_m"] = 6.0  # the "amendment"
            wt12_path.write_text(
                yaml.safe_dump(doc12, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            original = load_rulesets(repo_rules)
            edited = load_rulesets(tmp_rules)
            assert edited.ruleset_version != original.ruleset_version, "step 10"
            inputs = {
                "wall_has_windows_or_doors": True,
                "is_multifamily_over_4_storeys": False,
                "distance_to_boundary_m": 5.0,
                "mpzp_allows_reduced_setback": False,
            }
            assert evaluate(original.get("PL-WT-12-SETBACKS-001"), inputs).status is RuleStatus.PASS
            assert evaluate(edited.get("PL-WT-12-SETBACKS-001"), inputs).status is RuleStatus.FAIL, (
                "step 10: the YAML-only edit must flip the outcome (no code change)"
            )

            # -- 11. reproducibility: same input → same snapshot hash ----------- #
            md_again = await client.call_tool(
                "report_generate", {"analysis_id": aid, "format": "md"}
            )
            assert (
                md_again.structuredContent["analysis_snapshot_hash"] == hash_before
            ), "step 11: stored analysis must hash identically (§31)"

            # -- 12. audit completeness ----------------------------------------- #
            audits = DEFAULT_MASTERPLAN_AUDIT.entries(analysis_id=aid)
            assert len(audits) == 2, "step 12: every iteration must be audited"
            assert [a["rationale"] for a in audits] == [RATIONALE_1, RATIONALE_2]
            assert [a["accepted"] for a in audits] == [False, True], (
                "step 12: gate decisions (reject→accept) must be in the audit"
            )
            assert all(len(a["inputs_hash"]) == 64 for a in audits)
            assert audits[0]["inputs_hash"] != audits[1]["inputs_hash"]
            # The accepted iteration's variant is the one every deliverable used.
            assert audits[1]["variant_id"] == variant_id
    finally:
        usecases.set_connectors(None)
        DEFAULT_MASTERPLAN_AUDIT.clear()
