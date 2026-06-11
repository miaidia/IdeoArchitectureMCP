"""API write-audit log (Phase 14B; F-0481/0494, NFR-SEC-010).

Same shape as :class:`plot_agent.orchestrator.store.OrchestratorAudit`: an
in-memory chronological list, with every entry ALSO emitted through the
structured logger so the trail survives a store reset. Entries carry the
sha256-derived ``key_id`` — NEVER the key value (NFR-SEC-006).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from plot_shared import get_logger

_log = get_logger("plot_api.audit")


class ApiAuditLog:
    """Chronological audit of every API write (F-0481)."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(
        self,
        *,
        action: str,
        key_id: str,
        tenant_id: str,
        target_id: str | None = None,
        purpose: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "action": action,
            "key_id": key_id,
            "tenant_id": tenant_id,
            "target_id": target_id,
            "purpose": purpose,
            "detail": detail or {},
            "at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._entries.append(entry)
        _log.info(
            "api_audit",
            action=action,
            key_id=key_id,
            tenant_id=tenant_id,
            target_id=target_id,
            purpose=purpose,
        )
        return entry

    def entries(self, action: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if action is None:
                return list(self._entries)
            return [e for e in self._entries if e.get("action") == action]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


#: Module-level default (process-local; one API app per process).
DEFAULT_API_AUDIT = ApiAuditLog()
