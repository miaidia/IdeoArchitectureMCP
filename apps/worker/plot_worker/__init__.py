"""Dramatiq worker app: async analysis, batch, monitoring, cache warming (Phase 13).

* :mod:`plot_worker.broker` — env-configured broker (Redis when
  ``PLOT_QUEUE_ENABLED``, StubBroker otherwise — zero-Redis tests).
* :mod:`plot_worker.actors` — the four actors wrapping the SHARED
  ``plot_agent`` use-cases (importing it installs the broker).
* ``python -m plot_worker`` / ``dramatiq plot_worker.actors`` — entrypoints.

Actors are imported lazily by consumers (``from plot_worker import actors``)
so merely importing ``plot_worker`` never installs a broker.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
