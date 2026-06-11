"""Koncepcja (multi-building chłonność concept) deliverable (Phase 11 §11.1.7).

Phase 14 part A refactor (§31 DoD — ONE report model, all formats):
:func:`render_koncepcja_markdown` now BUILDS the unified
:class:`~plot_reports.model.ReportModel` via
:func:`~plot_reports.model.build_koncepcja_model` and renders the Markdown from
it via :func:`~plot_reports.formats.render_model_markdown`. The public API and
the default (``architect``) Markdown output are byte-compatible with Phase 11;
the SAME model also feeds the HTML/JSON/PDF renderers in
:mod:`plot_reports.formats`, so the numbers in every format agree by
construction.

The report content/assembly contract is unchanged (see the section emitters in
``formats.py`` for the line-level documentation):

1.  header — analysis id, generation date, variant id, ruleset version;
2.  brief summary; 3. per-building table; 4. per-stage table (+ SUMA);
5.  WT/ppoż compliance summary; 6. staging summary; 7. design rationale
    (neutralized — Markdown-injection guard F2, NFR-SEC-003);
8.  unknowns + questions-for-gmina annex; 9. disclaimers (PUM heuristic basis).

The plan render PNG itself is produced by :func:`plot_reports.render_masterplan`
(renderer v2 with the stage-table panel) — the MCP use-case layer stores it via
the ArtifactStore and references it from the report.
"""

from __future__ import annotations

from typing import Any

from plot_domain import MasterplanVariant

from plot_reports.formats import render_model_markdown
from plot_reports.model import (
    DISCLAIMER_TOOL,  # noqa: F401  (re-export: single wording convention)
    build_koncepcja_model,
    compliance_summary,
)

__all__ = ["DISCLAIMER_TOOL", "compliance_summary", "render_koncepcja_markdown"]


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

    Phase 14: thin wrapper over ``build_koncepcja_model`` +
    ``render_model_markdown`` (byte-compatible architect output).
    """
    model = build_koncepcja_model(
        analysis_id=analysis_id,
        variant=variant,
        generated_at=generated_at,
        brief=brief,
        rationales=rationales,
        unknowns=unknowns,
        capacity=capacity,
        plan_png_resource=plan_png_resource,
    )
    return render_model_markdown(model)
