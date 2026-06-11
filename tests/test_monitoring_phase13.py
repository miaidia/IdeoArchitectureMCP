"""Monitoring + freshness tests (Phase 13 / v1 Phase 11 §11.1.2–3; §4.5, F-0418/0439–0446).

* MPZP change simulation: act-state v1 → v2 → alert with the NEW act id in the
  semantic diff; identical state → no alert; first check = baseline only.
* Webhook alerts: POSTed to an ALLOWLISTED host (respx, zero live network);
  a non-allowlisted host is BLOCKED by the existing egress allowlist (F-0418).
* Source freshness: stale SourceRecord → degraded report → conservative mode;
  fresh records preserve the caller's default. Ruleset freshness flags expired/
  not-yet-in-force spans. Connector autotest uses the EXISTING healthcheck.
* monitoring_create use-case registers a real monitor with audit + config echo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from plot_agent.monitoring import (
    FreshnessConfig,
    MonitorStore,
    connector_autotest,
    create_monitor,
    diff_states,
    evaluation_mode_for,
    planning_acts_state,
    ruleset_freshness,
    run_monitor_check,
    source_freshness,
)
from plot_connectors.base.egress import EgressAllowlist

ACTS_V1 = {
    "kind": "planning_acts",
    "municipality_id": "1412011",
    "acts": {"mpzp-1": {"hash": "aaaa", "status": "in_force"}},
}
ACTS_V2 = {
    "kind": "planning_acts",
    "municipality_id": "1412011",
    "acts": {
        "mpzp-1": {"hash": "aaaa", "status": "in_force"},
        "mpzp-2-nowy": {"hash": "bbbb", "status": "in_force"},  # NEW act
    },
}


def _states_fetcher(states: list[dict[str, Any]]):
    calls = {"n": 0}

    def _fetch(monitor: Any) -> dict[str, Any]:
        state = states[min(calls["n"], len(states) - 1)]
        calls["n"] += 1
        return state

    return _fetch


# --------------------------------------------------------------------------- #
# Change detection (§4.5)
# --------------------------------------------------------------------------- #
def test_mpzp_change_emits_alert_with_new_act_id() -> None:
    store = MonitorStore()
    monitor = create_monitor("municipality", "1412011", "śledzenie zmian MPZP", store=store)

    first = run_monitor_check(
        monitor.monitor_id, store=store, fetch_state=_states_fetcher([ACTS_V1, ACTS_V2])
    )
    assert first["status"] == "no_change"  # baseline snapshot, no alert
    assert first["alert"] is None

    second = run_monitor_check(
        monitor.monitor_id, store=store, fetch_state=_states_fetcher([ACTS_V2])
    )
    assert second["status"] == "changed"
    assert second["alert"] is not None
    assert second["diff"]["new_acts"] == ["mpzp-2-nowy"]  # semantic diff names the act
    assert second["diff"]["changed_acts"] == []
    # Snapshot archive appended on every check (§4.5 "archiwum snapshotów").
    assert second["snapshot_archive_length"] == 2
    stored = store.get(monitor.monitor_id)
    assert stored is not None and len(stored.alerts) == 1


def test_no_change_means_no_alert() -> None:
    store = MonitorStore()
    monitor = create_monitor("municipality", "1412011", "test", store=store)
    run_monitor_check(monitor.monitor_id, store=store, fetch_state=_states_fetcher([ACTS_V1]))
    result = run_monitor_check(
        monitor.monitor_id, store=store, fetch_state=_states_fetcher([ACTS_V1])
    )
    assert result["status"] == "no_change"
    assert result["alert"] is None
    stored = store.get(monitor.monitor_id)
    assert stored is not None and stored.alerts == []


def test_changed_act_detected_via_hash() -> None:
    changed = {
        **ACTS_V1,
        "acts": {"mpzp-1": {"hash": "ZZZZ", "status": "amended"}},
    }
    diff = diff_states(ACTS_V1, changed)
    assert diff["changed"] is True
    assert diff["changed_acts"] == ["mpzp-1"]
    assert diff["new_acts"] == [] and diff["removed_acts"] == []


def test_planning_acts_state_reads_planning_store() -> None:
    from plot_planning import PlanningStore, parse_app_gml  # noqa: F401
    from plot_planning.store import PlanningStore as _Store

    state = planning_acts_state("no-such-gmina", planning_store=_Store())
    assert state["acts"] == {}  # empty store → empty state, never invented acts


# --------------------------------------------------------------------------- #
# Webhook through the EXISTING egress allowlist (F-0418)
# --------------------------------------------------------------------------- #
@respx.mock
def test_webhook_posted_to_allowlisted_host() -> None:
    route = respx.post("https://hooks.gov.pl/plot-alerts").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    store = MonitorStore()
    monitor = create_monitor(
        "municipality",
        "1412011",
        "alert MPZP",
        webhook_url="https://hooks.gov.pl/plot-alerts",
        store=store,
    )
    allowlist = EgressAllowlist.from_settings(resolve_dns=False)  # hosts enforced, no DNS
    run_monitor_check(
        monitor.monitor_id,
        store=store,
        fetch_state=_states_fetcher([ACTS_V1, ACTS_V2]),
        allowlist=allowlist,
    )
    result = run_monitor_check(
        monitor.monitor_id,
        store=store,
        fetch_state=_states_fetcher([ACTS_V2]),
        allowlist=allowlist,
    )
    assert result["alert"]["webhook_status"] == "sent"
    assert route.called
    body = route.calls.last.request.content.decode("utf-8")
    assert "mpzp-2-nowy" in body  # the alert payload carries the diff


@respx.mock
def test_webhook_to_non_allowlisted_host_is_blocked() -> None:
    route = respx.post("https://evil.example.com/hook").mock(
        return_value=httpx.Response(200)
    )
    store = MonitorStore()
    monitor = create_monitor(
        "municipality",
        "1412011",
        "alert MPZP",
        webhook_url="https://evil.example.com/hook",
        store=store,
    )
    allowlist = EgressAllowlist.from_settings(resolve_dns=False)
    run_monitor_check(
        monitor.monitor_id, store=store,
        fetch_state=_states_fetcher([ACTS_V1, ACTS_V2]), allowlist=allowlist,
    )
    result = run_monitor_check(
        monitor.monitor_id, store=store,
        fetch_state=_states_fetcher([ACTS_V2]), allowlist=allowlist,
    )
    # The change IS recorded; only the webhook is refused (F-0418).
    assert result["status"] == "changed"
    assert result["alert"]["webhook_status"] == "blocked_egress"
    assert not route.called  # no socket was opened to the forbidden host


# --------------------------------------------------------------------------- #
# Scheduler interface (in-memory; real cron = deployment concern)
# --------------------------------------------------------------------------- #
def test_due_monitors_interface() -> None:
    store = MonitorStore()
    monitor = create_monitor("municipality", "g1", "t", interval_s=3600.0, store=store)
    assert [m.monitor_id for m in store.due()] == [monitor.monitor_id]  # never checked
    run_monitor_check(monitor.monitor_id, store=store, fetch_state=_states_fetcher([ACTS_V1]))
    assert store.due() == []  # just checked
    later = datetime.now(UTC) + timedelta(hours=2)
    assert [m.monitor_id for m in store.due(later)] == [monitor.monitor_id]


def test_monitoring_create_usecase_registers_real_monitor() -> None:
    from plot_agent.monitoring import DEFAULT_MONITOR_STORE
    from plot_mcp_server import usecases

    result = usecases.monitoring_create(
        "municipality", "1412011", "śledzenie zmian planu", 12.0, None
    )
    try:
        assert result["active"] is True
        assert result["audit_logged"] is True
        assert result["interval_s"] == 12.0 * 3600.0
        assert result["purpose"] == "śledzenie zmian planu"
        assert DEFAULT_MONITOR_STORE.get(result["monitoring_id"]) is not None
    finally:
        DEFAULT_MONITOR_STORE.clear()


def test_monitoring_create_rejects_non_positive_interval() -> None:
    """Review m5: a monitor with interval <= 0 would be ALWAYS due (the
    scheduler would re-enqueue it on every pass) — rejected cleanly instead."""
    from plot_agent.monitoring import DEFAULT_MONITOR_STORE
    from plot_mcp_server import usecases

    try:
        for bad_interval in (-24.0, 0.0):
            result = usecases.monitoring_create(
                "municipality", "1412011", "test", bad_interval, None
            )
            assert result["status"] == "rejected_input"
            assert result["active"] is False
            assert result["monitoring_id"] is None
            assert "interval" in result["note"]
        assert DEFAULT_MONITOR_STORE.due() == []  # nothing was registered
        # None still falls back to the settings default (a valid monitor).
        ok = usecases.monitoring_create("municipality", "1412011", "test", None, None)
        assert ok["active"] is True and ok["interval_s"] > 0
    finally:
        DEFAULT_MONITOR_STORE.clear()


# --------------------------------------------------------------------------- #
# Freshness (F-0439/0440/0443/0445)
# --------------------------------------------------------------------------- #
def _source(source_id: str, age_days: float) -> dict[str, Any]:
    retrieved = datetime.now(UTC) - timedelta(days=age_days)
    return {"source_id": source_id, "retrieved_at": retrieved.isoformat()}


def test_stale_source_degrades_and_forces_conservative_mode() -> None:
    # Unit contract of the freshness check itself. How the mode reaches the
    # PRODUCTION consumers is covered by the real-path tests (review M2):
    # the chłonność payload/banner tests in test_chlonnosc_pipeline.py and the
    # bound propose_layout trace tests in test_mcp_full_analysis.py — the
    # previous version here passed the mode into plot_rules.evaluate manually,
    # which asserted nothing about any production path.
    config = FreshnessConfig(default_max_age_days=30.0)
    report = source_freshness([_source("pl.gugik.uldk", 400.0), _source("pl.isok", 1.0)], config=config)
    assert report.degraded is True
    assert report.stale_sources == ["pl.gugik.uldk"]
    assert evaluation_mode_for(report) == "conservative"


def test_fresh_sources_preserve_default_mode() -> None:
    config = FreshnessConfig(default_max_age_days=30.0)
    report = source_freshness([_source("pl.gugik.uldk", 2.0)], config=config)
    assert report.degraded is False
    assert evaluation_mode_for(report) == "strict"  # strict default preserved
    assert evaluation_mode_for(report, default="conservative") == "conservative"


def test_unparseable_retrieved_at_is_fail_safe() -> None:
    report = source_freshness([{"source_id": "x", "retrieved_at": "not-a-date"}])
    assert report.degraded is True  # unverifiable age is never assumed fresh
    assert report.items[0]["status"] == "unknown"


def test_per_source_max_age_override() -> None:
    config = FreshnessConfig(
        default_max_age_days=365.0, per_source_max_age_days={"pl.gugik.uldk": 1.0}
    )
    report = source_freshness([_source("pl.gugik.uldk:test", 5.0)], config=config)
    assert report.stale_sources == ["pl.gugik.uldk:test"]  # prefix-matched policy


def test_ruleset_freshness_flags_spans() -> None:
    from plot_rules import load_rulesets

    registry = load_rulesets("rulesets/PL")
    report = ruleset_freshness(registry)
    assert report.ruleset_version == registry.ruleset_version
    assert len(report.items) == len(registry.rules)
    # The committed corpus is dated and in force today.
    in_force = [i for i in report.items if i["status"] == "in_force"]
    assert in_force, "expected at least one in-force rule"
    # A synthetic expired rule degrades the report.
    fake_registry = type(
        "R",
        (),
        {
            "ruleset_version": "test",
            "rules": [
                type("Rule", (), {"id": "old", "valid_from": "2000-01-01", "valid_to": "2001-01-01"})()
            ],
        },
    )()
    expired = ruleset_freshness(fake_registry)
    assert expired.degraded is True
    assert expired.items[0]["status"] == "expired"


# --------------------------------------------------------------------------- #
# Connector autotest (F-0441) + diagnostics extension (F-0442)
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_connector_autotest_uses_existing_healthcheck_contract() -> None:
    class Healthy:
        async def healthcheck(self) -> Any:
            from plot_connectors import HealthStatus, ResultStatus

            return HealthStatus(
                source_id="t", healthy=True, status=ResultStatus.OK, circuit_state="closed"
            )

    class Broken:
        async def healthcheck(self) -> Any:
            raise ConnectionError("down")

    class NoProbe:
        pass

    results = await connector_autotest({"ok": Healthy(), "bad": Broken(), "none": NoProbe()})
    by_name = {r["name"]: r for r in results}
    assert by_name["ok"]["healthy"] is True
    assert by_name["bad"]["healthy"] is False and "ConnectionError" in by_name["bad"]["detail"]
    assert by_name["none"]["status"] == "no_healthcheck"  # never reported healthy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_diagnostics_run_reports_freshness_and_graph_failures() -> None:
    from plot_agent.orchestrator import DEFAULT_GRAPH_STORE, GraphState, NodeOutcome
    from plot_mcp_server import usecases
    from tests.mocks import mock_connectors

    usecases.set_connectors(mock_connectors())
    state = GraphState(graph_id="g:diag", analysis_id=None)
    state.outcomes["boom"] = NodeOutcome(
        node_id="boom", status="failed", error="RuntimeError: x", finished_at="2026-06-11T00:00:00"
    )
    DEFAULT_GRAPH_STORE.put(state)
    try:
        diag = usecases.diagnostics_run(
            ruleset_version="v",
            rule_count=1,
            dev_hot_reload=False,
            last_reload_at=None,
            server_version="test",
        )
    finally:
        DEFAULT_GRAPH_STORE.clear()
        usecases.set_connectors(None)
    assert "source_freshness" in diag and "ruleset_freshness" in diag
    assert diag["evaluation_mode"] in ("strict", "conservative")
    failures = diag["task_graph_failures"]
    assert any(f["node_id"] == "boom" for f in failures)  # last task-graph failures
    # Default diagnostics is zero-network: connectors listed but NOT probed.
    assert all(c["status"] == "not_probed" for c in diag["connectors"])
