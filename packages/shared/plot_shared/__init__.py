"""plot_shared — cross-cutting infrastructure for the Plot Analyzer monorepo.

Provides configuration (env-only secrets), structured logging, telemetry init,
and the error taxonomy. Depends only on infrastructure libraries, never on the
domain or connector packages (base_assumptions §9.4 decoupling rule).
"""

from plot_shared.client import API_KEY_HEADER, PlotAnalyzerClient
from plot_shared.config import Settings, get_settings
from plot_shared.errors import ErrorCategory, PlotAnalyzerError
from plot_shared.logging import configure_logging, get_logger
from plot_shared.telemetry import get_metrics_registry, get_tracer, init_telemetry

__version__ = "0.1.0"

__all__ = [
    "API_KEY_HEADER",
    "PlotAnalyzerClient",
    "Settings",
    "get_settings",
    "ErrorCategory",
    "PlotAnalyzerError",
    "configure_logging",
    "get_logger",
    "get_metrics_registry",
    "get_tracer",
    "init_telemetry",
]
