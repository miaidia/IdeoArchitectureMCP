"""Dramatiq actors for the async/batch surface (Phase 13 / v1 Phase 11 §11.1.3).

Importing this module installs the env-configured broker (Redis when
``PLOT_QUEUE_ENABLED``, else the StubBroker — zero-Redis tests) and declares
four actors wrapping the SHARED ``plot_agent`` use-cases (§27 — the same code
the MCP server runs in-process when no broker is configured):

* :func:`run_full_due_diligence_task` — RESUMABLE: the analysis runs inside a
  persisted :class:`~plot_agent.orchestrator.TaskGraph` keyed by the analysis
  id, so a killed worker re-run continues from the last completed node
  (NFR-REL-002/003 via the Task-1 idempotency hashes);
* :func:`portfolio_batch_task` — §4.4 batch (dedupe/ranking/exports);
* :func:`monitoring_check_task` — one §4.5 monitor check (a deployment-level
  scheduler enqueues these per ``MonitorStore.due()``; real cron is a
  deployment concern);
* :func:`cache_warm_task` — sequential cache warming with backpressure delay.

Connectors are injectable via :func:`set_connectors` (tests pass mocks; the
default is the production bundle). Every actor records start/finish events in
the orchestrator status store so progress is observable (F-0434).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import dramatiq
from plot_shared import get_logger, get_settings

from plot_worker.broker import setup_broker

_log = get_logger(__name__)

#: The broker is installed at import time (StubBroker unless PLOT_QUEUE_ENABLED).
broker = setup_broker()

_QUEUE = get_settings().queue_name

# Injectable connector bundle (mirrors plot_mcp_server.usecases.set_connectors).
_CONNECTORS: Any | None = None


def set_connectors(connectors: Any | None) -> None:
    """Override the connector bundle the actors use (tests inject mocks)."""
    global _CONNECTORS
    _CONNECTORS = connectors


def _get_connectors() -> Any:
    if _CONNECTORS is not None:
        return _CONNECTORS
    from plot_agent.analysis import build_default_connectors

    return build_default_connectors()


def _record(key: str, state: str, **detail: Any) -> None:
    from plot_agent.orchestrator import DEFAULT_STATUS_STORE

    DEFAULT_STATUS_STORE.append(
        key,
        {
            "graph_id": key,
            "analysis_id": None,
            "node_id": None,
            "state": state,
            "graph_status": state,
            "progress": 1.0 if state in ("complete", "failed") else 0.0,
            "detail": detail or None,
            "at": datetime.now(UTC).isoformat(),
        },
    )


@dramatiq.actor(queue_name=_QUEUE, max_retries=0)
def run_full_due_diligence_task(analysis_id: str, input_payload: dict[str, Any]) -> None:
    """Async, resumable full due diligence (NFR-PERF-002, NFR-REL-002/003).

    The work runs inside a persisted TaskGraph (graph_id = the analysis id):
    a re-sent message after a kill reuses every completed node outcome and
    continues from the failure point — idempotent re-runs are no-ops. The
    INPUT PAYLOAD is part of the analyze node's params (review M4): a re-run
    with a CHANGED payload invalidates the idempotency hash and re-executes
    instead of serving the stale outcome.
    """
    from plot_agent.orchestrator import NodeContext, TaskGraph, TaskNode

    key = f"task:full_due_diligence:{analysis_id}"
    _record(key, "running", analysis_id=analysis_id)
    try:  # review m3: a raising actor must never leave the status stuck "running"
        connectors = _get_connectors()
        graph = TaskGraph(f"fdd:{analysis_id}", analysis_id=analysis_id)

        def _analyze(ctx: NodeContext) -> Any:
            from plot_agent.analysis import (
                DEFAULT_SITE_CONTEXT_STORE,
                DEFAULT_STORE,
                run_full_due_diligence,
            )
            from plot_domain import AnalysisInput

            payload = AnalysisInput.model_validate(input_payload)
            result = asyncio.run(
                run_full_due_diligence(
                    payload,
                    connectors=connectors,
                    analysis_id=analysis_id,
                    site_store=DEFAULT_SITE_CONTEXT_STORE,
                )
            )
            DEFAULT_STORE.put(result)
            ctx.context["analysis_id"] = result.analysis_id
            return result

        if graph.node("analyze") is None:
            graph.add(
                TaskNode(
                    id="analyze",
                    run=_analyze,
                    # Review M4: the payload IS the node's input — it must be in
                    # the idempotency hash (F-0431), not only in the closure.
                    params={"input_payload": input_payload},
                    description="full_due_diligence",
                )
            )
        state = graph.run()
    except Exception as exc:
        _record(key, "failed", analysis_id=analysis_id, error=f"{type(exc).__name__}: {exc}")
        raise
    _record(key, "complete" if state.status == "complete" else state.status)


@dramatiq.actor(queue_name=_QUEUE, max_retries=0)
def portfolio_batch_task(batch_id: str, parcels: list[dict[str, Any]]) -> None:
    """§4.4 portfolio batch in the worker (queued path of ``portfolio_analyze``).

    Results persist as artifacts (CSV/JSON/GeoJSON via the shared ArtifactStore)
    and per-parcel analyses in the analysis store; progress/status events go to
    the status store under the batch id.
    """
    from plot_agent.portfolio import run_portfolio_analysis

    settings = get_settings()
    _record(batch_id, "running", submitted=len(parcels))
    try:  # review m3: never leave the batch status stuck "running"
        result = asyncio.run(
            run_portfolio_analysis(
                parcels,
                connectors=_get_connectors(),
                batch_id=batch_id,
                delay_s=settings.backpressure_delay_s,
            )
        )
    except Exception as exc:
        _record(batch_id, "failed", error=f"{type(exc).__name__}: {exc}")
        raise
    _record(
        batch_id,
        "complete",
        analyzed=result["analyzed"],
        failed=result["failed"],
        artifacts=result["artifacts"],
    )


@dramatiq.actor(queue_name=_QUEUE, max_retries=0)
def monitoring_check_task(monitor_id: str) -> None:
    """One §4.5 monitoring check (enqueued per due monitor by the scheduler)."""
    from plot_agent.monitoring import run_monitor_check

    key = f"task:monitoring:{monitor_id}"
    _record(key, "running")
    try:  # review m3: never leave the check status stuck "running"
        result = run_monitor_check(monitor_id)
    except Exception as exc:
        _record(key, "failed", error=f"{type(exc).__name__}: {exc}")
        raise
    _record(key, "complete", status=result.get("status"))


@dramatiq.actor(queue_name=_QUEUE, max_retries=0)
def cache_warm_task(scope: str, target_id: str | None) -> None:
    """Sequential cache warm with backpressure delay (NFR-PERF-011/014)."""
    from plot_agent.warming import warm_cache

    settings = get_settings()
    key = f"task:cache_warm:{scope}:{target_id}"
    _record(key, "running")
    try:  # review m3: never leave the warm status stuck "running"
        result = asyncio.run(
            warm_cache(
                scope,
                target_id,
                connectors=_get_connectors(),
                delay_s=settings.backpressure_delay_s,
            )
        )
    except Exception as exc:
        _record(key, "failed", error=f"{type(exc).__name__}: {exc}")
        raise
    _record(key, "complete", status=result.get("status"), warmed=result.get("warmed_count"))


__all__ = [
    "broker",
    "cache_warm_task",
    "monitoring_check_task",
    "portfolio_batch_task",
    "run_full_due_diligence_task",
    "set_connectors",
]
