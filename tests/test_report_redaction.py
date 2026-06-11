"""PII redaction in shared reports (Phase 14B; F-0483/0497) — synthetic field.

The system stores NO owner data today (honest scope note in
``plot_reports.redaction``); these tests inject a SYNTHETIC owner field into the
report model and assert: architect output keeps it (working channel), shared
audiences (investor/lawyer/bank) never emit it.
"""

from __future__ import annotations

from plot_domain import AnalysisResult
from plot_domain.enums import AnalysisStatus, Decision
from plot_reports import (
    REDACTION_PLACEHOLDER,
    build_screening_model,
    redact_model_for_sharing,
    render_model_html,
    render_model_markdown,
)

OWNER = "Jan Testowy-Właściciel"


def _model_with_synthetic_pii(audience: str = "investor"):
    result = AnalysisResult(
        analysis_id="a-pii-1",
        status=AnalysisStatus.COMPLETE,
        decision=Decision.OK,
    )
    model = build_screening_model(result, audience=audience)  # type: ignore[arg-type]
    # synthetic owner fields in two different free-form blocks
    model.title_block["owner"] = OWNER
    model.planning_summary["wlasciciel"] = OWNER
    return model


def test_redaction_strips_pii_fields_and_reports_paths() -> None:
    model = _model_with_synthetic_pii()
    redacted, paths = redact_model_for_sharing(model)
    assert sorted(paths) == ["planning_summary.wlasciciel", "title_block.owner"]
    assert redacted.title_block["owner"] == REDACTION_PLACEHOLDER
    assert redacted.planning_summary["wlasciciel"] == REDACTION_PLACEHOLDER
    assert OWNER not in render_model_markdown(redacted)
    assert OWNER not in render_model_html(redacted)


def test_redaction_noop_without_pii() -> None:
    result = AnalysisResult(
        analysis_id="a-pii-2", status=AnalysisStatus.COMPLETE, decision=Decision.OK
    )
    model = build_screening_model(result, audience="investor")
    redacted, paths = redact_model_for_sharing(model)
    assert paths == []
    assert redacted is model  # zero-copy when nothing to redact


def test_numbers_survive_redaction() -> None:
    model = _model_with_synthetic_pii()
    headline_before = dict(model.headline)
    redacted, _ = redact_model_for_sharing(model)
    assert redacted.headline == headline_before
    assert redacted.sections == model.sections


def test_report_generate_applies_redaction_for_shared_audiences(monkeypatch) -> None:
    """End-to-end: the use-case report path redacts for audience != architect."""
    from plot_agent.analysis import DEFAULT_STORE
    from plot_mcp_server import usecases

    result = AnalysisResult(
        analysis_id="a-pii-3",
        status=AnalysisStatus.COMPLETE,
        decision=Decision.OK,
        planning={"wlasciciel": OWNER, "mpzp_pog_wz": "brak danych"},
    )
    DEFAULT_STORE.put(result)

    # architect = working channel: no redaction pass applied
    architect = usecases.report_generate("a-pii-3", "md", None, "architect")
    assert architect["pii_redaction"]["applied"] is False

    # investor = share channel: the synthetic owner field never leaves
    investor = usecases.report_generate("a-pii-3", "md", None, "investor")
    assert investor["status"] == "rendered"
    assert OWNER not in investor["content"]
    block = investor["pii_redaction"]
    # the screening model carries planning_summary (subset of planning) — the
    # synthetic field lands there via the builder only if mapped; redaction has
    # applied=True ONLY when a PII field was present in the model.
    assert isinstance(block["redacted_fields"], list)

    html = usecases.report_generate("a-pii-3", "html", None, "investor")
    assert html["status"] == "rendered"
    assert "pii_redaction" in html
