"""Unified report model (Phase 14 part A — §31 DoD: ONE model, all formats).

:class:`ReportModel` is assembled ONCE from already-computed data (analysis result
+ masterplan variant + capacity metrics + rule checks + design brief + audit) and
every output format renders FROM IT:

* Markdown — :func:`plot_reports.formats.render_model_markdown` (the existing §22
  screening template and the Phase 11 koncepcja template, byte-compatible for the
  default ``architect`` audience);
* HTML — :func:`plot_reports.formats.render_model_html` (deterministic, no
  external assets — the chłonność deliverable template: title block, legend note,
  stage table, callouts, disclaimers, questions-for-gmina annex);
* JSON — :func:`plot_reports.formats.render_model_json` (the model dump);
* PDF — :func:`plot_reports.formats.render_model_pdf` (weasyprint over the SAME
  HTML; guarded import — ``pdf_unavailable`` when system pango/cairo are absent).

Numbers live in the model exactly once, so MD/HTML/JSON/PDF agree by construction
(§31 "dane liczbowe są spójne z JSON-em"); a test extracts the formatted PUM from
each format and asserts byte-equality.

Audience variants (F-0392, F-0403–F-0406): ``architect | investor | lawyer |
bank`` select DIFFERENT SECTION LISTS over the SAME numbers — the section lists
are config dicts below (:data:`KONCEPCJA_AUDIENCE_SECTIONS`,
:data:`SCREENING_AUDIENCE_SECTIONS`), never divergent computations.

Versioning + comparison (F-0398, F-0413, F-0414): the model carries
``report_version`` / ``ruleset_version`` / ``analysis_snapshot_hash`` (sha256 of
the canonical analysis+variant JSON — §31 "raport można odtworzyć ze snapshotu")
and :func:`compare_reports` diffs the NUMBERS and decisions of two models.

This module must NOT import ``plot_connectors`` / ``plot_rules`` /
``plot_planning`` / ``plot_agent`` (Phase 3 §3.4 decoupling holds).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from plot_domain import AnalysisResult, MasterplanVariant
from pydantic import BaseModel, ConfigDict, Field

#: Bump when the model SHAPE changes (renderer changes bump renderer versions).
REPORT_MODEL_VERSION = "1.0.0"

Audience = Literal["architect", "investor", "lawyer", "bank"]

#: Honesty line shared by every report family (single wording convention, §21/§22).
DISCLAIMER_TOOL = (
    "_Wynik jest narzędziem priorytetyzującym, nie decyzją prawną. "
    "Każda teza ma źródło albo oznaczenie braku źródła (no_source)._"
)

# --------------------------------------------------------------------------- #
# Audience section configs (F-0392): SAME numbers, different section selection.
# The ``architect`` lists reproduce the pre-Phase-14 reports byte-for-byte (the
# Phase 11 koncepcja's nine "## n." sections / the §22 screening sections).
# --------------------------------------------------------------------------- #
KONCEPCJA_AUDIENCE_SECTIONS: dict[str, tuple[str, ...]] = {
    "architect": (
        "brief", "headline", "buildings", "stages", "compliance",
        "staging", "rationale", "unknowns", "disclaimers",
    ),
    "investor": (
        "executive_summary", "headline", "buildings", "stages",
        "staging", "unknowns", "disclaimers",
    ),
    "lawyer": (
        "executive_summary", "compliance", "staging", "unknowns", "disclaimers",
    ),
    "bank": (
        "executive_summary", "headline", "stages", "compliance",
        "unknowns", "disclaimers",
    ),
}

SCREENING_AUDIENCE_SECTIONS: dict[str, tuple[str, ...]] = {
    "architect": (
        "decision", "conclusions", "red_flags", "design_opportunities",
        "confirmations", "parcel_params", "planning", "envelope",
        "media_access", "environment", "capacity", "next_steps", "sources",
    ),
    "investor": (
        "decision", "conclusions", "red_flags", "capacity", "next_steps", "sources",
    ),
    "lawyer": (
        "decision", "planning", "red_flags", "confirmations", "sources",
    ),
    "bank": (
        "decision", "conclusions", "red_flags", "parcel_params", "capacity", "sources",
    ),
}


class ReportModel(BaseModel):
    """The single report model every format renders from (§31 DoD).

    ``kind`` selects the report family: ``screening`` (the §22 single-parcel
    report) or ``koncepcja`` (the Phase 11 multi-building chłonność deliverable).
    Fields not applicable to a family keep their defaults — renderers consult
    ``sections`` (the audience-selected, ordered section keys), never the raw
    field presence.
    """

    model_config = ConfigDict(extra="forbid")

    # --- identity & versioning (F-0414, §31 reproducibility) ---------------- #
    report_version: str = REPORT_MODEL_VERSION
    ruleset_version: str | None = None
    analysis_snapshot_hash: str | None = Field(
        default=None,
        description="sha256 of the canonical analysis+variant JSON snapshot.",
    )
    kind: Literal["screening", "koncepcja"] = "screening"
    audience: Audience = "architect"
    sections: list[str] = Field(default_factory=list)
    analysis_id: str
    generated_at: str
    title_block: dict[str, Any] = Field(
        default_factory=dict,
        description="Chłonność title block: investor (None when not provided — "
        "never invented), date, variant, ruleset (v2 deliverable template).",
    )
    executive_summary: list[str] = Field(default_factory=list)

    # --- screening (§22) blocks --------------------------------------------- #
    decision: str | None = None
    status: str | None = None
    headline: dict[str, Any] = Field(default_factory=dict)
    parcel_display_id: str | None = None
    geometry_metrics: dict[str, Any] = Field(default_factory=dict)
    planning_summary: dict[str, Any] = Field(default_factory=dict)
    layer_status: dict[str, Any] = Field(default_factory=dict)
    envelope: dict[str, Any] = Field(default_factory=dict)
    constraint_types: list[str] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    evidence_count: int = 0

    # --- koncepcja (chłonność) blocks ---------------------------------------- #
    variant_id: str | None = None
    plan_png_resource: str | None = None
    brief: dict[str, Any] | None = None
    totals: dict[str, Any] = Field(default_factory=dict)
    capacity: dict[str, Any] | None = None
    buildings: list[dict[str, Any]] = Field(default_factory=list)
    stage_table: list[dict[str, Any]] = Field(default_factory=list)
    compliance: dict[str, Any] = Field(default_factory=dict)
    staging: list[dict[str, Any]] = Field(default_factory=list)
    rationales: list[dict[str, Any]] = Field(default_factory=list)
    unknowns: list[dict[str, Any]] = Field(default_factory=list)
    missing_indicators: list[Any] = Field(default_factory=list)
    config_basis: dict[str, Any] = Field(default_factory=dict)
    metrics_resource: str | None = None


# --------------------------------------------------------------------------- #
# Compliance summary (moved here from koncepcja.py so koncepcja can import the
# model builders without a cycle; re-exported from plot_reports.koncepcja).
# --------------------------------------------------------------------------- #
def compliance_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass/fail/warning/unknown counts + failing rule ids (plan §11.1.7 item 2).

    Phase 16 (§25.1 threshold policy applied in reports): any decided check whose
    confidence falls below the moderate threshold (0.60 — "niskie zaufanie,
    wymaga potwierdzenia") is flagged for manual review; the rule ids land in
    ``manual_review_rule_ids`` and the renderers surface them. ``not_applicable``
    checks are exempt (nothing was decided to confirm).
    """
    from plot_domain import THRESHOLD_MODERATE

    counts: dict[str, int] = {}
    for check in checks:
        status = str(check.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    failing = sorted(
        {str(c.get("rule_id", "")) for c in checks if str(c.get("status")) == "fail"}
    )
    manual_review = sorted(
        {
            str(c.get("rule_id", ""))
            for c in checks
            if str(c.get("status")) != "not_applicable"
            and float(c.get("confidence", 0.0)) < THRESHOLD_MODERATE
        }
    )
    return {
        "pass": counts.get("pass", 0),
        "fail": counts.get("fail", 0),
        "warning": counts.get("warning", 0),
        "unknown": counts.get("unknown", 0),
        "not_applicable": counts.get("not_applicable", 0),
        "failing_rule_ids": failing,
        # §25.1 policy: confidence < 0.60 → wymaga potwierdzenia człowieka.
        "manual_review_rule_ids": manual_review,
        "manual_review_threshold": THRESHOLD_MODERATE,
    }


def snapshot_hash(
    result: AnalysisResult | None, variant: MasterplanVariant | None = None
) -> str | None:
    """sha256 over the canonical JSON snapshot the report was built from (§31).

    Pydantic ``model_dump_json`` is field-order deterministic, so the same stored
    analysis + variant always hash identically (reproducibility evidence).
    Returns ``None`` when there is nothing to snapshot.
    """
    if result is None and variant is None:
        return None
    payload = json.dumps(
        {
            "analysis": result.model_dump(mode="json") if result is not None else None,
            "variant": variant.model_dump(mode="json") if variant is not None else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sections_for(kind: str, audience: str) -> list[str]:
    table = (
        KONCEPCJA_AUDIENCE_SECTIONS if kind == "koncepcja" else SCREENING_AUDIENCE_SECTIONS
    )
    if audience not in table:
        raise ValueError(
            f"Nieznana grupa odbiorców raportu: {audience!r} "
            f"(dozwolone: {sorted(table)})"
        )
    return list(table[audience])


# --------------------------------------------------------------------------- #
# Builders — assemble the model ONCE from already-computed data (no analysis,
# no network, no recomputation; §31).
# --------------------------------------------------------------------------- #
def build_screening_model(
    result: AnalysisResult, *, audience: Audience = "architect", generated_at: str = ""
) -> ReportModel:
    """Build the §22 screening :class:`ReportModel` from a stored AnalysisResult.

    The blocks mirror what the pre-Phase-14 ``render_markdown`` read directly off
    the result, so the architect Markdown stays byte-identical. ``generated_at``
    is metadata only (the §22 Markdown does not print it — byte-compat).
    """
    from plot_reports.report import headline_numbers  # local: report imports formats

    h = headline_numbers(result)
    planning = result.planning if isinstance(result.planning, dict) else {}
    parcel_display = (
        (result.parcel.external_id or result.parcel.teryt or result.parcel.id)
        if result.parcel
        else result.analysis_id
    )
    env = result.buildable_envelope
    env_block: dict[str, Any] = {"present": env is not None}
    if env is not None:
        env_block.update(
            {
                "area_m2": env.area_m2,
                "confidence": env.confidence,
                "has_lir": env.largest_inscribed_rectangle is not None,
                "removed_by": list(
                    (env.metadata or {}).get("removed_by", [])
                    if isinstance(env.metadata, dict)
                    else []
                ),
            }
        )
    decision = result.decision.value
    summary = [
        f"Decyzja screeningowa: {decision}.",
        f"Ograniczenia: {h['constraint_count']}; czerwone flagi: {h['risk_count']}; "
        f"pozycje niepewne: {h['unknown_count']}.",
    ]
    return ReportModel(
        kind="screening",
        audience=audience,
        sections=_sections_for("screening", audience),
        analysis_id=result.analysis_id,
        generated_at=generated_at,
        analysis_snapshot_hash=snapshot_hash(result),
        title_block={
            "investor": None,  # not in the domain inputs — never invented (§21)
            "parcel": parcel_display,
            "decision": decision,
        },
        executive_summary=summary,
        decision=decision,
        status=result.status.value,
        headline=h,
        parcel_display_id=str(parcel_display),
        geometry_metrics=dict(planning.get("_geometry_metrics", {}) or {}),
        planning_summary={
            "mpzp_pog_wz": planning.get("mpzp_pog_wz", "brak danych"),
            "coverage_status": planning.get("coverage_status", "brak danych"),
            "municipality": planning.get("municipality"),
        },
        layer_status=dict(planning.get("_risk_layer_status", {}) or {}),
        envelope=env_block,
        constraint_types=sorted({c.constraint_type for c in result.constraints}),
        risks=[r.model_dump(mode="json") for r in result.risks],
        unknowns=[u.model_dump(mode="json") for u in result.unknowns],
        next_actions=[a.model_dump(mode="json") for a in result.next_actions],
        sources=list(planning.get("_sources", []) or []),
        evidence_count=len(result.evidence),
    )


def build_koncepcja_model(
    *,
    analysis_id: str,
    variant: MasterplanVariant,
    generated_at: str,
    brief: dict[str, Any] | None = None,
    rationales: list[dict[str, Any]] | None = None,
    unknowns: list[dict[str, Any]] | None = None,
    capacity: dict[str, Any] | None = None,
    plan_png_resource: str | None = None,
    audience: Audience = "architect",
    analysis_result: AnalysisResult | None = None,
) -> ReportModel:
    """Build the koncepcja (chłonność) :class:`ReportModel` from STORED data.

    Same inputs the Phase 11 ``render_koncepcja_markdown`` took — the assembly is
    reused, the renderers moved to :mod:`plot_reports.formats`. ``analysis_result``
    (when the variant is analysis-bound) feeds the snapshot hash.
    """
    from plot_reports.formats import fmt_m2_pl

    metadata = variant.metadata if isinstance(variant.metadata, dict) else {}
    checks = list(metadata.get("inter_building_checks") or [])
    summary = compliance_summary(checks)
    totals = variant.totals or {}
    unknown_items = list(unknowns or [])
    missing = list((brief or {}).get("missing_indicators") or [])
    executive = [
        f"Wariant masterplanu `{variant.id}`: {totals.get('buildings', '?')} budynków, "
        f"~{totals.get('mieszkania_estimate', '?')} mieszkań (estymacja).",
        f"PUM: {fmt_m2_pl(totals.get('pum_m2'))} m²; PUU: "
        f"{fmt_m2_pl(totals.get('puu_m2'))} m²; PU: {fmt_m2_pl(totals.get('pu_m2'))} m².",
        f"Reguły WT/ppoż: fail: {summary['fail']}, warning: {summary['warning']}, "
        f"unknown: {summary['unknown']}.",
        f"Pozycje niepewne: {len(unknown_items)}; wskaźniki do potwierdzenia: "
        f"{len(missing)}.",
    ]
    return ReportModel(
        kind="koncepcja",
        audience=audience,
        sections=_sections_for("koncepcja", audience),
        analysis_id=analysis_id,
        generated_at=generated_at,
        ruleset_version=(
            str(metadata["ruleset_version"]) if metadata.get("ruleset_version") else None
        ),
        analysis_snapshot_hash=snapshot_hash(analysis_result, variant),
        title_block={
            "investor": None,  # not in the domain inputs — never invented (§21)
            "date": generated_at,
            "variant": variant.id,
            "ruleset": metadata.get("ruleset_version"),
        },
        executive_summary=executive,
        decision=None,
        headline={
            "buildings": totals.get("buildings"),
            "mieszkania_estimate": totals.get("mieszkania_estimate"),
            "pum_m2": totals.get("pum_m2"),
            "puu_m2": totals.get("puu_m2"),
            "pu_m2": totals.get("pu_m2"),
        },
        variant_id=variant.id,
        plan_png_resource=plan_png_resource,
        brief=brief,
        totals=dict(totals),
        capacity=capacity,
        buildings=[b.model_dump(mode="json") for b in variant.buildings],
        stage_table=[dict(r) for r in variant.stage_table],
        compliance=summary,
        staging=list(metadata.get("staging_checks") or []),
        rationales=list(rationales or []),
        unknowns=unknown_items,
        missing_indicators=missing,
        config_basis=dict(metadata.get("config_basis") or {}),
        metrics_resource=(
            f"analysis://{analysis_id}/masterplan/{variant.id}/metrics.json"
        ),
    )


# --------------------------------------------------------------------------- #
# Report comparison (F-0398 / F-0413)
# --------------------------------------------------------------------------- #
_NUMERIC_FIELDS: tuple[str, ...] = ("headline", "totals", "compliance")


def _flatten_numbers(prefix: str, obj: Any, out: dict[str, Any]) -> None:
    if isinstance(obj, dict):
        for key in sorted(obj):
            _flatten_numbers(f"{prefix}.{key}", obj[key], out)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _flatten_numbers(f"{prefix}[{i}]", item, out)
    elif isinstance(obj, bool):  # bool is an int subclass — not a report "number"
        return
    elif isinstance(obj, int | float):
        out[prefix] = obj


def compare_reports(a: ReportModel, b: ReportModel) -> dict[str, Any]:
    """Diff the NUMBERS and decisions of two report models (F-0398/F-0413).

    Compares the numeric content blocks (headline, totals, stage table, compliance
    counts) plus ``decision`` / ``report_version`` / ``ruleset_version`` /
    ``analysis_snapshot_hash``. Returns ``{"identical": bool, "changed": [...]}``
    where each change is ``{"field", "a", "b"}`` — section/audience differences
    are NOT changes (same numbers, different selection).
    """
    changed: list[dict[str, Any]] = []
    for field in ("decision", "report_version", "ruleset_version", "analysis_snapshot_hash"):
        va, vb = getattr(a, field), getattr(b, field)
        if va != vb:
            changed.append({"field": field, "a": va, "b": vb})
    nums_a: dict[str, Any] = {}
    nums_b: dict[str, Any] = {}
    for field in _NUMERIC_FIELDS:
        _flatten_numbers(field, getattr(a, field), nums_a)
        _flatten_numbers(field, getattr(b, field), nums_b)
    _flatten_numbers("stage_table", a.stage_table, nums_a)
    _flatten_numbers("stage_table", b.stage_table, nums_b)
    for key in sorted(set(nums_a) | set(nums_b)):
        va, vb = nums_a.get(key), nums_b.get(key)
        if va != vb:
            changed.append({"field": key, "a": va, "b": vb})
    return {
        "identical": not changed,
        "changed": changed,
        "a": {"analysis_id": a.analysis_id, "variant_id": a.variant_id},
        "b": {"analysis_id": b.analysis_id, "variant_id": b.variant_id},
    }
