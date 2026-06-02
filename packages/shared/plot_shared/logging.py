"""Structured logging setup via structlog (base_assumptions §9.3 structured logs).

Configures structlog to emit JSON-friendly, key-value structured events. Safe to
call multiple times; ``get_logger`` returns a bound logger.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog


def configure_logging(level: int = logging.INFO, *, json_output: bool = True) -> None:
    """Configure stdlib + structlog for structured output.

    Args:
        level: stdlib logging level.
        json_output: render JSON (production) when True, else a console renderer.
    """
    logging.basicConfig(format="%(message)s", level=level)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
