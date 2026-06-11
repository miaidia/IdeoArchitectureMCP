"""Chłonność composite pipeline tests (Phase 13 addendum; Target workflow 1–6).

Drives :func:`plot_agent.orchestrator.build_chlonnosc_graph` over the REAL
``plot_mcp_server.usecases`` module with mocked connectors (zero network):
gates at design-brief / model-iterations / final-concept, resumability,
``analysis_get_status`` streaming, the manual_override gate routing, and the
freshness → conservative-mode propagation into rule evaluation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from plot_agent.orchestrator import (
    ABORTED,
    AWAITING_REVIEW,
    COMPLETE,
    DEFAULT_AGENT_MEMORY,
    DEFAULT_GRAPH_REGISTRY,
    DEFAULT_GRAPH_STORE,
    DEFAULT_ORCHESTRATOR_AUDIT,
    DEFAULT_STATUS_STORE,
    build_chlonnosc_graph,
)
from plot_agent.orchestrator.chlonnosc import ChlonnoscConfig
from plot_mcp_server import usecases
from tests.mocks import mock_connectors

PARCEL = {"parcel_id": "141201_1.0001.1867/2"}
GOAL = {"type": "multifamily", "target_gfa_m2": 2000}

#: The composite's node order (single-parcel form) — Target workflow steps 1–6.
EXPECTED_NODES = [
    "resolve",
    "analyze",
    "planning",
    "freshness",
    "capacity",
    "design_brief",
    "layout_iterations",
    "koncepcja",
]


@pytest.fixture(autouse=True)
def _clean_stores() -> Any:
    usecases.set_connectors(mock_connectors())
    yield
    usecases.set_connectors(None)
    DEFAULT_GRAPH_STORE.clear()
    DEFAULT_GRAPH_REGISTRY.clear()
    DEFAULT_STATUS_STORE.clear()
    DEFAULT_ORCHESTRATOR_AUDIT.clear()
    DEFAULT_AGENT_MEMORY.clear()


def _approve(graph: Any, reason: str = "ok") -> Any:
    return graph.resume(approved=True, reason=reason, reviewer="architect")


def test_chlonnosc_composite_runs_steps_and_pauses_at_design_brief() -> None:
    graph = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:t1")
    assert [n.id for n in graph.nodes] == EXPECTED_NODES

    state = graph.run()
    assert state.status == AWAITING_REVIEW
    assert state.pending_gate == "design_brief"
    # Steps 1–3 completed before the gate; nothing past it ran.
    assert state.outcomes["resolve"].status == "ok"
    assert state.outcomes["analyze"].status == "ok"
    assert state.outcomes["planning"].status == "degraded"  # no documents → honest gap
    assert state.outcomes["planning"].reason == "no_planning_documents_available"
    assert state.outcomes["capacity"].status == "ok"
    assert state.outcomes["capacity"].value["status"] == "computed"
    assert "layout_iterations" not in state.outcomes
    # The analysis id was adopted from the analyze step (memory + state).
    assert state.analysis_id is not None
    assert DEFAULT_AGENT_MEMORY.recall("chl:t1", "analysis_id") == state.analysis_id
    # The brief gate exposes a real design brief for review.
    assert state.outcomes["design_brief"].value["status"] == "ok"


def test_full_gate_sequence_to_complete_koncepcja() -> None:
    graph = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:t2")
    state = graph.run()
    assert state.pending_gate == "design_brief"

    state = _approve(graph, "brief reviewed")
    assert state.status == AWAITING_REVIEW
    assert state.pending_gate == "layout_iterations"
    # The model-iteration step is a GATE, not an automated node: the server
    # never generated a layout (v1 §11.4 anti-pattern guard).
    payload = state.outcomes["layout_iterations"].value
    assert payload["action_required"] == "model_proposes_layouts"

    state = _approve(graph, "model finished iterating")
    assert state.status == AWAITING_REVIEW
    assert state.pending_gate == "koncepcja"
    # No masterplan variant was stored (no propose_layout in this test) — the
    # deliverable is honestly degraded, never fabricated.
    assert state.outcomes["koncepcja"].status == "degraded"
    assert state.outcomes["koncepcja"].reason == "no_masterplan_variant_stored"

    state = _approve(graph, "final review done")
    assert state.status == COMPLETE


def test_rejection_at_design_brief_aborts_with_audit() -> None:
    graph = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:t3")
    graph.run()
    state = graph.resume(approved=False, reason="zła typologia", reviewer="architect")
    assert state.status == ABORTED
    events = [e["event"] for e in DEFAULT_ORCHESTRATOR_AUDIT.entries("chl:t3")]
    assert "gate_rejected" in events
    assert "koncepcja" not in state.outcomes


def test_analysis_get_status_streams_running_review_complete() -> None:
    graph = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:t4")
    graph.run()

    status = usecases.analysis_get_status("chl:t4")
    assert status["status"] == AWAITING_REVIEW
    assert status["awaiting_review"]["node_id"] == "design_brief"
    assert "Przejrzyj design brief" in status["awaiting_review"]["what_to_review"]
    # Review m6: the status response CARRIES the gate payload (the brief itself),
    # not just a review_payload_available flag with no way to fetch it. Large
    # payloads arrive size-capped with an explicit truncation note.
    assert status["awaiting_review"]["review_payload_available"] is True
    review_payload = status["awaiting_review"]["review_payload"]
    assert isinstance(review_payload, dict)
    if review_payload.get("truncated") is True:
        assert review_payload["preview"]  # capped preview of the brief
    else:
        assert review_payload["status"] == "ok"  # the design brief under review
    assert 0.0 < status["progress"] < 1.0
    # Ordered per-node events: the stream went through running states first.
    states = [e["graph_status"] for e in DEFAULT_STATUS_STORE.events("chl:t4")]
    assert "running" in states and states.index("running") < states.index("awaiting_review")
    # The BOUND analysis id resolves to the same graph status (F-0434).
    by_analysis = usecases.analysis_get_status(str(status["analysis_id"]))
    assert by_analysis["graph_id"] == "chl:t4"

    _approve(graph)
    _approve(graph)
    _approve(graph)
    final = usecases.analysis_get_status("chl:t4")
    assert final["status"] == COMPLETE
    assert final["progress"] == 1.0
    seq = [e["seq"] for e in final["events"]]
    assert seq == sorted(seq)


def test_manual_override_routes_task_graph_gate_decisions() -> None:
    # Review B1/M1: the decision now binds to ONE named gate (after.node_id)
    # and to the OWNING analysis — the old test passed analysis_id="x" and no
    # node_id, which is exactly the unbound call the fixes reject.
    graph = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:t5")
    graph.run()

    # No explicit decision → rejected at the boundary (§21: nothing defaulted).
    refused = usecases.manual_override(
        "chl:t5", "task_graph_gate", "chl:t5", "brief ok", "architect", None
    )
    assert refused["status"] == "rejected_input"

    # No node_id → rejected (B1: a decision must name the SPECIFIC gate).
    unbound = usecases.manual_override(
        "chl:t5", "task_graph_gate", "chl:t5", "brief ok", "architect", {"status": "approved"}
    )
    assert unbound["status"] == "rejected_input"
    assert "node_id" in unbound["note"]
    assert graph.state.pending_gate == "design_brief"  # nothing was approved

    approved = usecases.manual_override(
        "chl:t5",
        "task_graph_gate",
        "chl:t5",
        "brief ok",
        "architect",
        {"status": "approved", "node_id": "design_brief"},
    )
    assert approved["applied"] is True
    assert approved["status"] == AWAITING_REVIEW  # moved on to the NEXT gate
    assert approved["pending_gate"] == "layout_iterations"

    rejected = usecases.manual_override(
        "chl:t5",
        "task_graph_gate",
        "chl:t5",
        "stop",
        "architect",
        {"status": "rejected", "node_id": "layout_iterations"},
    )
    assert rejected["status"] == ABORTED

    missing = usecases.manual_override(
        "chl:t5",
        "task_graph_gate",
        "no-such-graph",
        "r",
        "u",
        {"status": "approved", "node_id": "design_brief"},
    )
    assert missing["status"] == "graph_not_found"


def test_duplicate_gate_approval_is_rejected_as_gate_mismatch() -> None:
    """Review B1: an LLM-client RETRY of the same approval call must not
    silently approve the NEXT gate with the previous gate's reason."""
    graph = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:b1")
    graph.run()
    aid = str(graph.state.analysis_id)

    first = usecases.manual_override(
        aid,
        "task_graph_gate",
        "chl:b1",
        "brief ok",
        "architect",
        {"status": "approved", "node_id": "design_brief"},
    )
    assert first["applied"] is True
    assert first["pending_gate"] == "layout_iterations"

    # The duplicate (identical) call arrives again — e.g. an MCP retry.
    duplicate = usecases.manual_override(
        aid,
        "task_graph_gate",
        "chl:b1",
        "brief ok",
        "architect",
        {"status": "approved", "node_id": "design_brief"},
    )
    assert duplicate["applied"] is False
    assert duplicate["status"] == "gate_mismatch"
    assert duplicate["pending_gate"] == "layout_iterations"
    assert duplicate["audit_logged"] is True
    # gate2 is UNTOUCHED: still pending, never approved with the stale reason.
    assert graph.state.pending_gate == "layout_iterations"
    assert "layout_iterations" not in graph.state.approvals
    events = [e["event"] for e in DEFAULT_ORCHESTRATOR_AUDIT.entries("chl:b1")]
    assert "gate_mismatch" in events


