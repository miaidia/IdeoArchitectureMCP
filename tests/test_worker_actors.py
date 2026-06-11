"""Dramatiq worker tests (Phase 13 / v1 Phase 11 §11.1.3) — NO live Redis.

Uses the documented Dramatiq unit-testing pattern, verified against the
INSTALLED package source (dramatiq 2.x):

* ``dramatiq.brokers.stub.StubBroker`` — ``enqueue`` / ``join(queue_name,
  fail_fast=...)`` / ``flush_all`` (``dramatiq/brokers/stub.py:33``);
* ``dramatiq.Worker(broker, worker_timeout=100)`` + ``start/join/stop``
  (``dramatiq/worker.py:60``);
* actors declared via ``@dramatiq.actor`` bind to the broker installed by
  ``dramatiq.set_broker`` at import time (``dramatiq/broker.py:72``).

``plot_worker.broker`` installs the StubBroker whenever ``PLOT_QUEUE_ENABLED``
is false, so importing the actors needs no Redis; the queued use-case path is
exercised by flipping the setting AFTER import (the actors stay bound to the
stub broker — messages land on its in-memory queue).
"""

from __future__ import annotations

from typing import Any

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from plot_agent.orchestrator import DEFAULT_GRAPH_STORE, DEFAULT_STATUS_STORE
from plot_worker import actors
from tests.mocks import mock_connectors


@pytest.fixture()
def stub_worker() -> Any:
    """The docs' StubBroker + Worker harness around the plot_worker actors."""
    broker = actors.broker
    assert isinstance(broker, StubBroker)  # queue disabled → stub, never Redis
    broker.emit_after("process_boot")
    broker.flush_all()
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    actors.set_connectors(mock_connectors())
    try:
        yield broker, worker
    finally:
        actors.set_connectors(None)
        worker.stop()
        broker.flush_all()
        DEFAULT_STATUS_STORE.clear()
        DEFAULT_GRAPH_STORE.clear()


def _join(broker: StubBroker, worker: dramatiq.Worker, queue_name: str) -> None:
    broker.join(queue_name, fail_fast=True)
    worker.join()


def test_cache_warm_task_enqueues_and_processes(stub_worker: Any) -> None:
    broker, worker = stub_worker
    actors.cache_warm_task.send("parcel", "141201_1.0001.1867/2")
    _join(broker, worker, actors.cache_warm_task.queue_name)

    events = DEFAULT_STATUS_STORE.events("task:cache_warm:parcel:141201_1.0001.1867/2")
    states = [e["state"] for e in events]
    assert states == ["running", "complete"]
    assert events[-1]["detail"]["status"] == "warmed"
    assert events[-1]["detail"]["warmed"] > 0  # themes actually fetched (mocks)


def test_full_due_diligence_task_is_resumable_and_idempotent(stub_worker: Any) -> None:
    broker, worker = stub_worker
    from plot_agent.analysis import DEFAULT_STORE

    actors.run_full_due_diligence_task.send(
        "phase13-fdd-1",
        {
            "input": {"parcel_id": "141201_1.0001.1867/2"},
            "analysis_mode": "full_due_diligence",
            "investment_goal": {"type": "multifamily"},
            "options": {},
        },
    )
    _join(broker, worker, actors.run_full_due_diligence_task.queue_name)
    result = DEFAULT_STORE.get("phase13-fdd-1")
    assert result is not None and result.analysis_id == "phase13-fdd-1"

    state = DEFAULT_GRAPH_STORE.get("fdd:phase13-fdd-1")
    assert state is not None
    assert state.run_counts["analyze"] == 1

    # Idempotent re-run (NFR-REL-003): a re-sent message reuses the completed
    # node — the analysis is NOT recomputed.
    actors.run_full_due_diligence_task.send(
        "phase13-fdd-1",
        {
            "input": {"parcel_id": "141201_1.0001.1867/2"},
            "analysis_mode": "full_due_diligence",
            "investment_goal": {"type": "multifamily"},
            "options": {},
        },
    )
    _join(broker, worker, actors.run_full_due_diligence_task.queue_name)
    state = DEFAULT_GRAPH_STORE.get("fdd:phase13-fdd-1")
    assert state is not None and state.run_counts["analyze"] == 1  # not re-executed


