"""Typed connector errors (Phase 6 §6.1.A; §16 security; NFR-REL-009).

All connector errors subclass :class:`plot_shared.PlotAnalyzerError` so they carry
an :class:`~plot_shared.ErrorCategory` and can be routed/audited uniformly. Egress
violations are an ``input``/``system`` security failure; everything that means "the
source did not give us an answer" maps to ``source`` (→ ``source_unavailable``).
"""

from __future__ import annotations

from plot_shared import ErrorCategory, PlotAnalyzerError


class ConnectorError(PlotAnalyzerError):
    """Base class for connector failures (classified ``source`` by default)."""

    category = ErrorCategory.SOURCE


class EgressBlocked(ConnectorError):
    """A fetch targeted a host that is not on the egress allowlist, or resolved to
    a private/loopback address, or a redirect pointed off-allowlist (F-0488/0489, §16).

    Classified as ``system`` because it is a guard-rail violation, not a remote
    source problem — it must never be silently downgraded to ``source_unavailable``.
    """

    category = ErrorCategory.SYSTEM


class SourceUnavailable(ConnectorError):
    """The source did not return a usable answer (timeout, 5xx, circuit open).

    Carriers of this error map to ``ResultStatus.SOURCE_UNAVAILABLE`` — never to
    ``not_detected`` (NFR-REL-001, §21).
    """

    category = ErrorCategory.SOURCE


class LicenseError(ConnectorError):
    """A source profile has an incompatible / unknown license that forbids use
    under the current options (F-0092 license validation)."""

    category = ErrorCategory.SOURCE
