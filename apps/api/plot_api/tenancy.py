"""Tenant tagging for API-created resources (Phase 14B; F-0480).

Analyses/batches created THROUGH THE API are tagged with the creating key's
tenant id; reads of a resource tagged with a DIFFERENT tenant return 404 (not
403 — existence must not leak across tenants). Resources with NO tag (created
over local MCP stdio, which is a single-user trust domain with no tenant
concept) are readable by any authenticated key — documented behaviour, not an
oversight. Durable, per-tenant storage isolation is a deployment concern
(PostGIS row-level security / separate schemas) — see README.
"""

from __future__ import annotations

import threading

from fastapi import HTTPException


class TenantIndex:
    """Process-local ``resource_id → tenant_id`` map."""

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = threading.Lock()

    def tag(self, resource_id: str, tenant_id: str) -> None:
        with self._lock:
            self._owners[resource_id] = tenant_id

    def owner(self, resource_id: str) -> str | None:
        with self._lock:
            return self._owners.get(resource_id)

    def check_access(self, resource_id: str, tenant_id: str) -> None:
        """404 when the resource belongs to ANOTHER tenant (existence must not leak)."""
        owner = self.owner(resource_id)
        if owner is not None and owner != tenant_id:
            raise HTTPException(status_code=404, detail="Nie znaleziono analizy.")

    def clear(self) -> None:
        with self._lock:
            self._owners.clear()


#: Module-level default (process-local, mirrors the domain default stores).
DEFAULT_TENANT_INDEX = TenantIndex()
