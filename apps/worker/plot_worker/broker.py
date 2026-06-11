"""Dramatiq broker configuration from env settings (Phase 13 / v1 Phase 11 §11.1.3).

Broker selection follows the ``plot_shared`` settings idiom (env-only config):

* ``PLOT_QUEUE_ENABLED=true`` → :class:`dramatiq.brokers.redis.RedisBroker`
  over ``PLOT_REDIS_URL`` (the docker-compose Redis, NFR-PERF-014 queue);
* otherwise → :class:`dramatiq.brokers.stub.StubBroker` — the documented
  Dramatiq unit-testing broker (verified against the installed package source:
  ``dramatiq/brokers/stub.py`` — ``StubBroker.enqueue``/``join``/``flush_all``;
  tests drive it with ``dramatiq.Worker(broker, worker_timeout=100)``), so
  importing the actors NEVER requires a live Redis.

The MCP use-cases check :func:`queue_enabled` for graceful degradation: no
broker configured → the shared use-case runs synchronously in-process.
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.stub import StubBroker
from plot_shared import Settings, get_logger, get_settings

_log = get_logger(__name__)


def queue_enabled(settings: Settings | None = None) -> bool:
    """True when a real (Redis) broker is configured via the environment."""
    return bool((settings or get_settings()).queue_enabled)


def build_broker(settings: Settings | None = None) -> dramatiq.Broker:
    """Build the broker the env asks for (RedisBroker) or the StubBroker fallback."""
    settings = settings or get_settings()
    if settings.queue_enabled:
        # Imported lazily so the redis client is only required when enabled.
        from dramatiq.brokers.redis import RedisBroker

        _log.info("broker_configured", kind="redis", url=settings.redis_url)
        return RedisBroker(url=settings.redis_url)
    _log.info("broker_configured", kind="stub")
    return StubBroker()


def setup_broker(settings: Settings | None = None) -> dramatiq.Broker:
    """Build + install the global Dramatiq broker (``dramatiq.set_broker``)."""
    broker = build_broker(settings)
    dramatiq.set_broker(broker)
    return broker
