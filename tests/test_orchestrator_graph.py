"""TaskGraph orchestrator core tests (Phase 13 / v1 Phase 11 §11.1.1; F-0421–0438).

Covers: DAG validation, degradation on failure (one dead step never blocks the
graph), source fallback with audit, idempotent resumable execution (call-count
assertions), kill-mid-run simulation, manual-review gates (approve/reject +
audit), cache-aware planning, streaming status order, JSON-serializable state,
agent memory, and InvestmentArea chunking (F-0436).
"""

from __future__ import annotations

import json
import threading

import pytest
from plot_agent.orchestrator import (
    ABORTED,
    AWAITING_REVIEW,
    COMPLETE,
    AgentMemory,
    GateMismatchError,
    GraphStore,
    GraphValidationError,
    NodeContext,
    OrchestratorAudit,
    StatusStore,
    TaskGraph,
    TaskNode,
    add_chunked_nodes,
    degraded,
)


def _fresh_stores() -> dict[str, object]:
    return {
        "store": GraphStore(),
        "audit": OrchestratorAudit(),
        "status": StatusStore(),
        "memory": AgentMemory(),
    }


def _graph(graph_id: str = "g:test", **stores: object) -> TaskGraph:
    return TaskGraph(graph_id, **(stores or _fresh_stores()))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# DAG validation
# --------------------------------------------------------------------------- #
def test_duplicate_node_id_rejected() -> None:
    graph = _graph()
    graph.add(TaskNode(id="a", run=lambda ctx: 1))
    with pytest.raises(GraphValidationError):
        graph.add(TaskNode(id="a", run=lambda ctx: 2))


def test_cycle_detected() -> None:
    graph = _graph()
    graph.add(TaskNode(id="a", run=lambda ctx: 1, depends_on=("b",)))
    graph.add(TaskNode(id="b", run=lambda ctx: 2, depends_on=("a",)))
    with pytest.raises(GraphValidationError):
        graph.run()


def test_unknown_dependency_rejected() -> None:
    graph = _graph()
    graph.add(TaskNode(id="a", run=lambda ctx: 1, depends_on=("ghost",)))
    with pytest.raises(GraphValidationError):
        graph.run()


def test_duplicate_depends_on_is_not_a_false_cycle() -> None:
    """Review m1: ``depends_on=("a", "a")`` inflated the indegree (counted twice,
    decremented once) and produced a FALSE cycle error."""
    graph = _graph("g:dupdep", **_fresh_stores())
    graph.add(TaskNode(id="a", run=lambda ctx: "A"))
    graph.add(TaskNode(id="b", run=lambda ctx: ctx.upstream_value("a"), depends_on=("a", "a")))
    state = graph.run()
    assert state.status == COMPLETE
    assert state.outcomes["b"].value == "A"


# --------------------------------------------------------------------------- #
# Degradation (F-0424): a failing node never blocks the graph.
# --------------------------------------------------------------------------- #
def test_failed_node_degrades_downstream_but_graph_completes() -> None:
    stores = _fresh_stores()
    graph = _graph("g:degrade", **stores)
    graph.add(TaskNode(id="boom", run=lambda ctx: 1 / 0))
    seen: dict[str, bool] = {}

    def _downstream(ctx: NodeContext) -> str:
        seen["upstream_ok"] = ctx.upstream_ok("boom")
        if not ctx.upstream_ok("boom"):
            return degraded("partial", "upstream boom failed")  # type: ignore[return-value]
        return "full"

    graph.add(TaskNode(id="consumer", run=_downstream, depends_on=("boom",)))
    state = graph.run()
    assert state.status == COMPLETE
    assert state.outcomes["boom"].status == "failed"
    assert "ZeroDivisionError" in (state.outcomes["boom"].error or "")
    assert seen["upstream_ok"] is False
    assert state.outcomes["consumer"].status == "degraded"
    assert state.outcomes["consumer"].value == "partial"


