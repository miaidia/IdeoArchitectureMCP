"""Process-local store of scored masterplan variants (Phase 9 §9.1.5 MCP wiring).

The ``propose_layout`` masterplan path persists each scored variant here so the
``analysis://{analysis_id}/masterplan/{variant_id}/metrics.json`` resource can return
its metrics on demand (NFR-PERF-009: big payloads are resources, not inlined twice).
Same pattern as :data:`plot_agent.analysis.DEFAULT_STORE` — in-memory until the Phase 12
persistence layer.

Phase 11 (§11.1.6 design-rationale record) adds :class:`MasterplanAuditLog`: the
chronological F-0446 audit entries of every masterplan ``propose_layout`` call
(inputs hash, scores, critique, the model's rationale, artifact uri). The koncepcja
deliverable reads the rationale section from here — rationale flows audit → report
ONLY, never into validation (NFR-SEC-003).
"""

from __future__ import annotations

from typing import Any

from plot_domain import MasterplanVariant


class MasterplanVariantStore:
    """In-memory ``variant_id -> MasterplanVariant`` store (process-local)."""

    def __init__(self) -> None:
        self._variants: dict[str, MasterplanVariant] = {}

    def put(self, variant: MasterplanVariant) -> None:
        self._variants[variant.id] = variant

    def get(self, variant_id: str) -> MasterplanVariant | None:
        return self._variants.get(variant_id)

    def latest(self, analysis_id: str | None = None) -> MasterplanVariant | None:
        """The most recently stored variant (dict preserves insertion order).

        ``analysis_id`` scopes the lookup to ONE analysis (review fix F1): the
        store is process-global, so an unscoped "latest" could otherwise return
        another analysis's variant. ``None`` keeps the unscoped behaviour.
        """
        for variant in reversed(self._variants.values()):
            if analysis_id is None or variant.analysis_id == analysis_id:
                return variant
        return None

    def __len__(self) -> int:
        return len(self._variants)


class MasterplanAuditLog:
    """Chronological masterplan audit entries (F-0446 / Phase 11 §11.1.6).

    Process-local like the variant store. Entries are the loop's
    :class:`~plot_agent.drawing.loop.AuditEntry` dicts (stamped with their
    ``analysis_id``) extended by the MCP layer with ``variant_id`` +
    ``parent_variant_id``; the koncepcja report renders the reported variant's
    LINEAGE of rationales chronologically ("dlaczego tak") — never entries from
    unrelated sessions (review fix F1).
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def append(self, entry: dict[str, Any]) -> None:
        self._entries.append(entry)

    def entries(self, analysis_id: str | None = None) -> list[dict[str, Any]]:
        """Chronological entries, optionally scoped to one ``analysis_id``."""
        if analysis_id is None:
            return list(self._entries)
        return [e for e in self._entries if e.get("analysis_id") == analysis_id]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


#: Default process-local stores used by the MCP use-case layer + resources.
DEFAULT_VARIANT_STORE = MasterplanVariantStore()
DEFAULT_MASTERPLAN_AUDIT = MasterplanAuditLog()