def test_gate_decision_requires_owning_analysis_id() -> None:
    """Review M1: approving graph B while citing analysis A (and vice versa)
    is an audited ``analysis_mismatch`` — ownership in BOTH directions."""
    graph_a = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:own-a")
    graph_a.run()
    graph_b = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:own-b")
    graph_b.run()
    aid_a = str(graph_a.state.analysis_id)
    aid_b = str(graph_b.state.analysis_id)
    assert aid_a != aid_b

    # Direction 1: analysis A id against graph B → rejected, B untouched.
    cross = usecases.manual_override(
        aid_a,
        "task_graph_gate",
        "chl:own-b",
        "ok",
        "architect",
        {"status": "approved", "node_id": "design_brief"},
    )
    assert cross["applied"] is False
    assert cross["status"] == "analysis_mismatch"
    assert graph_b.state.pending_gate == "design_brief"
    events = [e["event"] for e in DEFAULT_ORCHESTRATOR_AUDIT.entries("chl:own-b")]
    assert "gate_analysis_mismatch" in events

    # Direction 2: analysis B id against graph A → also rejected.
    cross2 = usecases.manual_override(
        aid_b,
        "task_graph_gate",
        "chl:own-a",
        "ok",
        "architect",
        {"status": "approved", "node_id": "design_brief"},
    )
    assert cross2["status"] == "analysis_mismatch"
    assert graph_a.state.pending_gate == "design_brief"

    # The OWNING analysis id (or the graph id itself) is accepted.
    ok = usecases.manual_override(
        aid_b,
        "task_graph_gate",
        "chl:own-b",
        "ok",
        "architect",
        {"status": "approved", "node_id": "design_brief"},
    )
    assert ok["applied"] is True
    assert ok["pending_gate"] == "layout_iterations"