def test_hard_requires_marks_node_failed_without_executing() -> None:
    graph = _graph("g:hard", **_fresh_stores())
    graph.add(TaskNode(id="dead", run=lambda ctx: 1 / 0))
    calls: list[str] = []
    graph.add(
        TaskNode(
            id="needs_dead",
            run=lambda ctx: calls.append("ran"),
            depends_on=("dead",),
            hard_requires=("dead",),
        )
    )
    state = graph.run()
    assert state.outcomes["needs_dead"].status == "failed"
    assert "upstream_failed:dead" in (state.outcomes["needs_dead"].error or "")
    assert calls == []  # never executed
    assert state.status == COMPLETE  # graph still finishes (degradation)


# --------------------------------------------------------------------------- #
# Source fallback (F-0423) — recorded, never a silent pick (NFR-REL-007).
# --------------------------------------------------------------------------- #
def test_fallback_used_and_audited() -> None:
    stores = _fresh_stores()
    graph = _graph("g:fallback", **stores)

    def _primary(ctx: NodeContext) -> str:
        raise TimeoutError("primary source down")

    graph.add(
        TaskNode(id="fetch", run=_primary, fallbacks=(lambda ctx: "from-fallback",))
    )
    state = graph.run()
    outcome = state.outcomes["fetch"]
    assert outcome.status == "ok"
    assert outcome.value == "from-fallback"
    assert outcome.source == "fallback:0"  # which source produced the result
    audit: OrchestratorAudit = stores["audit"]  # type: ignore[assignment]
    events = [e["event"] for e in audit.entries("g:fallback")]
    assert "node_source_failed" in events
    assert "fallback_used" in events


# --------------------------------------------------------------------------- #
# Idempotency + resume (F-0431/0433; NFR-REL-002/003).
# --------------------------------------------------------------------------- #
def test_rerun_skips_completed_and_retries_failed_node() -> None:
    stores = _fresh_stores()
    calls = {"a": 0, "flaky": 0, "c": 0}

    def _build() -> TaskGraph:
        graph = TaskGraph("g:resume", **stores)  # type: ignore[arg-type]

        def _a(ctx: NodeContext) -> str:
            calls["a"] += 1
            return "A"

        def _flaky(ctx: NodeContext) -> str:
            calls["flaky"] += 1
            if calls["flaky"] == 1:
                raise RuntimeError("transient")
            return "B"

        def _c(ctx: NodeContext) -> str:
            calls["c"] += 1
            return f"C({ctx.upstream_value('flaky')})"

        graph.add(TaskNode(id="a", run=_a))
        graph.add(TaskNode(id="flaky", run=_flaky, depends_on=("a",)))
        graph.add(TaskNode(id="c", run=_c, depends_on=("flaky",)))
        return graph

    first = _build().run()
    assert first.outcomes["flaky"].status == "failed"
    assert first.status == COMPLETE  # degraded completion

    # Re-run with a NEW graph object over the same persisted state (the
    # kill/re-run contract): completed nodes are NOT re-executed.
    second = _build().run()
    assert calls["a"] == 1  # idempotent: never re-executed
    assert calls["flaky"] == 2  # failed → retried
    assert second.outcomes["flaky"].status == "ok"
    # c re-ran because its upstream outcome (inputs hash) changed — correct
    # dependency-hash semantics, not a violation of idempotency.
    assert second.outcomes["c"].value == "C(B)"


def test_kill_mid_run_resumes_from_last_completed_node() -> None:
    stores = _fresh_stores()
    calls = {"first": 0, "victim": 0, "last": 0}

    def _build(kill: bool) -> TaskGraph:
        graph = TaskGraph("g:kill", **stores)  # type: ignore[arg-type]

        def _first(ctx: NodeContext) -> str:
            calls["first"] += 1
            return "ok"

        def _victim(ctx: NodeContext) -> str:
            calls["victim"] += 1
            if kill:
                raise KeyboardInterrupt  # simulated process kill
            return "survived"

        def _last(ctx: NodeContext) -> str:
            calls["last"] += 1
            return "done"

        graph.add(TaskNode(id="first", run=_first))
        graph.add(TaskNode(id="victim", run=_victim, depends_on=("first",)))
        graph.add(TaskNode(id="last", run=_last, depends_on=("victim",)))
        return graph

    with pytest.raises(KeyboardInterrupt):
        _build(kill=True).run()
    # State survived the kill: the completed node is persisted.
    store: GraphStore = stores["store"]  # type: ignore[assignment]
    persisted = store.get("g:kill")
    assert persisted is not None
    assert persisted.outcomes["first"].status == "ok"

    state = _build(kill=False).run()
    assert state.status == COMPLETE
    assert calls["first"] == 1  # resumed, not re-executed
    assert calls["victim"] == 2
    assert calls["last"] == 1


