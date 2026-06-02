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