def test_changed_payload_reexecutes_analysis_under_same_analysis_id(stub_worker: Any) -> None:
    """Review M4: the input payload is part of the analyze node's idempotency
    hash — a re-sent message with a CHANGED payload must re-execute instead of
    serving the stale outcome of the previous payload."""
    broker, worker = stub_worker

    def _payload(goal_type: str) -> dict[str, Any]:
        return {
            "input": {"parcel_id": "141201_1.0001.1867/2"},
            "analysis_mode": "full_due_diligence",
            "investment_goal": {"type": goal_type},
            "options": {},
        }

    actors.run_full_due_diligence_task.send("phase13-fdd-m4", _payload("multifamily"))
    _join(broker, worker, actors.run_full_due_diligence_task.queue_name)
    state = DEFAULT_GRAPH_STORE.get("fdd:phase13-fdd-m4")
    assert state is not None and state.run_counts["analyze"] == 1

    # Same analysis id, DIFFERENT payload → the hash invalidates → re-execute.
    actors.run_full_due_diligence_task.send("phase13-fdd-m4", _payload("office"))
    _join(broker, worker, actors.run_full_due_diligence_task.queue_name)
    state = DEFAULT_GRAPH_STORE.get("fdd:phase13-fdd-m4")
    assert state is not None and state.run_counts["analyze"] == 2


