"""Smoke tests for plot_shared: config (env-only), errors, telemetry, logging."""

from __future__ import annotations

from plot_shared import (
    ErrorCategory,
    PlotAnalyzerError,
    Settings,
    configure_logging,
    get_logger,
    get_metrics_registry,
    get_tracer,
    init_telemetry,
)


def test_error_category_taxonomy() -> None:
    # NFR-REL-009: input | source | ruleset | geometry | parser | system.
    assert {c.value for c in ErrorCategory} == {
        "input",
        "source",
        "ruleset",
        "geometry",
        "parser",
        "system",
    }


def test_plot_analyzer_error_carries_category() -> None:
    err = PlotAnalyzerError("bad input", category=ErrorCategory.INPUT)
    assert err.category is ErrorCategory.INPUT
    assert "input" in str(err)


def test_settings_defaults_are_env_overridable(monkeypatch) -> None:
    settings = Settings()
    # Analytical CRS default is EPSG:2180 (PUWG1992).
    assert settings.analytical_crs == "EPSG:2180"
    assert settings.dev_hot_reload is False

    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "true")
    monkeypatch.setenv("PLOT_ANALYTICAL_CRS", "EPSG:3857")
    overridden = Settings()
    assert overridden.dev_hot_reload is True
    assert overridden.analytical_crs == "EPSG:3857"


def test_telemetry_and_logging_are_no_op_safe() -> None:
    init_telemetry()
    assert get_tracer() is not None
    assert get_metrics_registry() is not None
    configure_logging(json_output=True)
    get_logger("test").info("event", key="value")
