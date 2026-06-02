"""Egress allowlist + SSRF guard (Phase 6 §6.1.A.3; F-0488/0489; §16).

A connector ``fetch`` must refuse any host that is not on the allowlist (raising the
typed :class:`~plot_connectors.base.errors.EgressBlocked`), and the guard rejects
URLs that resolve to private / loopback / link-local addresses and off-allowlist
redirect targets.

The allowlist is seeded from :class:`plot_shared.Settings.egress_allowlist` (the §6
government domains). Host matching is suffix-based on dotted labels so
``uldk.gugik.gov.pl`` matches the ``gov.pl`` / ``gugik.gov.pl`` suffixes but
``evilgov.pl`` does NOT (no substring matching).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from plot_shared import Settings, get_settings

from plot_connectors.base.errors import EgressBlocked


def _host_matches_suffix(host: str, suffix: str) -> bool:
    """True if ``host`` equals ``suffix`` or is a dotted sub-domain of it.

    Label-aware so ``gov.pl`` matches ``a.gov.pl`` but never ``notgov.pl``.
    """
    host = host.lower().rstrip(".")
    suffix = suffix.lower().rstrip(".")
    return host == suffix or host.endswith("." + suffix)


def is_private_host(host: str) -> bool:
    """True if ``host`` is, or resolves to, a private / loopback / link-local /
    reserved address (SSRF guard, F-0489).

    A literal IP is checked directly; a hostname is resolved via DNS and *every*
    returned address must be public — if any is private, the host is rejected.
    Resolution failures are treated as private (fail-closed).
    """
    # Literal IP?
    try:
        ip = ipaddress.ip_address(host)
        return _ip_is_private(ip)
    except ValueError:
        pass

    # Hostname → resolve all addresses; fail-closed on error.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if _ip_is_private(ip):
            return True
    return False


def _ip_is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any non-publicly-routable address class."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class EgressAllowlist:
    """Host allowlist + SSRF guard for connector egress (F-0488/0489, §16).

    Construct from :class:`plot_shared.Settings` (default: the global settings) so the
    allowed §6 government domains come from one auditable place. ``resolve_dns`` can be
    disabled in unit tests that should not perform DNS lookups — the host-suffix check
    still runs (so the allowlist itself is always enforced).
    """

    def __init__(
        self,
        allowed_hosts: tuple[str, ...] | list[str],
        *,
        resolve_dns: bool = True,
    ) -> None:
        self._allowed = tuple(h.lower().rstrip(".") for h in allowed_hosts)
        self._resolve_dns = resolve_dns

    @classmethod
    def from_settings(cls, settings: Settings | None = None, *, resolve_dns: bool = True) -> EgressAllowlist:
        """Build the allowlist from ``settings.egress_allowlist`` (the §6 domains)."""
        settings = settings or get_settings()
        return cls(settings.egress_allowlist, resolve_dns=resolve_dns)

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return self._allowed

    def is_allowed_host(self, host: str) -> bool:
        """True if ``host`` matches any allowed suffix."""
        return any(_host_matches_suffix(host, suffix) for suffix in self._allowed)

    def check_url(self, url: str) -> str:
        """Validate ``url`` for egress; return the host on success or raise.

        Raises :class:`EgressBlocked` when the scheme is not http(s), the host is not
        on the allowlist, or (when ``resolve_dns``) the host resolves to a private /
        loopback address.
        """
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise EgressBlocked(f"Refusing non-http(s) egress scheme: {parts.scheme!r} ({url})")
        host = parts.hostname
        if not host:
            raise EgressBlocked(f"Refusing egress to URL with no host: {url}")
        if not self.is_allowed_host(host):
            raise EgressBlocked(
                f"Host {host!r} is not on the egress allowlist {self._allowed} (F-0488)"
            )
        if self._resolve_dns and is_private_host(host):
            raise EgressBlocked(
                f"Host {host!r} resolves to a private/loopback address (SSRF guard, F-0489)"
            )
        return host

    def check_redirect(self, location: str) -> None:
        """Validate a redirect ``Location`` target the same way as :meth:`check_url`.

        A redirect to a non-allowlisted (or private) host is an SSRF vector, so it is
        rejected even if the original request was allowed (F-0489).
        """
        self.check_url(location)


def default_allowlist(*, resolve_dns: bool = True) -> EgressAllowlist:
    """Convenience: the allowlist built from the global settings (§6 domains)."""
    return EgressAllowlist.from_settings(resolve_dns=resolve_dns)
