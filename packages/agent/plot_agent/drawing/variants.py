"""Process-local store of scored masterplan variants (Phase 9 §9.1.5 MCP wiring).

The ``propose_layout`` masterplan path persists each scored variant here so the
``analysis://{analysis_id}/masterplan/{variant_id}/metrics.json`` resource can return
its metrics on demand (NFR-PERF-009: big payloads are resources, not inlined twice).
Same pattern as :data:`plot_agent.analysis.DEFAULT_STORE` — in-memory until the Phase 12
persistence layer.
"""

from __future__ import annotations

from plot_domain import MasterplanVariant


class MasterplanVariantStore:
    """In-memory ``variant_id -> MasterplanVariant`` store (process-local)."""

    def __init__(self) -> None:
        self._variants: dict[str, MasterplanVariant] = {}

    def put(self, variant: MasterplanVariant) -> None:
        self._variants[variant.id] = variant

    def get(self, variant_id: str) -> MasterplanVariant | None:
        return self._variants.get(variant_id)

    def __len__(self) -> int:
        return len(self._variants)


#: Default process-local store used by the MCP use-case layer + resources.
DEFAULT_VARIANT_STORE = MasterplanVariantStore()
