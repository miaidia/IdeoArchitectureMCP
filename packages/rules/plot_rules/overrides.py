"""Audited expert-override store (Phase 8, F-0137/0138; NFR-AUD-003).

``manual_override`` creates an audited :class:`~plot_domain.Override` record here;
the rule-evaluation layer applies it by passing the matching record into
:func:`plot_rules.engine.evaluate` (which embeds the full audit — author, reason,
before/after — into the RuleCheck trace). The store itself never mutates rule
outcomes; it only keeps the auditable records (the audit trail is the API).

Process-local + thread-safe like the analysis store; persistence (§26.1
``overrides`` table) lands in Phase 12.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from plot_domain.models import Override


@dataclass
class OverrideStore:
    """Process-local store of audited expert overrides keyed by analysis."""

    _overrides: list[Override] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, override: Override) -> None:
        with self._lock:
            self._overrides.append(override)

    def for_target(self, analysis_id: str, target_id: str) -> Override | None:
        """Latest override for ``target_id`` within ``analysis_id`` (F-0137)."""
        with self._lock:
            for override in reversed(self._overrides):
                if override.analysis_id == analysis_id and override.target_id == target_id:
                    return override
        return None

    def audit(self, analysis_id: str | None = None) -> list[Override]:
        """The audit trail (all overrides, or one analysis's), oldest first (F-0138)."""
        with self._lock:
            if analysis_id is None:
                return list(self._overrides)
            return [o for o in self._overrides if o.analysis_id == analysis_id]

    def clear(self) -> None:
        with self._lock:
            self._overrides.clear()


#: Module-level default store shared by the MCP tools within a process.
DEFAULT_OVERRIDE_STORE = OverrideStore()
