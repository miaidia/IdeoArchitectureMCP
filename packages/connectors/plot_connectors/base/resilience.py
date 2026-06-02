"""Resilience primitives for connectors (Phase 6 §6.1.A.2; F-0078/0079).

* :func:`build_async_client` — a shared ``httpx.AsyncClient`` factory with a per-source
  timeout budget and bounded connection pool. Verified against httpx 0.28.1
  (``AsyncClient(timeout=..., limits=..., follow_redirects=..., event_hooks=...)``).
* :func:`make_retryer` — a ``tenacity.AsyncRetrying`` configured with
  stop-after-N-attempts + exponential backoff, retrying only transient httpx errors.
  Verified against tenacity 9.1.4 (``AsyncRetrying(...)(coro_fn)`` awaits the call).
* :func:`make_breaker` — a ``pybreaker.CircuitBreaker`` per source (fail_max / reset).
* :func:`guarded_call` — runs an awaitable through the breaker first, then the retryer,
  mapping a tripped breaker / exhausted retries on transient errors to
  :class:`SourceUnavailable` (→ ``source_unavailable``, NFR-REL-001), never swallowing
  :class:`EgressBlocked`.

NOTE on pybreaker async: pybreaker 1.4.1's own ``call_async`` is Tornado-only (its
closure references ``gen.coroutine`` which is unimportable without Tornado). We instead
drive the breaker's *public state machine* exactly as its synchronous ``call`` does —
``state.before_call`` (raises ``CircuitBreakerError`` when open) → await → record
success/failure — which is verified against ``CircuitBreakerState`` in pybreaker 1.4.1.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pybreaker
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from plot_connectors.base.errors import EgressBlocked, SourceUnavailable

# Transient httpx failures worth retrying / counting as source unavailability. A 4xx is
# NOT transient (it is a client/contract problem), so it is excluded by default.
TRANSIENT_HTTP_ERRORS: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


@dataclass(frozen=True)
class ResilienceConfig:
    """Per-connector timeout / retry / circuit-breaker budget (§6.1.A.1)."""

    timeout_s: float = 20.0
    connect_timeout_s: float = 5.0
    max_attempts: int = 3
    backoff_multiplier_s: float = 0.2
    backoff_max_s: float = 5.0
    breaker_fail_max: int = 5
    breaker_reset_timeout_s: float = 30.0
    max_connections: int = 10


def build_async_client(
    config: ResilienceConfig,
    *,
    base_url: str = "",
    headers: dict[str, str] | None = None,
    event_hooks: dict[str, list[Callable[..., Any]]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build a shared ``httpx.AsyncClient`` with the connector's timeout budget.

    ``follow_redirects=False`` by design: redirects are validated by the egress guard
    before being followed (F-0489), so we never auto-follow to an off-allowlist host.
    A test may inject a ``transport`` (e.g. respx) so no socket is opened.
    """
    timeout = httpx.Timeout(config.timeout_s, connect=config.connect_timeout_s)
    limits = httpx.Limits(max_connections=config.max_connections)
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        limits=limits,
        headers=headers or {},
        follow_redirects=False,
        event_hooks=event_hooks or {},
        transport=transport,
    )


def make_retryer(
    config: ResilienceConfig,
    *,
    retry_on: tuple[type[BaseException], ...] = TRANSIENT_HTTP_ERRORS,
) -> AsyncRetrying:
    """Build a ``tenacity.AsyncRetrying`` with backoff for transient errors (F-0078).

    ``reraise=True`` so the final underlying exception propagates (not a ``RetryError``
    wrapper), letting :func:`guarded_call` map it cleanly to ``SourceUnavailable``.
    """
    return AsyncRetrying(
        stop=stop_after_attempt(config.max_attempts),
        wait=wait_exponential(
            multiplier=config.backoff_multiplier_s, max=config.backoff_max_s
        ),
        retry=retry_if_exception_type(retry_on),
        reraise=True,
    )


def make_breaker(config: ResilienceConfig, *, name: str) -> pybreaker.CircuitBreaker:
    """Build a per-source circuit breaker (F-0079).

    ``EgressBlocked`` is *excluded* from the breaker's failure accounting — a guard-rail
    rejection is not a source outage and must not trip the circuit.
    """
    return pybreaker.CircuitBreaker(
        fail_max=config.breaker_fail_max,
        reset_timeout=config.breaker_reset_timeout_s,
        exclude=[EgressBlocked],
        name=name,
    )


async def _breaker_call_async[T](
    breaker: pybreaker.CircuitBreaker, coro_fn: Callable[[], Awaitable[T]]
) -> T:
    """Drive ``breaker`` around an awaitable, replicating its sync ``call`` flow.

    Verified against pybreaker 1.4.1 ``CircuitBreakerState`` source: ``before_call``
    raises ``CircuitBreakerError`` when open (and triggers half-open after the reset
    timeout), ``_handle_success`` resets the counter, ``_handle_error`` increments it
    and reraises. We do not use ``breaker.call_async`` because it is Tornado-only.
    """
    state = breaker.state
    state.before_call(coro_fn)  # raises CircuitBreakerError if the circuit is open
    for listener in breaker.listeners:
        listener.before_call(breaker, coro_fn)
    try:
        ret = await coro_fn()
    except BaseException as exc:  # noqa: BLE001 - re-raised by _handle_error
        state._handle_error(exc)  # increments counter / may trip; then reraises
        raise  # pragma: no cover - _handle_error already reraised
    else:
        state._handle_success()
        return ret


async def guarded_call[T](
    breaker: pybreaker.CircuitBreaker,
    retryer: AsyncRetrying,
    coro_fn: Callable[[], Awaitable[T]],
    *,
    source_id: str,
) -> T:
    """Run ``coro_fn`` under the circuit breaker + retryer, mapping outages cleanly.

    Order: retry *inside* the breaker so a single logical call (with its retries) counts
    as one breaker attempt. A tripped breaker or transient error exhausting retries is
    surfaced as :class:`SourceUnavailable` (→ ``source_unavailable``). ``EgressBlocked``
    is never downgraded — it propagates unchanged (F-0489).
    """

    async def _attempt() -> T:
        return await retryer(coro_fn)

    try:
        return await _breaker_call_async(breaker, _attempt)
    except EgressBlocked:
        raise  # guard-rail violation — never mask as source_unavailable
    except pybreaker.CircuitBreakerError as exc:
        raise SourceUnavailable(
            f"Circuit open for source {source_id!r}: {exc}"
        ) from exc
    except TRANSIENT_HTTP_ERRORS as exc:
        raise SourceUnavailable(
            f"Source {source_id!r} unavailable (transient error): {exc!r}"
        ) from exc
    except httpx.HTTPStatusError as exc:
        # 5xx ⇒ source unavailable; 4xx is a contract error and propagates as-is.
        if 500 <= exc.response.status_code < 600:
            raise SourceUnavailable(
                f"Source {source_id!r} returned {exc.response.status_code}"
            ) from exc
        raise
