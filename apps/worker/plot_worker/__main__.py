"""Worker entrypoint (Phase 13 / v1 Phase 11 §11.1.3).

Two equivalent ways to run the worker against the env-configured broker:

* the Dramatiq CLI (production):    ``uv run dramatiq plot_worker.actors``
* this module (dev convenience):    ``uv run python -m plot_worker``

Both import :mod:`plot_worker.actors`, which installs the broker from settings
(``PLOT_QUEUE_ENABLED`` + ``PLOT_REDIS_URL``). Periodic scheduling of
``monitoring_check_task`` (per ``MonitorStore.due()``) is a deployment concern
(cron/systemd timer) — this process only consumes the queue.
"""

from __future__ import annotations

import logging
import time

import dramatiq
from plot_shared import configure_logging, get_logger, get_settings

from plot_worker import actors

_log = get_logger(__name__)


def main() -> None:
    configure_logging(logging.INFO)
    settings = get_settings()
    if not settings.queue_enabled:
        _log.warning(
            "queue_disabled",
            note=(
                "PLOT_QUEUE_ENABLED is false — the StubBroker is installed; "
                "set PLOT_QUEUE_ENABLED=true and PLOT_REDIS_URL to consume Redis."
            ),
        )
    worker = dramatiq.Worker(actors.broker)
    worker.start()
    _log.info("worker_started", queue=settings.queue_name)
    try:
        while True:  # consume until interrupted
            time.sleep(1.0)
    except KeyboardInterrupt:
        _log.info("worker_stopping")
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