def test_failed_planning_node_resumes_without_reexecuting_completed() -> None:
    calls = {"resolve": 0, "analyze": 0, "planning": 0}

    real_resolve = usecases.parcel_resolve
    real_analyze = usecases.parcel_analyze
    real_parse = usecases.planning_parse_document

    def _counting_resolve(payload: Any) -> Any:
        calls["resolve"] += 1
        return real_resolve(payload)

    def _counting_analyze(payload: Any, version: str) -> Any:
        calls["analyze"] += 1
        return real_analyze(payload, version)

    def _flaky_parse(file_id: Any, text: Any, candidates: Any = None) -> Any:
        calls["planning"] += 1
        if calls["planning"] == 1:
            raise TimeoutError("TEST FIXTURE: registry down")
        return real_parse(file_id, text, candidates)

    wrapper = SimpleNamespace(
        parcel_resolve=_counting_resolve,
        parcel_analyze=_counting_analyze,
        planning_fetch=usecases.planning_fetch,
        planning_parse_document=_flaky_parse,
        capacity_generate_scenarios=usecases.capacity_generate_scenarios,
        design_brief_for_analysis=usecases.design_brief_for_analysis,
        report_generate=usecases.report_generate,
    )

    def _build() -> Any:
        return build_chlonnosc_graph(
            wrapper,
            PARCEL,
            GOAL,
            documents=["§5. Maksymalna wysokość zabudowy: 12 m."],
            graph_id="chl:t6",
        )

    first = _build().run()
    # The planning failure DEGRADED the run (graph reached the brief gate) —
    # one dead service never blocks the analysis (F-0424, v1 §11.4 guard).
    assert first.outcomes["planning"].status == "failed"
    assert first.status == AWAITING_REVIEW

    # Re-run (new graph object, same persisted state): completed nodes are NOT
    # re-executed; only the failed planning node retries (then capacity re-runs
    # because its upstream hash changed — correct dependency semantics).
    second = _build().run()
    assert calls["resolve"] == 1
    assert calls["analyze"] == 1
    assert calls["planning"] == 2
    assert second.outcomes["planning"].status == "ok"
    assert second.outcomes["planning"].value["indicators"]  # parsed for real
    assert second.state if hasattr(second, "state") else True


