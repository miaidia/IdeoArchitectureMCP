"""Application settings — env-only, no secrets in code (F-0477 / F-0478, NFR-SEC-006).

Uses pydantic-settings ``BaseSettings`` so every value can be supplied via the
environment (or a local ``.env`` that is git-ignored). Defaults are safe, local,
non-secret development values; production injects real secrets via the env only.
"""

from __future__ import annotations

from functools import lru_cache

# pydantic-settings v2: BaseSettings + SettingsConfigDict (pydantic v2, Phase 0.4).
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment.

    Secrets (DB password, S3 keys) arrive only via env vars / ``.env`` — never
    committed. See ``.env.example`` for the contract.
    """

    model_config = SettingsConfigDict(
        env_prefix="PLOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database (PostGIS) ---
    database_url: str = Field(
        default="postgresql+psycopg://plot:plot@localhost:5432/plot",
        description="SQLAlchemy DB URL for the PostGIS database.",
    )

    # --- Cache / queue (Redis) ---
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for cache and the worker queue.",
    )

    # --- Object storage (S3 / MinIO) ---
    s3_endpoint_url: str = Field(
        default="http://localhost:9000",
        description="S3-compatible endpoint URL (MinIO in dev).",
    )
    s3_access_key: str = Field(
        default="minioadmin",
        description="S3 access key. Override via env in production; never commit a real key.",
    )
    s3_secret_key: str = Field(
        default="minioadmin",
        description="S3 secret key. Override via env in production; never commit a real secret.",
    )
    s3_bucket: str = Field(
        default="plot-artifacts",
        description="Bucket for snapshots, rasters, and report artifacts (no raster blobs in DB).",
    )

    # --- Connector egress allowlist (F-0488/0489, §16 "Allowlista domen publicznych") ---
    # Host suffixes the connectors are allowed to reach. Seeded from the §6 start
    # sources (Polish government / official-register domains). A fetch to any other
    # host is refused (EgressBlocked). Override via PLOT_EGRESS_ALLOWLIST (comma- or
    # JSON-list) to extend for new connectors; never widen to a wildcard.
    egress_allowlist: tuple[str, ...] = Field(
        default=(
            "uldk.gugik.gov.pl",  # ULDK parcel resolver (Phase 0.3, F-0041)
            "geoportal.gov.pl",  # Geoportal WMS/WMTS/WFS/WCS, ortofoto, NMT/NMPT, BDOT10k, GESUT (§6.1)
            "gugik.gov.pl",  # GUGiK services (PRG, EGiB) (§6.1)
            "gov.pl",  # planning-data viewer / Rejestr Urbanistyczny / zagospodarowanieprzestrzenne (§6.2)
            "isok.gov.pl",  # Hydroportal / ISOK MZP/MRP/WORP flood (§6.3, F-0064)
            "gdos.gov.pl",  # Geoserwis GDOŚ / CRFOP protected areas (§6.3, F-0066)
            "pgi.gov.pl",  # PIG-PIB SOPO landslides / CBDG / MIDAS (§6.3, F-0068)
            "zabytek.gov.pl",  # NID heritage map portal (§6.3, F-0071)
            "stat.gov.pl",  # GUS / TERYT (§6.1, F-0044)
        ),
        description="Allowed connector egress host suffixes (F-0488/0489, §16). Never a wildcard.",
    )

    # --- Workers / queue (Phase 13: Dramatiq + Redis; v1 Phase 11 §11.1.3) ---
    queue_enabled: bool = Field(
        default=False,
        description=(
            "Enable the Dramatiq Redis broker for async tasks (portfolio/monitoring/"
            "cache-warm/full analysis). Off → use-cases run in-process (graceful "
            "degradation); workers/tests use the StubBroker."
        ),
    )
    queue_name: str = Field(
        default="plot-analyzer",
        description="Dramatiq queue name for the worker actors.",
    )
    backpressure_delay_s: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Delay between sequential outbound fetches in batch/warm paths "
            "(NFR-PERF-014 backpressure). 0 in dev/tests; deployments set "
            "PLOT_BACKPRESSURE_DELAY_S to throttle against public services."
        ),
    )
    source_max_age_days: float = Field(
        default=90.0,
        gt=0.0,
        description=(
            "Default max age for SourceRecord freshness (F-0439); staler sources "
            "flip rule evaluation into conservative mode (F-0443/0445)."
        ),
    )
    monitoring_default_interval_hours: float = Field(
        default=24.0,
        gt=0.0,
        description="Default monitoring_create check interval (§4.5).",
    )

    # --- HTTP API auth (Phase 14B; F-0477/0479/0480 — env-only, no secrets in repo) ---
    # Comma-separated "key:role:tenant" triples, e.g.
    #   PLOT_API_KEYS="s3cretA:admin:tenant-a,s3cretB:read:tenant-b"
    # role ∈ read | analyst | admin. EMPTY (the safe default) means the API
    # accepts NO authenticated requests (fail closed) — production injects real
    # keys via the environment only (F-0477/0478). The key VALUE is never
    # logged; audit entries carry a sha256-derived key id.
    api_keys: str = Field(
        default="",
        description=(
            "API keys as 'key:role:tenant' comma-separated triples (F-0479/0480). "
            "Empty = no access (fail closed). Env-only — never commit real keys."
        ),
    )

    # --- Upload sandbox (Phase 14B; F-0491/0492/0493, §16) ---
    upload_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        gt=0,
        description="Max accepted upload size in bytes (F-0493; default 20 MB).",
    )
    upload_max_chars: int = Field(
        default=1_000_000,
        gt=0,
        description="Max extracted text characters retained from an upload (NFR-SEC-009).",
    )
    upload_max_pages: int = Field(
        default=500,
        gt=0,
        description=(
            "Max estimated PDF page count for uploads (heuristic /Type /Page scan — "
            "documented estimate, not a full PDF parse)."
        ),
    )

    # --- Performance (Phase 14B; F-0510/0517/0525) ---
    parallel_fetch_limit: int = Field(
        default=8,
        gt=0,
        description=(
            "Max concurrent risk-layer fetches per analysis (F-0510). Only applies "
            "when PLOT_BACKPRESSURE_DELAY_S is 0 — a configured backpressure delay "
            "keeps fetches sequential (NFR-PERF-014)."
        ),
    )
    analysis_deadline_s: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Total analysis-level deadline in seconds for source fetches (F-0517/0525). "
            "0 disables. When exceeded, remaining themes degrade to source_unavailable "
            "(explicit unknowns, partial result) — never silently dropped."
        ),
    )
    perf_quick_budget_s: float = Field(
        default=5.0,
        gt=0.0,
        description="NFR-PERF budget for quick_screening on the golden parcel (benchmark tests).",
    )
    perf_layout_budget_s: float = Field(
        default=10.0,
        gt=0.0,
        description="NFR-PERF budget for one propose_layout masterplan iteration (benchmark tests).",
    )

    # --- Development / analysis defaults ---
    dev_hot_reload: bool = Field(
        default=False,
        description="Gate dev-only file-watching and the dev_reload tool (Phase 2). Off in prod.",
    )
    analytical_crs: str = Field(
        default="EPSG:2180",
        description="Analytical geometry CRS for Poland (PUWG1992). Input CRS kept as metadata.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance (constructed from the environment)."""
    return Settings()