# --------------------------------------------------------------------------- #
# Gates (F-0430/0437)
# --------------------------------------------------------------------------- #
def test_gate_pauses_then_approval_continues() -> None:
    stores = _fresh_stores()
    graph = _graph("g:gate", **stores)
    graph.add(TaskNode(id="work", run=lambda ctx: {"draft": 1}))
    graph.add(
        TaskNode(
            id="review_me",
            run=lambda ctx: {"brief": "x"},
            depends_on=("work",),
            gate=True,
            gate_reason="review the brief",
        )
    )
    after_calls: list[str] = []
    graph.add(
        TaskNode(id="after", run=lambda ctx: after_calls.append("ran"), depends_on=("review_me",))
    )

    state = graph.run()
    assert state.status == AWAITING_REVIEW
    assert state.pending_gate == "review_me"
    assert after_calls == []  # nothing past the gate ran
    # The gate node itself EXECUTED (its output is what gets reviewed).
    assert state.outcomes["review_me"].status == "ok"

    state = graph.resume(approved=True, reason="brief OK", reviewer="architect")
    assert state.status == COMPLETE
    assert after_calls == ["ran"]
    audit: OrchestratorAudit = stores["audit"]  # type: ignore[assignment]
    events = [e["event"] for e in audit.entries("g:gate")]
    assert "gate_paused" in events
    assert "gate_approved" in events


def test_resume_bound_to_expected_gate_rejects_duplicate_approval() -> None:
    """Review B1: a duplicate approval (same expected_gate) must never silently
    approve the NEXT gate — it raises an audited GateMismatchError and the
    second gate stays untouched."""
    stores = _fresh_stores()
    graph = _graph("g:twogates", **stores)
    graph.add(TaskNode(id="g1", run=lambda ctx: "first", gate=True))
    graph.add(TaskNode(id="g2", run=lambda ctx: "second", depends_on=("g1",), gate=True))

    assert graph.run().pending_gate == "g1"
    state = graph.resume(approved=True, reason="g1 ok", expected_gate="g1")
    assert state.pending_gate == "g2"  # moved on to the second gate

    # The duplicate approval call (an MCP client retry) names g1 again.
    with pytest.raises(GateMismatchError) as err:
        graph.resume(approved=True, reason="g1 ok", expected_gate="g1")
    assert err.value.pending_gate == "g2"
    # g2 is UNTOUCHED: still pending, no approval recorded, reason not reused.
    assert graph.state.pending_gate == "g2"
    assert "g2" not in graph.state.approvals
    assert graph.state.status == AWAITING_REVIEW
    audit: OrchestratorAudit = stores["audit"]  # type: ignore[assignment]
    mismatches = [e for e in audit.entries("g:twogates") if e["event"] == "gate_mismatch"]
    assert mismatches and mismatches[0]["detail"]["pending_gate"] == "g2"

    # The correctly-bound decision still works.
    state = graph.resume(approved=True, reason="g2 ok", expected_gate="g2")
    assert state.status == COMPLETE


def test_concurrent_resume_decides_gate_exactly_once() -> None:
    """Review B1 (TOCTOU): two concurrent resume() calls for the same gate are
    serialized under the graph's lock — exactly one decides it."""
    stores = _fresh_stores()
    graph = _graph("g:race", **stores)
    graph.add(TaskNode(id="g1", run=lambda ctx: "first", gate=True))
    graph.add(TaskNode(id="g2", run=lambda ctx: "second", depends_on=("g1",), gate=True))
    assert graph.run().pending_gate == "g1"

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def _approve() -> None:
        barrier.wait()
        try:
            graph.resume(approved=True, reason="race", expected_gate="g1")
            result = "approved"
        except GateMismatchError:
            result = "mismatch"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=_approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["approved", "mismatch"]  # exactly one decision
    assert list(graph.state.approvals) == ["g1"]
    assert graph.state.pending_gate == "g2"  # the race never reached g2