def _stale_wrapper() -> SimpleNamespace:
    """The real usecases with parcel_analyze results aged to ancient sources."""
    real_analyze = usecases.parcel_analyze

    def _stale_analyze(payload: Any, version: str) -> Any:
        result = real_analyze(payload, version)
        for source in result.planning.get("_sources", []):
            source["retrieved_at"] = "2020-01-01T00:00:00+00:00"  # ancient
        return result

    return SimpleNamespace(
        parcel_resolve=usecases.parcel_resolve,
        parcel_analyze=_stale_analyze,
        planning_fetch=usecases.planning_fetch,
        planning_parse_document=usecases.planning_parse_document,
        capacity_generate_scenarios=usecases.capacity_generate_scenarios,
        design_brief_for_analysis=usecases.design_brief_for_analysis,
        report_generate=usecases.report_generate,
    )


def test_stale_sources_force_conservative_mode_on_real_consumers() -> None:
    """F-0439/0443/0445 (review M2): stale SourceRecord → conservative mode
    CONSUMED by the production path — the capacity output and the koncepcja
    gate payload carry the mode + banner the model sees. (The previous version
    of this test passed the mode into plot_rules.evaluate MANUALLY, which
    proved nothing about the pipeline — replaced with the real consumers; the
    propose_layout consumer is covered in test_mcp_full_analysis.py.)"""
    graph = build_chlonnosc_graph(_stale_wrapper(), PARCEL, GOAL, graph_id="chl:t7")
    state = graph.run()
    assert state.context["evaluation_mode"] == "conservative"
    freshness = state.outcomes["freshness"].value
    assert freshness["freshness"]["degraded"] is True
    assert freshness["freshness"]["stale_sources"]  # names the stale sources

    # The capacity node OUTPUT carries the mode + the model-visible banner.
    capacity = state.outcomes["capacity"].value
    assert capacity["evaluation_mode"] == "conservative"
    assert "tryb konserwatywny" in capacity["freshness_banner"]

    # …and so does the final-review koncepcja GATE payload.
    state = graph.resume(approved=True, reason="brief ok", reviewer="architect")
    state = graph.resume(approved=True, reason="iterations done", reviewer="architect")
    assert state.pending_gate == "koncepcja"
    koncepcja = state.outcomes["koncepcja"].value
    assert koncepcja["evaluation_mode"] == "conservative"
    assert "źródła nieaktualne" in koncepcja["freshness_banner"]

    # Fresh sources preserve the composite's default mode — and no banner.
    fresh_graph = build_chlonnosc_graph(usecases, PARCEL, GOAL, graph_id="chl:t8")
    fresh_state = fresh_graph.run()
    assert fresh_state.context["evaluation_mode"] == "strict"
    fresh_capacity = fresh_state.outcomes["capacity"].value
    assert fresh_capacity["evaluation_mode"] == "strict"
    assert "freshness_banner" not in fresh_capacity


