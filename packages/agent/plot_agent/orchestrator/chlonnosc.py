"""Chłonność pipeline composite (Phase 13 addendum; v2 Target workflow steps 1–6).

``build_chlonnosc_graph`` wires the EXISTING §27 use-cases into one plannable
:class:`~plot_agent.orchestrator.graph.TaskGraph`:

``parcel_resolve`` → ``parcel_analyze(full)`` → ``planning_fetch``/``planning_parse_document``
(when documents are available) → freshness check (source staleness → conservative
rule-evaluation mode, F-0443/0445) → ``capacity_generate_scenarios`` →
``design_brief`` **[GATE: review the brief]** → model layout iterations
**[GATE — the model proposes via propose_layout; NEVER an automated optimizer
node, v1 §11.4 anti-pattern]** → ``report_generate(koncepcja)`` **[GATE: final
review]**.

The freshness verdict is CONSUMED, not merely computed (review M2): the
``capacity`` output and the ``koncepcja`` gate payload carry
``evaluation_mode`` plus an explicit banner when conservative (the model SEES
that stale sources degraded the run), and the full-DD analyze step stores the
verdict on the analysis (``planning['_freshness']``) so the analysis-bound
``propose_layout`` path evaluates the inter-building checks under it.

The use-case callables are INJECTED as a duck-typed namespace (the MCP server
passes its reloadable ``usecases`` module; tests pass stubs/wrappers) so this
package never imports the app layer. Multi-parcel investment areas above the
configured limit are split into per-parcel subgraphs (F-0436) feeding a merge
node that designates the primary analysis.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from plot_agent.orchestrator.graph import (
    NodeContext,
    TaskGraph,
    TaskNode,
    add_chunked_nodes,
    degraded,
)
from plot_agent.orchestrator.store import DEFAULT_GRAPH_REGISTRY


@dataclass(frozen=True)
class ChlonnoscConfig:
    """Composite configuration (no legal values — orchestration knobs only)."""

    #: InvestmentArea chunk limit (F-0436): more parcels than this → per-parcel
    #: subgraphs. 1 = any multi-parcel input is chunked.
    chunk_limit: int = 1
    #: Rule-evaluation mode the composite reports when sources are FRESH;
    #: degraded freshness always forces "conservative" (F-0443/0445). The mode
    #: travels in the graph context and the capacity/koncepcja payloads (with a
    #: banner when conservative) — review M2.
    fresh_mode: str = "strict"
    #: Per-source max age (days) for the freshness check; None → settings default.
    source_max_age_days: float | None = None
    ruleset_version: str = "latest"


#: Model-visible banner for a conservative run (review M2): the gate payloads
#: and the capacity output must SAY that stale sources degraded the evaluation.
CONSERVATIVE_BANNER = (
    "tryb konserwatywny: źródła nieaktualne — niewiadome na regułach twardych "
    "raportowane jako potencjalne blokery (F-0443/0445)"
)


def _analysis_id_of(ctx: NodeContext) -> str | None:
    aid = ctx.context.get("analysis_id")
    return str(aid) if aid else None


def _evaluation_mode_block(ctx: NodeContext, default: str) -> dict[str, Any]:
    """``evaluation_mode`` (+ banner when conservative) for model-facing payloads."""
    mode = str(ctx.context.get("evaluation_mode") or default)
    block: dict[str, Any] = {"evaluation_mode": mode}
    if mode == "conservative":
        block["freshness_banner"] = CONSERVATIVE_BANNER
    return block


def _full_analyze(usecases: Any, parcel_payload: dict[str, Any], goal: dict[str, Any] | None) -> Any:
    """Run ``parcel_analyze`` in full_due_diligence mode via the injected use-cases."""
    from plot_domain import AnalysisInput

    payload = AnalysisInput.model_validate(
        {
            "input": parcel_payload,
            "analysis_mode": "full_due_diligence",
            "investment_goal": goal or {},
            "options": {},
        }
    )
    return usecases.parcel_analyze(payload, "latest")


def build_chlonnosc_graph(
    usecases: Any,
    parcel_input: dict[str, Any],
    goal: dict[str, Any] | None = None,
    *,
    documents: list[str] | None = None,
    municipality_id: str | None = None,
    graph_id: str | None = None,
    config: ChlonnoscConfig | None = None,
    store: Any | None = None,
    audit: Any | None = None,
    status: Any | None = None,
    memory: Any | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    register: bool = True,
) -> TaskGraph:
    """Build the chłonność composite graph (Target-workflow steps 1–6).

    ``usecases`` is duck-typed: it must provide ``parcel_resolve``,
    ``parcel_analyze``, ``planning_fetch``, ``planning_parse_document``,
    ``capacity_generate_scenarios``, ``design_brief_for_analysis`` and
    ``report_generate`` with the §27 signatures. ``documents`` are optional
    planning-document texts (uchwała excerpts) — absent documents degrade the
    planning step explicitly (indicators stay unknown, never defaulted).
    """
    cfg = config or ChlonnoscConfig()
    graph = TaskGraph(
        graph_id or f"chlonnosc:{uuid.uuid4().hex[:12]}",
        store=store,
        audit=audit,
        status=status,
        memory=memory,
        on_event=on_event,
    )

    parcel_ids = parcel_input.get("parcel_ids")
    chunked = isinstance(parcel_ids, list) and len(parcel_ids) > cfg.chunk_limit

    # ------------------------------------------------------------------ #
    # Steps 1–2: resolve + analyze (single parcel OR chunked per parcel).
    # ------------------------------------------------------------------ #
    if not chunked:
        single_input = dict(parcel_input)
        if isinstance(parcel_ids, list) and parcel_ids:
            single_input = {"parcel_id": parcel_ids[0]}

        def _resolve(ctx: NodeContext) -> Any:
            return usecases.parcel_resolve(dict(single_input))

        def _analyze(ctx: NodeContext) -> Any:
            result = _full_analyze(usecases, single_input, goal)
            ctx.context["analysis_id"] = result.analysis_id
            ctx.memory.remember("analysis_id", result.analysis_id)
            ctx.memory.remember(
                "decision", getattr(getattr(result, "decision", None), "value", None)
            )
            return result

        graph.add(
            TaskNode(
                id="resolve",
                run=_resolve,
                params={"input": single_input},
                description="parcel_resolve (ULDK) — Target workflow step 1",
            )
        )
        graph.add(
            TaskNode(
                id="analyze",
                run=_analyze,
                depends_on=("resolve",),
                # Review M4: the goal CHANGES the analysis result, so it must be
                # part of the idempotency hash — a re-run with a different goal
                # re-executes instead of reusing the stale outcome (F-0431).
                params={"mode": "full_due_diligence", "goal": goal or {}},
                description="parcel_analyze(full_due_diligence) — step 1",
            )
        )
        analyze_dep = "analyze"
    else:
        # F-0436: InvestmentArea above the limit → per-parcel subgraphs + merge.
        def _make_chunk_run(pid: str) -> Callable[[NodeContext], Any]:
            def _chunk(ctx: NodeContext) -> Any:
                result = _full_analyze(usecases, {"parcel_id": pid}, goal)
                planning_block = result.planning if isinstance(result.planning, dict) else {}
                return {
                    "parcel_id": pid,
                    "analysis_id": result.analysis_id,
                    "status": result.status.value,
                    "decision": result.decision.value,
                    "parcel_area_m2": (
                        float(result.parcel.area_m2)
                        if result.parcel is not None and result.parcel.area_m2
                        else None
                    ),
                    # Review M3: each chunk surfaces ITS SourceRecords so the
                    # freshness check is not blind on chunked runs.
                    "sources": list(planning_block.get("_sources") or []),
                }

            return _chunk

        def _merge(ctx: NodeContext) -> Any:
            partials = [
                ctx.upstream_value(dep)
                for dep in ctx.upstream
                if ctx.upstream_ok(dep)
            ]
            partials = [p for p in partials if isinstance(p, dict)]
            failed = [dep for dep in ctx.upstream if not ctx.upstream_ok(dep)]
            if not partials:
                raise RuntimeError("no parcel chunk produced an analysis")
            primary = max(partials, key=lambda p: p.get("parcel_area_m2") or 0.0)
            ctx.context["analysis_id"] = primary["analysis_id"]
            ctx.memory.remember("analysis_id", primary["analysis_id"])
            ctx.memory.remember("chunked_analyses", partials)
            # Review M3: the union of every chunk's sources travels with the
            # merge value — the freshness node reads them from here.
            sources = [s for p in partials for s in (p.get("sources") or [])]
            merged = {
                "primary_analysis_id": primary["analysis_id"],
                "parcels": partials,
                "failed_chunks": failed,
                "sources": sources,
                "note": (
                    "Obszar inwestycyjny podzielony na podgrafy per działka "
                    "(F-0436); chłonność liczona dla działki głównej, wyniki "
                    "częściowe zachowane w pamięci analizy (F-0438)."
                ),
            }
            if failed:
                return degraded(merged, f"failed_chunks:{','.join(failed)}")
            return merged

        analyze_dep = add_chunked_nodes(
            graph,
            base_id="analyze",
            items=[str(p) for p in parcel_ids or []],
            make_run=_make_chunk_run,
            merge=_merge,
            # Review M4: the goal is a real input of every chunk analysis.
            params={"goal": goal or {}},
        )

    # ------------------------------------------------------------------ #
    # Step 2: planning frame — fetch + parse (documents optional, honest gap).
    # ------------------------------------------------------------------ #
    def _planning(ctx: NodeContext) -> Any:
        fetch = None
        try:
            fetch = usecases.planning_fetch(municipality_id, parcel_input.get("parcel_id"))
        except Exception as exc:  # planning fetch failure degrades, never blocks
            fetch = {"status": "fetch_failed", "error": str(exc)}
        if not documents:
            return degraded(
                {"fetch": fetch, "parse": None, "indicators": []},
                "no_planning_documents_available",
            )
        indicators: list[dict[str, Any]] = []
        parses: list[dict[str, Any]] = []
        for text in documents:
            parsed = usecases.planning_parse_document(None, text, None)
            parses.append(parsed)
            indicators.extend(parsed.get("indicators") or [])
        return {"fetch": fetch, "parse": parses, "indicators": indicators}

    graph.add(
        TaskNode(
            id="planning",
            run=_planning,
            depends_on=(analyze_dep,),
            # Review M4: the parsed documents + municipality are the node's real
            # inputs — changing them must invalidate the idempotency hash.
            params={
                "documents": list(documents or []),
                "municipality_id": municipality_id,
            },
            description="planning_fetch + planning_parse_document — step 2",
        )
    )

    # ------------------------------------------------------------------ #
    # Freshness check (F-0439/0443/0445): stale sources → conservative mode.
    # ------------------------------------------------------------------ #
    def _freshness(ctx: NodeContext) -> Any:
        from plot_agent.monitoring import (
            FreshnessConfig,
            evaluation_mode_for,
            source_freshness,
        )

        sources: list[Any] = []
        analyze_value = ctx.upstream_value(analyze_dep)
        if isinstance(analyze_value, Mapping):
            # Chunked run (review M3): the merge value carries the union of
            # every chunk's SourceRecords.
            sources = list(analyze_value.get("sources") or [])
        else:
            planning_block = getattr(analyze_value, "planning", None)
            if isinstance(planning_block, Mapping):
                sources = list(planning_block.get("_sources") or [])
        fcfg = (
            FreshnessConfig(default_max_age_days=cfg.source_max_age_days)
            if cfg.source_max_age_days is not None
            else FreshnessConfig.from_settings()
        )
        report = source_freshness(sources, config=fcfg)
        if not sources:
            # Review M3 fail-safe: ZERO visible sources is never "fresh" — an
            # unverifiable run degrades and forces conservative mode (§21).
            report.degraded = True
        mode = evaluation_mode_for(report, default=cfg.fresh_mode)
        ctx.context["evaluation_mode"] = mode
        ctx.memory.remember("evaluation_mode", mode)
        doc: dict[str, Any] = {"freshness": report.to_dict(), "evaluation_mode": mode}
        if not sources:
            doc["freshness"]["reason"] = "no_sources_visible"
            return degraded(doc, "no_sources_visible")
        return doc

    graph.add(
        TaskNode(
            id="freshness",
            run=_freshness,
            depends_on=(analyze_dep,),
            description="source freshness → rule-evaluation mode (F-0439/0445)",
        )
    )

    # ------------------------------------------------------------------ #
    # Step 3: numeric chłonność BEFORE any drawing.
    # ------------------------------------------------------------------ #
    def _capacity(ctx: NodeContext) -> Any:
        aid = _analysis_id_of(ctx)
        planning_value = ctx.upstream_value("planning") or {}
        indicators = planning_value.get("indicators") if isinstance(planning_value, dict) else None
        result = usecases.capacity_generate_scenarios(aid, indicators or None)
        if isinstance(result, dict):
            # Review M2: the capacity output CARRIES the evaluation mode (+ a
            # banner when conservative) so the model sees the degraded basis.
            return {**result, **_evaluation_mode_block(ctx, cfg.fresh_mode)}
        return result

    graph.add(
        TaskNode(
            id="capacity",
            run=_capacity,
            depends_on=(analyze_dep, "planning", "freshness"),
            hard_requires=(analyze_dep,),
            description="capacity_generate_scenarios — step 3 (numbers before drawing)",
        )
    )

    # ------------------------------------------------------------------ #
    # Step 4: design brief — GATE (manual review of the brief, addendum).
    # ------------------------------------------------------------------ #
    def _brief(ctx: NodeContext) -> Any:
        aid = _analysis_id_of(ctx)
        if aid is None:
            raise RuntimeError("analysis_id missing — analyze step did not complete")
        return usecases.design_brief_for_analysis(aid)

    graph.add(
        TaskNode(
            id="design_brief",
            run=_brief,
            depends_on=(analyze_dep, "capacity"),
            hard_requires=(analyze_dep,),
            gate=True,
            gate_reason=(
                "Przejrzyj design brief (osie kompozycyjne, strefy zabudowy, "
                "typologie, reguły twarde) przed iteracjami projektowymi."
            ),
            description="design brief — step 4 [GATE: review brief]",
        )
    )

    # ------------------------------------------------------------------ #
    # Step 5: model layout iterations — a GATE, NOT an automated node.
    # The model proposes via propose_layout interactively (v1 §11.4 guard:
    # the server validates/scores/criticizes — it never generates designs).
    # ------------------------------------------------------------------ #
    def _layout_iterations(ctx: NodeContext) -> Any:
        return {
            "action_required": "model_proposes_layouts",
            "how": (
                "Model iteruje propose_layout (DSL v2) interaktywnie na podstawie "
                "briefu i scenariuszy chłonności; graf czeka — serwer NIE generuje "
                "projektów automatycznie (plan §11.4)."
            ),
            "analysis_id": _analysis_id_of(ctx),
            "brief": ctx.upstream_value("design_brief"),
            "capacity": ctx.upstream_value("capacity"),
        }

    graph.add(
        TaskNode(
            id="layout_iterations",
            run=_layout_iterations,
            depends_on=("design_brief", "capacity"),
            gate=True,
            gate_reason=(
                "Iteracje masterplanu należą do modelu (propose_layout); zatwierdź "
                "gdy wariant koncepcji jest gotowy."
            ),
            description="model massing iterations — step 5 [GATE: model proposes]",
        )
    )

    # ------------------------------------------------------------------ #
    # Step 6: koncepcja deliverable — GATE (final review, addendum).
    # ------------------------------------------------------------------ #
    def _koncepcja(ctx: NodeContext) -> Any:
        aid = _analysis_id_of(ctx)
        report = usecases.report_generate(aid, "koncepcja", None)
        if isinstance(report, dict):
            # Review M2: the final-review GATE payload carries the evaluation
            # mode (+ conservative banner) — the reviewer/model sees that the
            # deliverable was evaluated on stale sources.
            report = {**report, **_evaluation_mode_block(ctx, cfg.fresh_mode)}
            if report.get("status") == "not_found":
                return degraded(report, "no_masterplan_variant_stored")
        return report

    graph.add(
        TaskNode(
            id="koncepcja",
            run=_koncepcja,
            depends_on=("layout_iterations",),
            gate=True,
            gate_reason="Finalna recenzja koncepcji (raport + rysunek planu) przed zamknięciem.",
            description="report_generate(koncepcja) — step 6 [GATE: final review]",
        )
    )

    if register:
        DEFAULT_GRAPH_REGISTRY.register(graph)
    return graph