def test_gate_pause_exposes_capped_review_payload() -> None:
    """Review m6: the paused state CARRIES the (size-capped) review payload —
    not just a 'payload available' flag the caller cannot act on."""
    from plot_agent.orchestrator.graph import REVIEW_PAYLOAD_MAX_CHARS

    stores = _fresh_stores()
    graph = _graph("g:review", **stores)
    graph.add(
        TaskNode(
            id="brief",
            run=lambda ctx: {"typology": "multifamily", "axes": [1, 2]},
            gate=True,
            gate_reason="review the brief",
        )
    )
    state = graph.run()
    assert state.pending_review is not None
    assert state.pending_review["node_id"] == "brief"
    assert state.pending_review["review_payload"] == {"typology": "multifamily", "axes": [1, 2]}
    store: GraphStore = stores["store"]  # type: ignore[assignment]
    snapshot = store.snapshot("g:review")
    assert snapshot is not None
    json.dumps(snapshot)  # still JSON-serializable with the payload included
    assert snapshot["pending_review"]["review_payload"]["typology"] == "multifamily"

    # The decision clears the pending review payload.
    state = graph.resume(approved=True, expected_gate="brief")
    assert state.status == COMPLETE
    assert state.pending_review is None

    # An oversized payload is truncated with an explicit note (NFR-PERF-009).
    big_graph = _graph("g:review-big", **_fresh_stores())
    big_graph.add(
        TaskNode(id="huge", run=lambda ctx: {"blob": "x" * (REVIEW_PAYLOAD_MAX_CHARS + 100)}, gate=True)
    )
    big_state = big_graph.run()
    assert big_state.pending_review is not None
    payload = big_state.pending_review["review_payload"]
    assert payload["truncated"] is True
    assert len(payload["preview"]) <= REVIEW_PAYLOAD_MAX_CHARS
    assert "obcięty" in payload["note"]


def test_gate_rejection_aborts_and_audits() -> None:
    stores = _fresh_stores()
    graph = _graph("g:reject", **stores)
    graph.add(TaskNode(id="brief", run=lambda ctx: "draft", gate=True))
    ran: list[str] = []
    graph.add(TaskNode(id="after", run=lambda ctx: ran.append("x"), depends_on=("brief",)))

    assert graph.run().status == AWAITING_REVIEW
    state = graph.resume(approved=False, reason="wrong typology", reviewer="architect")
    assert state.status == ABORTED
    assert ran == []
    audit: OrchestratorAudit = stores["audit"]  # type: ignore[assignment]
    rejected = [e for e in audit.entries("g:reject") if e["event"] == "gate_rejected"]
    assert rejected and rejected[0]["detail"]["reason"] == "wrong typology"
    # An aborted graph stays aborted on re-run.
    assert graph.run().status == ABORTED


# --------------------------------------------------------------------------- #
# Cache-aware planning (F-0432)
# --------------------------------------------------------------------------- #
def test_cache_probe_skips_fetch_node() -> None:
    graph = _graph("g:cache", **_fresh_stores())
    calls: list[str] = []
    graph.add(
        TaskNode(id="fetch", run=lambda ctx: calls.append("fetched"), cache_probe=lambda: True)
    )
    state = graph.run()
    assert calls == []
    assert state.outcomes["fetch"].status == "skipped_cached"
    assert state.outcomes["fetch"].source == "cache"


