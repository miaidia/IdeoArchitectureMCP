"""Load smoke test (Phase 16; F-0554 — the concurrency gap over F-0553 budgets).

20 concurrent quick_screenings through the SHARED use-case module (mock
connectors, zero network) driven from a thread pool — the way concurrent MCP/
HTTP sessions hit the in-process engine:

* every run completes (no exception, a stored result, a decision);
* results are isolated (20 distinct analysis ids; each retrievable);
* the batch lands under a wall-clock budget derived from the measured
  per-request budget (``perf_quick_budget_s``) — concurrency must not serialize
  catastrophically NOR blow up shared state.

Marked ``perf`` like the Phase 14B budget tests (fast; runs by default).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from plot_domain import AnalysisInput
from tests.mocks import mock_connectors

pytestmark = pytest.mark.perf

N_CONCURRENT = 20


@pytest.fixture
def quick_input() -> AnalysisInput:
    return AnalysisInput.model_validate(
        {
            "input": {"parcel_id": "141201_1.0001.1867/2"},
            "analysis_mode": "quick_screening",
        }
    )


def test_twenty_concurrent_quick_screenings_under_budget(
    monkeypatch: pytest.MonkeyPatch, quick_input: AnalysisInput
) -> None:
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    settings = cfg.get_settings()

    from plot_mcp_server import usecases

    usecases.set_connectors(mock_connectors())
    try:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=N_CONCURRENT) as pool:
            results = list(
                pool.map(
                    lambda _: usecases.parcel_analyze(quick_input, "load-smoke"),
                    range(N_CONCURRENT),
                )
            )
        elapsed = time.perf_counter() - started

        # Every run completed with a stored, retrievable, decided result.
        ids = {r.analysis_id for r in results}
        assert len(ids) == N_CONCURRENT, "analysis ids collided under concurrency"
        for result in results:
            assert result.decision is not None
            stored = usecases.DEFAULT_STORE.get(result.analysis_id)
            assert stored is not None and stored.analysis_id == result.analysis_id

        # Budget: the whole 20-run batch must beat 20 sequential budget runs by
        # a wide margin — 4× the single-run budget is generous for CI variance
        # while still failing on accidental serialization/lock contention.
        budget = settings.perf_quick_budget_s * 4
        assert elapsed < budget, (
            f"{N_CONCURRENT} concurrent quick screenings took {elapsed:.2f}s "
            f"(budget {budget:.1f}s)"
        )
    finally:
        usecases.set_connectors(None)
        cfg.get_settings.cache_clear()
