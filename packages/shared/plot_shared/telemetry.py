"""Telemetry: OpenTelemetry tracer + Prometheus registry (base_assumptions §9.3).

Thin and no-op safe: importing and calling these without a configured collector
must not raise. The OTel API returns a no-op tracer until an SDK provider is set;
``init_telemetry`` installs a basic ``TracerProvider`` so spans are created but
exporting stays optional (no exporter wired in Phase 1).
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry

# A dedicated Prometheus registry so metrics are explicit and testable rather
# than relying on the global default registry.
_METRICS_REGISTRY = CollectorRegistry()

_SERVICE_NAME = "plot-analyzer"
_initialized = False


def init_telemetry(service_name: str = _SERVICE_NAME) -> None:
    """Install a basic OTel ``TracerProvider`` (idempotent, exporter-less).

    No-op safe: if a provider is already set, this does nothing.
    """
    global _initialized
    if _initialized:
        return
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    # set_tracer_provider only takes effect on first call; guarded by _initialized.
    trace.set_tracer_provider(provider)
    _initialized = True


def get_tracer(name: str = _SERVICE_NAME) -> trace.Tracer:
    """Return an OTel tracer (no-op until ``init_telemetry`` is called)."""
    return trace.get_tracer(name)


def get_metrics_registry() -> CollectorRegistry:
    """Return the process-local Prometheus ``CollectorRegistry``."""
    return _METRICS_REGISTRY