# --------------------------------------------------------------------------- #
# Streaming status (F-0434): per-node updates, in order.
# --------------------------------------------------------------------------- #
def test_status_events_per_node_in_order() -> None:
    stores = _fresh_stores()
    seen: list[dict[str, object]] = []
    graph = TaskGraph("g:status", on_event=seen.append, **stores)  # type: ignore[arg-type]
    graph.add(TaskNode(id="one", run=lambda ctx: 1))
    graph.add(TaskNode(id="two", run=lambda ctx: 2, depends_on=("one",)))
    graph.run()

    status: StatusStore = stores["status"]  # type: ignore[assignment]
    events = status.events("g:status")
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    node_events = [(e["node_id"], e["state"]) for e in events if e["node_id"]]
    assert node_events.index(("one", "running")) < node_events.index(("one", "ok"))
    assert node_events.index(("one", "ok")) < node_events.index(("two", "running"))
    assert node_events.index(("two", "running")) < node_events.index(("two", "ok"))
    # callback received the same stream
    assert [e["state"] for e in seen if e["node_id"] == "two"] == ["running", "ok"]
    # progress is monotone non-decreasing across node completions
    progress = [e["progress"] for e in events if e["state"] == "ok"]
    assert progress == sorted(progress)


# --------------------------------------------------------------------------- #
# Persistence is JSON-serializable; agent memory works (F-0438).
# --------------------------------------------------------------------------- #
def test_state_snapshot_json_serializable_and_memory_recall() -> None:
    stores = _fresh_stores()
    graph = _graph("g:json", **stores)

    def _remember(ctx: NodeContext) -> dict[str, object]:
        ctx.memory.remember("decision", "go-north")
        return {"k": [1, 2], "nested": {"x": None}}

    def _recall(ctx: NodeContext) -> str:
        return str(ctx.memory.recall("decision"))

    graph.add(TaskNode(id="m1", run=_remember))
    graph.add(TaskNode(id="m2", run=_recall, depends_on=("m1",)))
    state = graph.run()
    assert state.outcomes["m2"].value == "go-north"  # later node recalled the note

    store: GraphStore = stores["store"]  # type: ignore[assignment]
    snapshot = store.snapshot("g:json")
    assert snapshot is not None
    json.dumps(snapshot)  # must not raise — JSON-serializable contract
    memory: AgentMemory = stores["memory"]  # type: ignore[assignment]
    assert memory.notes("g:json")["decision"] == "go-north"


# --------------------------------------------------------------------------- #
# Hash stability (review m2): sets must project deterministically.
# --------------------------------------------------------------------------- #
def test_set_params_project_sorted_and_hash_stable() -> None:
    """Review m2: an unsorted set projection made the idempotency inputs hash
    depend on set iteration order (process-dependent under hash randomization)."""
    from plot_agent.orchestrator.graph import _jsonable

    assert _jsonable({"b", "a", "c"}) == ["a", "b", "c"]
    assert _jsonable(frozenset({3, 1, 2})) == [1, 2, 3]
    # Mixed types sort deterministically by their string projection.
    assert _jsonable({1, "1x", 2}) == sorted([1, "1x", 2], key=str)

    def _hash_with(themes: set[str]) -> str:
        graph = _graph(f"g:set-{id(themes)}", **_fresh_stores())
        node = graph.add(TaskNode(id="fetch", run=lambda ctx: 1, params={"themes": themes}))
        return graph._inputs_hash(node)

    # Same set built with different insertion histories → identical hash.
    one = {"flood", "heritage", "landslide"}
    two = set()
    for theme in ["landslide", "flood", "noise", "heritage"]:
        two.add(theme)
    two.discard("noise")
    assert one == two
    assert _hash_with(one) == _hash_with(two)


# --------------------------------------------------------------------------- #
# Chunking (F-0436)
# --------------------------------------------------------------------------- #
def test_chunked_nodes_split_and_merge() -> None:
    graph = _graph("g:chunks", **_fresh_stores())
    items = ["p1", "p2", "p3"]

    def _make(item: str):
        return lambda ctx: f"analyzed:{item}"

    def _merge(ctx: NodeContext) -> list[str]:
        return sorted(str(ctx.upstream_value(dep)) for dep in ctx.upstream)

    merge_id = add_chunked_nodes(
        graph, base_id="area", items=items, make_run=_make, merge=_merge
    )
    state = graph.run()
    assert merge_id == "area:merge"
    assert state.outcomes["area:merge"].value == [
        "analyzed:p1",
        "analyzed:p2",
        "analyzed:p3",
    ]
    assert {f"area:chunk:{i}" for i in range(3)} <= set(state.outcomes)
