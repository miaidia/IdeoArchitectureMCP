"""Perf benchmark + deadline tests (Phase 14B; F-0517/0525/0528, NFR-PERF).

Budgets are measured, not guessed: quick_screening on the golden mock parcel
must finish under ``Settings.perf_quick_budget_s`` (default 5 s — generous for
CI variance; locally it runs in well under a second) and one propose_layout
masterplan iteration under ``Settings.perf_layout_budget_s`` (default 10 s).
The analysis-level deadline (F-0517/0525) degrades themes that cannot finish to
``source_unavailable`` — tested with a deliberately slow mock source.

Marked ``perf`` and INCLUDED in the default run (they are fast).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import anyio
import pytest
from plot_agent.analysis import Connectors
from plot_agent.analysis.connectors import RiskLayerFetch
from plot_domain import AnalysisInput
from tests.mocks import MockRiskLayerSource, MockULDKConnector, mock_connectors

pytestmark = pytest.mark.perf

GOLDEN_INPUT = {"parcel_id": "141201_1.0001.1867/2"}


@pytest.fixture
def fresh_settings(monkeypatch: pytest.MonkeyPatch):
    import plot_shared.config as cfg

    def apply(**env: str):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        cfg.get_settings.cache_clear()
        return cfg.get_settings()

    yield apply
    cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# budgets (F-0528 benchmark suite; NFR-PERF quick mode under budget)
# --------------------------------------------------------------------------- #
def test_quick_screening_under_budget(fresh_settings) -> None:
    settings = fresh_settings(PLOT_DEV_HOT_RELOAD="false")
    from plot_mcp_server import usecases

    usecases.set_connectors(mock_connectors())
    try:
        payload = AnalysisInput.model_validate(
            {"input": GOLDEN_INPUT, "analysis_mode": "quick_screening"}
        )
        started = time.perf_counter()
        result = usecases.parcel_analyze(payload, "perf")
        elapsed = time.perf_counter() - started
    finally:
        usecases.set_connectors(None)
    assert result.buildable_envelope is not None
    print(f"\n[perf] quick_screening: {elapsed:.3f}s (budget {settings.perf_quick_budget_s}s)")
    assert elapsed < settings.perf_quick_budget_s


def test_propose_layout_masterplan_under_budget(fresh_settings) -> None:
    settings = fresh_settings(PLOT_DEV_HOT_RELOAD="false")
    from plot_agent.context import AnalysisContext
    from plot_mcp_server import usecases
    from tests.masterplan_fixtures import staged_masterplan_payload, staging_parcel

    usecases.set_drawing_context(
        AnalysisContext.with_loaded_rules(
            parcel=staging_parcel(), buildable_envelope=staging_parcel()
        )
    )
    try:
        started = time.perf_counter()
        out = usecases.propose_layout_render(staged_masterplan_payload(), None, None, None)
        elapsed = time.perf_counter() - started
    finally:
        usecases.set_drawing_context(None)
    assert out["schema_version"] == 2 and out["metrics"]
    print(f"\n[perf] propose_layout: {elapsed:.3f}s (budget {settings.perf_layout_budget_s}s)")
    assert elapsed < settings.perf_layout_budget_s


# --------------------------------------------------------------------------- #
# parallel fetch behaviour (F-0510): 8 concurrent themes vs sequential
# --------------------------------------------------------------------------- #
@dataclass
class SlowRiskLayerSource(MockRiskLayerSource):
    delay_s: float = 0.0
    calls: int = 0

    async def fetch(self, kind, bbox) -> RiskLayerFetch:  # type: ignore[override]
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return await super().fetch(kind, bbox)


def _run_screening(connectors: Connectors):
    from plot_agent.analysis import run_quick_screening

    async def _run():
        payload = AnalysisInput.model_validate(
            {"input": GOLDEN_INPUT, "analysis_mode": "quick_screening"}
        )
        return await run_quick_screening(payload, connectors=connectors)

    return anyio.run(_run)


def test_theme_fetches_run_concurrently(fresh_settings) -> None:
    """8 themes × 0.15 s each: concurrent gather must beat the 1.2 s sequential sum."""
    fresh_settings(PLOT_DEV_HOT_RELOAD="false", PLOT_BACKPRESSURE_DELAY_S="0")
    source = SlowRiskLayerSource(delay_s=0.15)
    connectors = Connectors(uldk=MockULDKConnector(), risk_layers=source)  # type: ignore[arg-type]
    started = time.perf_counter()
    result = _run_screening(connectors)
    elapsed = time.perf_counter() - started
    assert source.calls == 8
    assert result.status.value in ("complete", "manual_review_required")
    print(f"\n[perf] 8 themes x 0.15s concurrent: {elapsed:.3f}s")
    assert elapsed < 1.2  # sequential would be >= 1.2 s


# --------------------------------------------------------------------------- #
# analysis-level deadline (F-0517/0525): degrade to source_unavailable
# --------------------------------------------------------------------------- #
def test_deadline_degrades_remaining_themes_to_source_unavailable(fresh_settings) -> None:
    fresh_settings(PLOT_DEV_HOT_RELOAD="false", PLOT_ANALYSIS_DEADLINE_S="0.2")
    source = SlowRiskLayerSource(delay_s=0.6)  # every theme slower than the budget
    connectors = Connectors(uldk=MockULDKConnector(), risk_layers=source)  # type: ignore[arg-type]
    result = _run_screening(connectors)

    layer_status = result.planning["_risk_layer_status"]
    assert set(layer_status.values()) == {"source_unavailable"}
    assert result.status.value == "partial"  # degraded, never silently complete
    # every cut-off theme is an EXPLICIT unknown (§21)
    assert len(result.unknowns) >= 8


def test_deadline_sequential_backpressure_keeps_first_theme(fresh_settings) -> None:
    """Sequential mode: theme 1 completes (deadline not yet hit at launch),
    the remaining themes degrade — partial results, not all-or-nothing."""
    fresh_settings(
        PLOT_DEV_HOT_RELOAD="false",
        PLOT_ANALYSIS_DEADLINE_S="0.25",
        PLOT_BACKPRESSURE_DELAY_S="0.01",
    )
    source = SlowRiskLayerSource(delay_s=0.3)
    connectors = Connectors(uldk=MockULDKConnector(), risk_layers=source)  # type: ignore[arg-type]
    result = _run_screening(connectors)
    statuses = list(result.planning["_risk_layer_status"].values())
    assert statuses.count("source_unavailable") == 7
    assert source.calls == 1  # only the first theme was actually fetched
    assert result.status.value == "partial"


def test_no_deadline_by_default(fresh_settings) -> None:
    settings = fresh_settings(PLOT_DEV_HOT_RELOAD="false")
    assert settings.analysis_deadline_s == 0.0
    source = SlowRiskLayerSource(delay_s=0.0)
    connectors = Connectors(uldk=MockULDKConnector(), risk_layers=source)  # type: ignore[arg-type]
    result = _run_screening(connectors)
    assert "source_unavailable" not in result.planning["_risk_layer_status"].values()
