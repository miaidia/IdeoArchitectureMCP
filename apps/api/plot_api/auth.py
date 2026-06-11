"""API-key auth + minimal honest RBAC (Phase 14B; F-0479/0480, §16).

Keys come ONLY from the environment (``PLOT_API_KEYS`` — F-0477: no secrets in
the repo) as comma-separated ``key:role:tenant`` triples — key values must not
contain ``:`` or ``,`` (entries with extra ``:`` are rejected loudly at parse
time). Roles form a strict ladder ``read < analyst < admin``:

* **read** — GET endpoints + parcel resolve (read-only semantics);
* **analyst** — read + analysis-creating writes (analyses, portfolio,
  monitoring, document ingest, cache warm);
* **admin** — analyst + expert overrides (the destructive write).

The raw key value never leaves this module: audit entries and logs carry the
sha256-derived ``key_id`` only (NFR-SEC-006). An EMPTY configuration is fail
closed — every authenticated endpoint returns 401 with an explanatory note.

Honest scope: this is API-surface RBAC + tenant tagging, suitable for a small
deployment behind TLS. Enterprise concerns — IdP/OIDC, key rotation tooling,
per-tenant rate limits (F-0487), encryption at rest — are deployment scope
(see README), deliberately not faked here.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request
from plot_shared import API_KEY_HEADER, Settings, get_logger

_log = get_logger("plot_api.auth")

ROLE_ORDER: dict[str, int] = {"read": 0, "analyst": 1, "admin": 2}


@dataclass(frozen=True)
class ApiKeyRecord:
    """One configured API key (the secret itself stays out of logs/audit)."""

    key: str
    key_id: str  # sha256(key)[:12] — safe for logs/audit (F-0494)
    role: str
    tenant_id: str


def key_id_for(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def parse_api_keys(raw: str) -> dict[str, ApiKeyRecord]:
    """Parse the ``PLOT_API_KEYS`` env value; malformed entries are SKIPPED loudly.

    Key values MUST NOT contain ``:`` (the field separator) — such entries are
    rejected with an ERROR log (and skipped), never silently truncated.
    """
    records: dict[str, ApiKeyRecord] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) > 3:
            _log.error(
                "api_key_entry_rejected",
                reason="key must not contain ':' (format is key:role:tenant)",
            )
            continue
        if len(parts) != 3 or not all(p.strip() for p in parts):
            _log.warning("api_key_entry_malformed", reason="expected key:role:tenant")
            continue
        key, role, tenant = (p.strip() for p in parts)
        if role not in ROLE_ORDER:
            _log.warning("api_key_entry_malformed", reason=f"unknown role {role!r}")
            continue
        records[key] = ApiKeyRecord(
            key=key, key_id=key_id_for(key), role=role, tenant_id=tenant
        )
    return records


class ApiKeyAuth:
    """Request authenticator constructed once per app from settings."""

    def __init__(self, settings: Settings) -> None:
        self._records = parse_api_keys(settings.api_keys)

    @property
    def configured(self) -> bool:
        return bool(self._records)

    def authenticate(self, request: Request) -> ApiKeyRecord:
        """Resolve the calling key or raise 401 (no/unknown key — fail closed)."""
        presented = request.headers.get(API_KEY_HEADER)
        if not presented:
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Brak nagłówka {API_KEY_HEADER}. Klucze konfiguruje wyłącznie "
                    "środowisko (PLOT_API_KEYS, F-0477)."
                ),
            )
        # Constant-time lookup over the configured keys (no early-exit timing
        # leak). Compare BYTES: compare_digest(str, str) raises TypeError on
        # non-ASCII, and Starlette decodes header bytes as latin-1 — a stray
        # non-ASCII header must 401, never 500. surrogateescape keeps even
        # un-encodable surrogates on the mismatch path instead of raising.
        matched: ApiKeyRecord | None = None
        presented_bytes = presented.encode("utf-8", "surrogateescape")
        for key, record in self._records.items():
            if hmac.compare_digest(presented_bytes, key.encode("utf-8")):
                matched = record
        if matched is None:
            raise HTTPException(status_code=401, detail="Nieznany klucz API.")
        return matched

    def require(self, request: Request, minimum_role: str) -> ApiKeyRecord:
        """Authenticate + enforce the role ladder; 403 on insufficient role."""
        record = self.authenticate(request)
        if ROLE_ORDER[record.role] < ROLE_ORDER[minimum_role]:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Rola '{record.role}' nie wystarcza — endpoint wymaga "
                    f"'{minimum_role}'+ (F-0479)."
                ),
            )
        return record