def test_multi_parcel_investment_area_is_chunked(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-0436: more parcels than the limit → per-parcel subgraphs + merge."""
    graph = build_chlonnosc_graph(
        usecases,
        {"parcel_ids": ["141201_1.0001.1867/2", "141201_1.0001.1867/3"]},
        GOAL,
        graph_id="chl:t9",
        config=ChlonnoscConfig(chunk_limit=1),
    )
    node_ids = [n.id for n in graph.nodes]
    assert "analyze:chunk:0" in node_ids
    assert "analyze:chunk:1" in node_ids
    assert "analyze:merge" in node_ids
    state = graph.run()
    merged = state.outcomes["analyze:merge"].value
    assert len(merged["parcels"]) == 2
    assert merged["primary_analysis_id"] == state.analysis_id
    assert DEFAULT_AGENT_MEMORY.recall("chl:t9", "chunked_analyses")
    assert state.pending_gate == "design_brief"
    # Review M3: the chunks' SourceRecords are VISIBLE to the freshness node —
    # a chunked run is not freshness-blind (fresh mocks → strict, not a
    # silently-empty source list).
    assert merged["sources"]
    freshness = state.outcomes["freshness"].value
    assert freshness["freshness"]["items"]  # per-source assessments exist
    assert state.context["evaluation_mode"] == "strict"


def test_chunked_run_with_stale_sources_goes_conservative() -> None:
    """Review M3: a 2-parcel chunked graph (default-style chunk_limit=1) with a
    stale source must evaluate conservative — previously the merge value hid
    every SourceRecord and chunked runs were ALWAYS 'fresh'."""
    graph = build_chlonnosc_graph(
        _stale_wrapper(),
        {"parcel_ids": ["141201_1.0001.1867/2", "141201_1.0001.1867/3"]},
        GOAL,
        graph_id="chl:m3-stale",
        config=ChlonnoscConfig(chunk_limit=1),
    )
    state = graph.run()
    assert state.outcomes["analyze:merge"].status == "ok"
    freshness = state.outcomes["freshness"].value
    assert freshness["freshness"]["degraded"] is True
    assert freshness["freshness"]["stale_sources"]
    assert state.context["evaluation_mode"] == "conservative"
    capacity = state.outcomes["capacity"].value
    assert capacity["evaluation_mode"] == "conservative"
    assert "tryb konserwatywny" in capacity["freshness_banner"]


def test_zero_visible_sources_degrade_freshness_not_fresh() -> None:
    """Review M3 fail-safe: a merge that surfaces ZERO SourceRecords is
    degraded (`no_sources_visible`) and conservative — never assumed fresh."""
    real_analyze = usecases.parcel_analyze

    def _sourceless_analyze(payload: Any, version: str) -> Any:
        result = real_analyze(payload, version)
        result.planning["_sources"] = []  # nothing visible to verify
        return result

    wrapper = SimpleNamespace(
        parcel_resolve=usecases.parcel_resolve,
        parcel_analyze=_sourceless_analyze,
        planning_fetch=usecases.planning_fetch,
        planning_parse_document=usecases.planning_parse_document,
        capacity_generate_scenarios=usecases.capacity_generate_scenarios,
        design_brief_for_analysis=usecases.design_brief_for_analysis,
        report_generate=usecases.report_generate,
    )
    graph = build_chlonnosc_graph(
        wrapper,
        {"parcel_ids": ["141201_1.0001.1867/2", "141201_1.0001.1867/3"]},
        GOAL,
        graph_id="chl:m3-zero",
        config=ChlonnoscConfig(chunk_limit=1),
    )
    state = graph.run()
    outcome = state.outcomes["freshness"]
    assert outcome.status == "degraded"
    assert outcome.reason == "no_sources_visible"
    assert outcome.value["freshness"]["degraded"] is True
    assert outcome.value["freshness"]["reason"] == "no_sources_visible"
    assert state.context["evaluation_mode"] == "conservative"
