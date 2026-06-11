"""Process-local orchestrator stores (Phase 13 / v1 Phase 11 §11.1).

Same lifecycle/idiom as :data:`plot_agent.analysis.DEFAULT_STORE` and
:class:`plot_agent.drawing.variants.MasterplanAuditLog` — in-memory,
thread-safe, JSON-serializable snapshots; durable persistence (PostGIS) is a
later-phase concern:

* :class:`GraphStore` — persisted :class:`~plot_agent.orchestrator.graph
  .GraphState` per graph id (the resumability substrate, NFR-REL-002/003);
* :class:`GraphRegistry` — LIVE :class:`TaskGraph` objects per id (state alone
  cannot re-create node callables; ``resume_graph`` needs the live object);
* :class:`OrchestratorAudit` — every orchestrator decision, chronological
  (F-0446), mirrored to the structured logger;
* :class:`AgentMemory` — per-analysis notes/decisions later nodes can recall
  (F-0438);
* :class:`StatusStore` — ordered per-graph/per-analysis progress events
  (F-0434; consumed by ``analysis_get_status`` + ``ctx.report_progress``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from plot_shared import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from plot_agent.orchestrator.graph import GraphState, TaskGraph

_log = get_logger(__name__)


@dataclass
class GraphStore:
    """Graph-state store keyed by graph id (in-memory; JSON-serializable snapshots)."""

    _states: dict[str, GraphState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, state: GraphState) -> None:
        with self._lock:
            self._states[state.graph_id] = state

    def get(self, graph_id: str) -> GraphState | None:
        with self._lock:
            return self._states.get(graph_id)

    def has(self, graph_id: str) -> bool:
        with self._lock:
            return graph_id in self._states

    def snapshot(self, graph_id: str) -> dict[str, Any] | None:
        """JSON-serializable snapshot of one graph state (persistence contract)."""
        state = self.get(graph_id)
        return state.to_dict() if state is not None else None

    def all_states(self) -> list[GraphState]:
        with self._lock:
            return list(self._states.values())

    def recent_failures(self, limit: int = 10) -> list[dict[str, Any]]:
        """Last task-graph node failures across graphs (diagnostics, F-0442)."""
        failures: list[dict[str, Any]] = []
        for state in self.all_states():
            for outcome in state.outcomes.values():
                if outcome.status == "failed":
                    failures.append(
                        {
                            "graph_id": state.graph_id,
                            "analysis_id": state.analysis_id,
                            "node_id": outcome.node_id,
                            "error": outcome.error,
                            "finished_at": outcome.finished_at,
                        }
                    )
        failures.sort(key=lambda f: str(f.get("finished_at")), reverse=True)
        return failures[:limit]

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


@dataclass
class GraphRegistry:
    """Live :class:`TaskGraph` objects per id (resume needs the node callables)."""

    _graphs: dict[str, TaskGraph] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, graph: TaskGraph) -> None:
        with self._lock:
            self._graphs[graph.graph_id] = graph

    def get(self, graph_id: str) -> TaskGraph | None:
        with self._lock:
            return self._graphs.get(graph_id)

    def clear(self) -> None:
        with self._lock:
            self._graphs.clear()


class OrchestratorAudit:
    """Chronological audit of every orchestrator decision (F-0446).

    Same shape as :class:`plot_agent.drawing.variants.MasterplanAuditLog`;
    every entry is ALSO emitted through the structured logger so the trail
    exists even when the in-memory store is reset.
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._entries.append(entry)
        # "event" is structlog's positional message key — remap to audit_event.
        _log.info(
            "orchestrator_audit",
            audit_event=entry.get("event"),
            graph_id=entry.get("graph_id"),
            analysis_id=entry.get("analysis_id"),
            node_id=entry.get("node_id"),
            at=entry.get("at"),
        )

    def entries(self, graph_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if graph_id is None:
                return list(self._entries)
            return [e for e in self._entries if e.get("graph_id") == graph_id]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


@dataclass
class _MemoryView:
    """Per-graph view of :class:`AgentMemory` handed to node callables (F-0438)."""

    _memory: AgentMemory
    _graph_id: str

    def remember(self, key: str, value: Any) -> None:
        self._memory.remember(self._graph_id, key, value)

    def recall(self, key: str, default: Any = None) -> Any:
        return self._memory.recall(self._graph_id, key, default)

    def notes(self) -> dict[str, Any]:
        return self._memory.notes(self._graph_id)


@dataclass
class AgentMemory:
    """Keyed per-analysis notes/decisions store (agent memory, F-0438)."""

    _notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def remember(self, graph_id: str, key: str, value: Any) -> None:
        with self._lock:
            self._notes.setdefault(graph_id, {})[key] = value

    def recall(self, graph_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._notes.get(graph_id, {}).get(key, default)

    def notes(self, graph_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._notes.get(graph_id, {}))

    def view(self, graph_id: str) -> _MemoryView:
        return _MemoryView(self, graph_id)

    def clear(self) -> None:
        with self._lock:
            self._notes.clear()


class StatusStore:
    """Ordered per-key progress events (F-0434 streaming status).

    Keys are graph ids AND analysis ids (the graph emits under both), so
    ``analysis_get_status`` can stream node-level progress for a long analysis
    (NFR-PERF-010) and the MCP tool can feed ``ctx.report_progress``.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._seq: dict[str, int] = {}
        self._lock = threading.Lock()

    def append(self, key: str, event: dict[str, Any]) -> None:
        with self._lock:
            seq = self._seq.get(key, 0) + 1
            self._seq[key] = seq
            self._events.setdefault(key, []).append({"seq": seq, **event})

    def events(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events.get(key, []))

    def latest(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            events = self._events.get(key)
            return dict(events[-1]) if events else None

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._events

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._seq.clear()


#: Module-level defaults shared within a process (reset on reload — dev/MVP OK).
DEFAULT_GRAPH_STORE = GraphStore()
DEFAULT_GRAPH_REGISTRY = GraphRegistry()
DEFAULT_ORCHESTRATOR_AUDIT = OrchestratorAudit()
DEFAULT_AGENT_MEMORY = AgentMemory()
DEFAULT_STATUS_STORE = StatusStore()
