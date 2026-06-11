"""Task-graph orchestrator core (Phase 13 / v1 Phase 11 §11.1.1; F-0421–0438).

A :class:`TaskGraph` is a declarative DAG of analysis steps. Each node is a
use-case callable plus inputs, dependencies and a gate flag. Execution is
topological with the §8.13 autonomy guarantees:

* **degradation, never blocking** (F-0424/0428, §21 / NFR-REL-001): a node that
  raises is recorded as ``failed`` and the graph CONTINUES — downstream nodes
  receive the failed outcome and degrade explicitly; one unavailable service
  never blocks the whole analysis (v1 §11.4 anti-pattern guard);
* **source fallback hooks** (F-0423/0425): a node may declare ``fallbacks`` —
  alternate callables tried in declared order when the primary raises; which
  source produced the result is RECORDED in the outcome + audit (never a silent
  random pick on conflict — NFR-REL-007);
* **idempotent execution** (F-0431/0433): every outcome is keyed by an inputs
  hash (node id + params + upstream outcome hashes); a re-run reuses completed
  outcomes whose hash is unchanged and re-executes only failed/changed nodes —
  this is what makes a killed run resumable (NFR-REL-002/003);
* **cache-aware planning** (F-0432): a fetch node may declare a ``cache_probe``;
  when it reports the connector cache warm the node is skipped as
  ``skipped_cached`` (interface only — the probe is a simple injected check);
* **manual-review gates** (F-0430/0437): a ``gate=True`` node executes, then the
  graph PAUSES with status ``awaiting_review`` exposing what to review;
  :meth:`TaskGraph.resume` records the audited approval/rejection and either
  continues or aborts;
* **streaming status** (F-0434 / NFR-PERF-010): every node transition emits an
  event through the status callback (drives ``analysis_get_status`` and
  ``ctx.report_progress``);
* **chunking** (F-0436): :func:`add_chunked_nodes` splits a too-large
  InvestmentArea into per-parcel subgraphs feeding a merge node.

A crash-style interruption (``KeyboardInterrupt`` / ``SystemExit``) is NOT
swallowed: the state is persisted and the exception re-raised, so a killed
worker resumes from the last completed node on re-run (NFR-REL-002).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from plot_shared import get_logger

_log = get_logger(__name__)

#: Cap for the review payload carried in the paused-gate state snapshot (m6):
#: ``analysis_get_status`` must expose WHAT to review without streaming an
#: unbounded blob (NFR-PERF-009).
REVIEW_PAYLOAD_MAX_CHARS = 16_000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-serializable projection of a node value (state snapshots)."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        # Sets iterate in hash order which differs across processes (hash
        # randomization) — sort for a stable projection so the idempotency
        # inputs hash (F-0431) is process-independent (review m2).
        return sorted((_jsonable(v) for v in value), key=str)
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):  # pydantic models (AnalysisResult, …)
        try:
            return dump(mode="json")
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value)


def _capped_review_payload(value: Any) -> Any:
    """JSON projection of a gate outcome, truncated for the status surface (m6)."""
    payload = _jsonable(value)
    try:
        blob = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover - defensive
        blob = str(payload)
    if len(blob) <= REVIEW_PAYLOAD_MAX_CHARS:
        return payload
    return {
        "truncated": True,
        "note": (
            f"Review payload przekracza {REVIEW_PAYLOAD_MAX_CHARS} znaków — "
            "obcięty podgląd poniżej; pełny wynik węzła w stanie grafu."
        ),
        "preview": blob[:REVIEW_PAYLOAD_MAX_CHARS],
    }


def run_maybe_async(result: Any) -> Any:
    """Drive a coroutine returned by a node callable to completion.

    Node callables may be sync or async (the §27 use-cases are sync; the
    analysis package is async). When a loop is already running on this thread
    (e.g. inside the MCP server) the coroutine is offloaded to a worker thread
    with its own loop — the same idiom as ``usecases._run_async``.
    """
    if not inspect.isawaitable(result):
        return result

    async def _await() -> Any:
        return await result

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await())  # no running loop → drive directly
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _await()).result()


@dataclass(frozen=True)
class DegradedResult:
    """Explicit degraded value a node may return (F-0424 partial results).

    The graph unwraps it: the outcome keeps ``value`` but is marked
    ``degraded`` with ``reason`` so downstream consumers SEE the gap (§21 —
    a degraded theme is never silently presented as a clean one).
    """

    value: Any
    reason: str


def degraded(value: Any, reason: str) -> DegradedResult:
    """Mark a node result as degraded/partial (F-0424)."""
    return DegradedResult(value=value, reason=reason)


@dataclass
class NodeOutcome:
    """Result of one node execution (JSON-serializable via :meth:`to_dict`)."""

    node_id: str
    status: str  # ok | degraded | failed | skipped_cached
    value: Any = None
    error: str | None = None
    reason: str | None = None
    inputs_hash: str = ""
    source: str = "run"  # run | fallback:<i> | cache | reused
    started_at: str = ""
    finished_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "degraded", "skipped_cached")

    def to_dict(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["value"] = _jsonable(self.value)
        return doc


@dataclass
class NodeContext:
    """What a node callable receives: params, upstream outcomes, memory, shared context."""

    graph_id: str
    node_id: str
    params: dict[str, Any]
    upstream: dict[str, NodeOutcome]
    context: dict[str, Any]
    memory: Any  # GraphMemoryView (duck-typed: remember/recall/notes)

    def upstream_value(self, node_id: str, default: Any = None) -> Any:
        """Value of an upstream node if it completed ok/degraded, else ``default``."""
        outcome = self.upstream.get(node_id)
        if outcome is not None and outcome.ok:
            return outcome.value
        return default

    def upstream_ok(self, node_id: str) -> bool:
        outcome = self.upstream.get(node_id)
        return outcome is not None and outcome.ok


@dataclass
class TaskNode:
    """One declarative step: a use-case callable + inputs + dependencies + gate flag."""

    id: str
    run: Callable[[NodeContext], Any]
    depends_on: tuple[str, ...] = ()
    #: Hard dependencies: if any of these FAILED, this node does not execute and
    #: is itself recorded failed with ``upstream_failed`` (degradation, F-0424).
    #: Soft dependencies (the rest of ``depends_on``) merely flow their outcome in.
    hard_requires: tuple[str, ...] = ()
    gate: bool = False
    gate_reason: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    #: Alternate sources tried in order when ``run`` raises (F-0423).
    fallbacks: tuple[Callable[[NodeContext], Any], ...] = ()
    #: Cache-aware planning probe (F-0432): True → connector cache warm → skip.
    cache_probe: Callable[[], bool] | None = None
    description: str = ""


# Graph statuses
PENDING = "pending"
RUNNING = "running"
AWAITING_REVIEW = "awaiting_review"
COMPLETE = "complete"
ABORTED = "aborted"


@dataclass
class GraphState:
    """Persistable execution state of one graph run (JSON-serializable)."""

    graph_id: str
    analysis_id: str | None = None
    status: str = PENDING
    outcomes: dict[str, NodeOutcome] = field(default_factory=dict)
    #: node_id -> {approved, reason, reviewer, at} (audited gate decisions, F-0437)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: shared cross-node context (e.g. evaluation_mode from the freshness check)
    context: dict[str, Any] = field(default_factory=dict)
    pending_gate: str | None = None
    #: WHAT the paused gate exposes for review (m6): node id, gate reason and a
    #: size-capped JSON projection of the gate node's outcome — consumed by
    #: ``analysis_get_status`` so the reviewer can actually SEE the payload.
    pending_review: dict[str, Any] | None = None
    #: total node count of the declared graph (progress denominator, F-0434)
    total_nodes: int = 0
    #: execution counts per node — idempotency evidence for tests (F-0431)
    run_counts: dict[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def progress(self, total_nodes: int | None = None) -> float:
        total = total_nodes if total_nodes else self.total_nodes
        if total <= 0:
            return 0.0
        done = sum(1 for o in self.outcomes.values() if o.ok)
        return round(min(done / total, 1.0), 4)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable snapshot (verified by ``json.dumps`` in tests)."""
        return {
            "graph_id": self.graph_id,
            "analysis_id": self.analysis_id,
            "status": self.status,
            "outcomes": {k: o.to_dict() for k, o in self.outcomes.items()},
            "approvals": _jsonable(self.approvals),
            "context": _jsonable(self.context),
            "pending_gate": self.pending_gate,
            "pending_review": _jsonable(self.pending_review),
            "total_nodes": self.total_nodes,
            "run_counts": dict(self.run_counts),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class GraphValidationError(ValueError):
    """Raised for duplicate node ids / unknown dependencies / cycles."""


class GateMismatchError(ValueError):
    """A gate decision targeted a gate that is NOT the pending one (review B1).

    Raised by :meth:`TaskGraph.resume` when ``expected_gate`` differs from the
    graph's current ``pending_gate`` — a duplicate approval call (e.g. an MCP
    client retry) must never silently approve the NEXT gate with the previous
    gate's reason. The mismatch is audited before raising.
    """

    def __init__(self, *, graph_id: str, expected_gate: str, pending_gate: str | None) -> None:
        self.graph_id = graph_id
        self.expected_gate = expected_gate
        self.pending_gate = pending_gate
        super().__init__(
            f"graph '{graph_id}': gate decision targets '{expected_gate}' but the "
            f"pending gate is {pending_gate!r}"
        )


def _topological_order(nodes: Sequence[TaskNode]) -> list[TaskNode]:
    """Kahn topological sort preserving declaration order; raises on cycles.

    ``depends_on`` duplicates are deduped (review m1): a repeated dependency
    must not inflate the indegree — it only decrements once per completed
    upstream, which previously produced a FALSE cycle error.
    """
    by_id = {n.id: n for n in nodes}
    deps_of = {n.id: tuple(dict.fromkeys(n.depends_on)) for n in nodes}
    indegree = {n.id: 0 for n in nodes}
    for node in nodes:
        for dep in deps_of[node.id]:
            if dep not in by_id:
                raise GraphValidationError(f"node '{node.id}' depends on unknown node '{dep}'")
            indegree[node.id] += 1
    order: list[TaskNode] = []
    ready = [n for n in nodes if indegree[n.id] == 0]
    while ready:
        node = ready.pop(0)
        order.append(node)
        for candidate in nodes:
            if node.id in deps_of[candidate.id]:
                indegree[candidate.id] -= 1
                if indegree[candidate.id] == 0:
                    ready.append(candidate)
    if len(order) != len(nodes):
        cyclic = sorted(set(by_id) - {n.id for n in order})
        raise GraphValidationError(f"dependency cycle involving nodes: {cyclic}")
    return order


class TaskGraph:
    """Declarative DAG of analysis steps with resumable, gated execution.

    ``store`` / ``audit`` / ``status`` / ``memory`` are duck-typed (the
    :mod:`plot_agent.orchestrator.store` defaults); injectable for tests.
    """

    def __init__(
        self,
        graph_id: str | None = None,
        *,
        analysis_id: str | None = None,
        store: Any | None = None,
        audit: Any | None = None,
        status: Any | None = None,
        memory: Any | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        from plot_agent.orchestrator.store import (
            DEFAULT_AGENT_MEMORY,
            DEFAULT_GRAPH_STORE,
            DEFAULT_ORCHESTRATOR_AUDIT,
            DEFAULT_STATUS_STORE,
        )

        self.graph_id = graph_id or f"graph:{uuid.uuid4().hex[:12]}"
        self._nodes: list[TaskNode] = []
        # Gate decisions are serialized (review B1): two concurrent resume()
        # calls must never both observe the same pending gate (TOCTOU) — the
        # second waits, then sees the gate already decided and is rejected.
        self._resume_lock = threading.Lock()
        self._store = store if store is not None else DEFAULT_GRAPH_STORE
        self._audit = audit if audit is not None else DEFAULT_ORCHESTRATOR_AUDIT
        self._status = status if status is not None else DEFAULT_STATUS_STORE
        self._memory = memory if memory is not None else DEFAULT_AGENT_MEMORY
        self._on_event = on_event
        # Resume from a persisted state when one exists (idempotent re-run,
        # F-0431/0433): a NEW TaskGraph object with the same graph_id continues
        # from the stored outcomes — the kill/re-run path.
        existing = self._store.get(self.graph_id)
        if existing is not None:
            self.state = existing
            if analysis_id is not None:
                self.state.analysis_id = analysis_id
        else:
            self.state = GraphState(graph_id=self.graph_id, analysis_id=analysis_id)
            self._store.put(self.state)

    # ------------------------------------------------------------------ #
    # Declaration
    # ------------------------------------------------------------------ #
    @property
    def nodes(self) -> tuple[TaskNode, ...]:
        return tuple(self._nodes)

    def add(self, node: TaskNode) -> TaskNode:
        if any(existing.id == node.id for existing in self._nodes):
            raise GraphValidationError(f"duplicate node id '{node.id}'")
        self._nodes.append(node)
        return node

    def node(self, node_id: str) -> TaskNode | None:
        for node in self._nodes:
            if node.id == node_id:
                return node
        return None

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def run(self) -> GraphState:
        """Execute pending nodes in topological order (resumable, gated).

        Returns the (persisted) state. Never raises on a node ``Exception`` —
        failures degrade (F-0424). ``KeyboardInterrupt``/``SystemExit`` persist
        the state and re-raise (kill semantics; resume on the next run()).
        """
        if self.state.status == ABORTED:
            return self.state
        order = _topological_order(self._nodes)
        self.state.total_nodes = len(order)
        self._set_status(RUNNING)
        try:
            for node in order:
                if not self._execute_node(node, total=len(order)):
                    return self.state  # paused at a gate
        except BaseException:
            self._persist()  # crash/kill: keep completed outcomes for resume
            raise
        self._set_status(COMPLETE)
        return self.state

    def resume(
        self,
        *,
        approved: bool,
        reason: str = "",
        reviewer: str = "",
        expected_gate: str | None = None,
    ) -> GraphState:
        """Record an audited gate decision and continue or abort (F-0430/0437).

        ``expected_gate`` (review B1) binds the decision to ONE specific gate:
        when it differs from the current ``pending_gate`` (e.g. a duplicate
        approval call retried by an MCP client after the graph moved to the
        next gate) the decision is REJECTED with an audited
        :class:`GateMismatchError` — never silently applied to whatever gate
        happens to be pending. The whole decision (check → record → continue)
        runs under the graph's resume lock, so a concurrent double-resume
        cannot both observe the same pending gate (TOCTOU).
        """
        with self._resume_lock:
            gate_id = self.state.pending_gate
            if expected_gate is not None and expected_gate != gate_id:
                self._audit_event(
                    "gate_mismatch",
                    node_id=expected_gate,
                    detail={
                        "expected_gate": expected_gate,
                        "pending_gate": gate_id,
                        "status": self.state.status,
                        "reason": reason,
                        "reviewer": reviewer,
                    },
                )
                raise GateMismatchError(
                    graph_id=self.graph_id,
                    expected_gate=expected_gate,
                    pending_gate=gate_id,
                )
            if self.state.status != AWAITING_REVIEW or gate_id is None:
                self._audit_event(
                    "resume_ignored",
                    detail={"status": self.state.status, "reason": reason},
                )
                return self.state
            decision = {
                "approved": approved,
                "reason": reason,
                "reviewer": reviewer,
                "at": _now_iso(),
            }
            self.state.approvals[gate_id] = decision
            self.state.pending_gate = None
            self.state.pending_review = None
            self._audit_event(
                "gate_approved" if approved else "gate_rejected",
                node_id=gate_id,
                detail=decision,
            )
            if not approved:
                self._set_status(ABORTED)
                self._emit(node_id=gate_id, state="aborted", detail=reason)
                return self.state
            self._emit(node_id=gate_id, state="approved", detail=reason)
            return self.run()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _execute_node(self, node: TaskNode, *, total: int) -> bool:
        """Run one node; returns False when the graph paused at a gate."""
        prior = self.state.outcomes.get(node.id)
        inputs_hash = self._inputs_hash(node)

        # Idempotency (F-0431): completed outcome with the same inputs hash → reuse.
        if prior is not None and prior.ok and prior.inputs_hash == inputs_hash:
            if node.gate and node.id not in self.state.approvals:
                return self._pause_at_gate(node, prior)
            self._emit(node_id=node.id, state="reused", progress=self._progress(total))
            return True

        # Hard upstream requirement failed → this node degrades without executing.
        failed_required = [
            dep
            for dep in node.hard_requires
            if not (dep in self.state.outcomes and self.state.outcomes[dep].ok)
        ]
        if failed_required:
            outcome = NodeOutcome(
                node_id=node.id,
                status="failed",
                error=f"upstream_failed:{','.join(failed_required)}",
                inputs_hash=inputs_hash,
                started_at=_now_iso(),
                finished_at=_now_iso(),
            )
            self._record(node, outcome, total)
            return True

        # Cache-aware planning (F-0432): warm connector cache → skip the fetch node.
        if node.cache_probe is not None:
            try:
                warm = bool(node.cache_probe())
            except Exception:  # probe failure must never block the analysis
                warm = False
            if warm:
                outcome = NodeOutcome(
                    node_id=node.id,
                    status="skipped_cached",
                    reason="connector_cache_warm",
                    inputs_hash=inputs_hash,
                    source="cache",
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                )
                self._record(node, outcome, total)
                return True

        outcome = self._run_callables(node, inputs_hash)
        self._record(node, outcome, total)

        if node.gate and outcome.ok and node.id not in self.state.approvals:
            return self._pause_at_gate(node, outcome)
        return True

    def _run_callables(self, node: TaskNode, inputs_hash: str) -> NodeOutcome:
        """Primary run + declared fallbacks (F-0423); failures degrade (F-0424)."""
        ctx = NodeContext(
            graph_id=self.graph_id,
            node_id=node.id,
            params=dict(node.params),
            upstream={dep: self.state.outcomes[dep] for dep in node.depends_on if dep in self.state.outcomes},
            context=self.state.context,
            memory=self._memory.view(self.graph_id),
        )
        started = _now_iso()
        self.state.run_counts[node.id] = self.state.run_counts.get(node.id, 0) + 1
        self._emit(node_id=node.id, state="running")
        callables: list[tuple[str, Callable[[NodeContext], Any]]] = [("run", node.run)]
        callables += [(f"fallback:{i}", fb) for i, fb in enumerate(node.fallbacks)]
        last_error: str | None = None
        for source, fn in callables:
            try:
                value = run_maybe_async(fn(ctx))
            except KeyboardInterrupt:
                raise  # kill semantics — handled (persist + re-raise) in run()
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._audit_event(
                    "node_source_failed",
                    node_id=node.id,
                    detail={"source": source, "error": last_error},
                )
                continue
            if source != "run":
                # Which alternate source produced the result is recorded —
                # never a silent pick (NFR-REL-007 / F-0423 audit).
                self._audit_event(
                    "fallback_used",
                    node_id=node.id,
                    detail={"source": source, "primary_error": last_error},
                )
            if isinstance(value, DegradedResult):
                return NodeOutcome(
                    node_id=node.id,
                    status="degraded",
                    value=value.value,
                    reason=value.reason,
                    inputs_hash=inputs_hash,
                    source=source,
                    started_at=started,
                    finished_at=_now_iso(),
                )
            return NodeOutcome(
                node_id=node.id,
                status="ok",
                value=value,
                inputs_hash=inputs_hash,
                source=source,
                started_at=started,
                finished_at=_now_iso(),
            )
        return NodeOutcome(
            node_id=node.id,
            status="failed",
            error=last_error or "node produced no result",
            inputs_hash=inputs_hash,
            started_at=started,
            finished_at=_now_iso(),
        )

    def _pause_at_gate(self, node: TaskNode, outcome: NodeOutcome) -> bool:
        review = {
            "node_id": node.id,
            "gate_reason": node.gate_reason or node.description or node.id,
            # Size-capped payload (m6): persisted on the state so the status
            # surface (analysis_get_status) can SHOW what is under review.
            "review_payload": _capped_review_payload(outcome.value),
        }
        self.state.pending_gate = node.id
        self.state.pending_review = review
        self._set_status(AWAITING_REVIEW)
        self._audit_event("gate_paused", node_id=node.id, detail={"gate_reason": review["gate_reason"]})
        self._emit(node_id=node.id, state="awaiting_review", detail=review["gate_reason"])
        return False

    def _record(self, node: TaskNode, outcome: NodeOutcome, total: int) -> None:
        self.state.outcomes[node.id] = outcome
        self._persist()
        self._audit_event(
            f"node_{outcome.status}",
            node_id=node.id,
            detail={"source": outcome.source, "error": outcome.error, "reason": outcome.reason},
        )
        self._emit(node_id=node.id, state=outcome.status, progress=self._progress(total))

    def _inputs_hash(self, node: TaskNode) -> str:
        """Idempotency key: node id + params + upstream outcome hashes (F-0431)."""
        upstream = {
            dep: (
                self.state.outcomes[dep].inputs_hash,
                self.state.outcomes[dep].status,
            )
            for dep in node.depends_on
            if dep in self.state.outcomes
        }
        blob = json.dumps(
            {"id": node.id, "params": _jsonable(node.params), "upstream": upstream},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def _progress(self, total: int) -> float:
        return self.state.progress(total)

    def _set_status(self, status: str) -> None:
        self.state.status = status
        self._persist()
        self._emit(node_id=None, state=status)

    def _persist(self) -> None:
        # A node may discover the analysis id mid-run (parcel_analyze creates it);
        # adopt it from the shared context so status events key under it too.
        if self.state.analysis_id is None:
            aid = self.state.context.get("analysis_id")
            if aid:
                self.state.analysis_id = str(aid)
        self.state.updated_at = _now_iso()
        self._store.put(self.state)

    def _emit(
        self,
        *,
        node_id: str | None,
        state: str,
        progress: float | None = None,
        detail: str | None = None,
    ) -> None:
        event = {
            "graph_id": self.graph_id,
            "analysis_id": self.state.analysis_id,
            "node_id": node_id,
            "state": state,
            "graph_status": self.state.status,
            "progress": progress if progress is not None else self.state.progress(max(len(self._nodes), 1)),
            "detail": detail,
            "at": _now_iso(),
        }
        self._status.append(self.graph_id, event)
        if self.state.analysis_id and self.state.analysis_id != self.graph_id:
            self._status.append(self.state.analysis_id, event)
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # status streaming must never break the analysis
                _log.warning("status_callback_failed", graph_id=self.graph_id)

    def _audit_event(
        self, event: str, *, node_id: str | None = None, detail: dict[str, Any] | None = None
    ) -> None:
        """Audit every orchestrator decision (F-0446) — structured log + store."""
        self._audit.append(
            {
                "graph_id": self.graph_id,
                "analysis_id": self.state.analysis_id,
                "event": event,
                "node_id": node_id,
                "detail": _jsonable(detail or {}),
                "at": _now_iso(),
            }
        )


def resume_graph(
    graph_id: str,
    *,
    approved: bool,
    reason: str = "",
    reviewer: str = "",
    expected_gate: str | None = None,
    graphs: Mapping[str, TaskGraph] | None = None,
) -> GraphState | None:
    """Resume a paused graph by id (F-0430): approval continues, rejection aborts.

    ``expected_gate`` binds the decision to one specific gate (review B1) —
    a mismatch raises :class:`GateMismatchError` (audited by ``resume``).
    Looks the live graph up in the process-local registry (graphs keep their
    node callables in memory; the persisted state alone cannot re-create the
    callables — documented limitation of the in-memory MVP store).
    Returns ``None`` when no live graph with that id exists.
    """
    from plot_agent.orchestrator.store import DEFAULT_GRAPH_REGISTRY

    registry = graphs if graphs is not None else DEFAULT_GRAPH_REGISTRY
    graph = registry.get(graph_id)
    if graph is None:
        return None
    return graph.resume(
        approved=approved, reason=reason, reviewer=reviewer, expected_gate=expected_gate
    )


def add_chunked_nodes(
    graph: TaskGraph,
    *,
    base_id: str,
    items: Iterable[Any],
    make_run: Callable[[Any], Callable[[NodeContext], Any]],
    merge: Callable[[NodeContext], Any],
    depends_on: tuple[str, ...] = (),
    params: Mapping[str, Any] | None = None,
) -> str:
    """Chunking helper (F-0436): one subgraph node per item + a merge node.

    Used when an InvestmentArea exceeds the configured per-run limit: the area
    is split into per-parcel subgraphs whose outcomes feed ``{base_id}:merge``.
    ``params`` are extra inputs every chunk node depends on (review M4: inputs
    that change the chunk result — e.g. the investment goal — must be part of
    the idempotency hash, or a re-run with a changed input reuses stale
    outcomes). Returns the merge node id.
    """
    shared_params = dict(params or {})
    chunk_ids: list[str] = []
    for i, item in enumerate(items):
        node_id = f"{base_id}:chunk:{i}"
        graph.add(
            TaskNode(
                id=node_id,
                run=make_run(item),
                depends_on=depends_on,
                params={**shared_params, "chunk_index": i, "chunk_item": _jsonable(item)},
                description=f"chunked subgraph {i} of {base_id} (F-0436)",
            )
        )
        chunk_ids.append(node_id)
    merge_id = f"{base_id}:merge"
    graph.add(
        TaskNode(
            id=merge_id,
            run=merge,
            depends_on=tuple(chunk_ids),
            description=f"merge of {len(chunk_ids)} chunked subgraphs (F-0436)",
        )
    )
    return merge_id