def test_actor_exception_records_failed_status_never_zombie_running(
    stub_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review m3: a raising actor must record status 'failed' — previously the
    status store stayed stuck on 'running' forever (zombie task)."""
    broker, worker = stub_worker
    import plot_agent.warming as warming

    async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("TEST FIXTURE: warm exploded")

    monkeypatch.setattr(warming, "warm_cache", _boom)
    actors.cache_warm_task.send("parcel", "fail-1")
    with pytest.raises(RuntimeError, match="warm exploded"):
        _join(broker, worker, actors.cache_warm_task.queue_name)

    events = DEFAULT_STATUS_STORE.events("task:cache_warm:parcel:fail-1")
    assert [e["state"] for e in events] == ["running", "failed"]
    assert "RuntimeError" in events[-1]["detail"]["error"]


def test_portfolio_batch_task_failure_records_failed_status(
    stub_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review m3 (batch path): a crash before the isolation layer (e.g. the
    artifact export) must mark the batch failed, not leave it 'running'."""
    broker, worker = stub_worker
    import plot_agent.portfolio as portfolio_mod

    async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise OSError("TEST FIXTURE: artifact store down")

    monkeypatch.setattr(portfolio_mod, "run_portfolio_analysis", _boom)
    actors.portfolio_batch_task.send("batch:fail-1", [{"parcel_id": "P1"}])
    with pytest.raises(OSError, match="artifact store down"):
        _join(broker, worker, actors.portfolio_batch_task.queue_name)

    events = DEFAULT_STATUS_STORE.events("batch:fail-1")
    assert [e["state"] for e in events] == ["running", "failed"]
    assert "OSError" in events[-1]["detail"]["error"]


def test_portfolio_batch_task_processes_batch(stub_worker: Any, tmp_path: Any, monkeypatch: Any) -> None:
    broker, worker = stub_worker
    import plot_agent.portfolio as portfolio_mod
    from plot_reports import LocalArtifactStore

    # Keep batch artifacts inside tmp_path.
    real = portfolio_mod._export_artifacts
    monkeypatch.setattr(
        portfolio_mod,
        "_export_artifacts",
        lambda bid, ranked, artifact_store=None: real(
            bid, ranked, artifact_store=LocalArtifactStore(tmp_path)
        ),
    )
    actors.portfolio_batch_task.send("batch:stub-1", [{"parcel_id": "P1"}, {"parcel_id": "P2"}])
    _join(broker, worker, actors.portfolio_batch_task.queue_name)
    events = DEFAULT_STATUS_STORE.events("batch:stub-1")
    assert [e["state"] for e in events] == ["running", "complete"]
    assert events[-1]["detail"]["analyzed"] == 2
    assert events[-1]["detail"]["artifacts"]["csv"]


def test_monitoring_check_task_runs_registered_monitor(stub_worker: Any) -> None:
    broker, worker = stub_worker
    from plot_agent.monitoring import DEFAULT_MONITOR_STORE, create_monitor

    monitor = create_monitor("municipality", "1412011", "test")
    try:
        actors.monitoring_check_task.send(monitor.monitor_id)
        _join(broker, worker, actors.monitoring_check_task.queue_name)
        events = DEFAULT_STATUS_STORE.events(f"task:monitoring:{monitor.monitor_id}")
        assert [e["state"] for e in events] == ["running", "complete"]
        # Empty planning store → baseline snapshot, no alert (no_change).
        assert events[-1]["detail"]["status"] == "no_change"
    finally:
        DEFAULT_MONITOR_STORE.clear()


def test_usecase_queued_path_enqueues_when_broker_configured(
    stub_worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """portfolio_analyze/cache_warm enqueue when the env configures a broker."""
    broker, worker = stub_worker
    from plot_mcp_server import usecases

    monkeypatch.setenv("PLOT_QUEUE_ENABLED", "true")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    try:
        queued = usecases.portfolio_analyze({"parcels": [{"parcel_id": "P1"}]})
        assert queued["status"] == "queued"
        warm = usecases.cache_warm("parcel", "P1")
        assert warm["status"] == "queued"
        # The messages actually landed on the broker queue and process fine.
        _join(broker, worker, actors.portfolio_batch_task.queue_name)
        events = DEFAULT_STATUS_STORE.events(queued["batch_id"])
        assert events and events[-1]["state"] == "complete"
    finally:
        monkeypatch.setenv("PLOT_QUEUE_ENABLED", "false")
        cfg.get_settings.cache_clear()


def test_no_broker_fallback_runs_cache_warm_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Graceful degradation: queue disabled → cache_warm runs in-proc, real result."""
    from plot_mcp_server import usecases

    monkeypatch.setenv("PLOT_QUEUE_ENABLED", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    usecases.set_connectors(mock_connectors())
    try:
        result = usecases.cache_warm("parcel", "141201_1.0001.1867/2")
    finally:
        usecases.set_connectors(None)
        cfg.get_settings.cache_clear()
    assert result["status"] == "warmed"  # not "queued" — ran synchronously
    assert result["warmed_count"] > 0
    themes = {w["theme"] for w in result["warmed"]}
    assert "flood" in themes  # the MVP risk themes were fetched sequentially


def test_cache_warm_municipality_without_bbox_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from plot_agent.warming import warm_cache

    result = asyncio.run(
        warm_cache("municipality", "1412011", connectors=mock_connectors())
    )
    assert result["status"] == "bbox_required"  # never warmed against a guess
    assert result["warmed"] == []


def test_cache_warm_backpressure_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-PERF-014: sequential warm sleeps between theme fetches."""
    import asyncio

    from plot_agent import warming

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(warming.asyncio, "sleep", _fake_sleep)
    result = asyncio.run(
        warm_cache_with_delay()
    )
    assert result["warmed_count"] > 0
    assert sleeps and all(s == 0.2 for s in sleeps)
    assert len(sleeps) == len(result["warmed"]) - 1  # between fetches only


async def warm_cache_with_delay() -> dict[str, Any]:
    from plot_agent.warming import warm_cache

    return await warm_cache(
        "parcel", "141201_1.0001.1867/2", connectors=mock_connectors(), delay_s=0.2
    )
