"""PostGIS integration tests (Phase 16; F-0540) — OPT-IN, marked ``live``.

The default suite makes ZERO external connections (pyproject ``-m "not live"``),
so these run only when explicitly selected against a disposable PostGIS:

    docker run -d --rm --name pg -e POSTGRES_USER=plot -e POSTGRES_PASSWORD=plot \
      -e POSTGRES_DB=plot -p 5433:5432 postgis/postgis:16-3.4
    PLOT_TEST_DATABASE_URL=postgresql+psycopg://plot:plot@localhost:5433/plot \
      uv run pytest tests/integration -m live

Covered: alembic migrations apply cleanly to an empty database; the §26.1 ORM
round-trips an EPSG:2180 parcel geometry; PostGIS computes the same area as
shapely (the in-process geometry engine and the DB agree); the SRID is enforced.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[2]
URL_ENV = "PLOT_TEST_DATABASE_URL"


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get(URL_ENV)
    if not url:
        pytest.skip(f"{URL_ENV} not set — start a disposable PostGIS first (see docstring)")
    import sqlalchemy

    engine = sqlalchemy.create_engine(url)
    try:
        with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"PostGIS not reachable: {exc}")
    return engine


@pytest.fixture(scope="module")
def migrated(engine):
    """Apply the repo migrations to the live database (upgrade head).

    ``migrations/env.py`` reads the URL from Settings/env (PLOT_DATABASE_URL),
    so the test database URL is exported for the duration of the upgrade.
    """
    from alembic import command
    from alembic.config import Config

    url = os.environ[URL_ENV]
    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    previous = os.environ.get("PLOT_DATABASE_URL")
    os.environ["PLOT_DATABASE_URL"] = url
    try:
        import plot_shared.config as cfg

        cfg.get_settings.cache_clear()
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("PLOT_DATABASE_URL", None)
        else:
            os.environ["PLOT_DATABASE_URL"] = previous
        import plot_shared.config as cfg

        cfg.get_settings.cache_clear()
    return engine


def test_migrations_create_the_26_1_tables(migrated) -> None:
    import sqlalchemy

    inspector = sqlalchemy.inspect(migrated)
    tables = set(inspector.get_table_names())
    assert {"analysis_runs", "parcels", "evidence_items", "constraints"} <= tables


def test_parcel_geometry_round_trip_and_postgis_area(migrated) -> None:
    """ORM insert (EPSG:2180 WKT) → PostGIS ST_Area equals shapely's area."""
    import sqlalchemy
    from plot_domain.db import ParcelORM
    from shapely import wkt as shapely_wkt
    from sqlalchemy.orm import Session

    parcel_wkt = (
        "POLYGON((630880 497170,630920 497170,630920 497200,630880 497200,630880 497170))"
    )
    expected_area = shapely_wkt.loads(parcel_wkt).area
    parcel_id = f"itest:{uuid.uuid4().hex[:8]}"
    with Session(migrated) as session:
        session.add(
            ParcelORM(id=parcel_id, geom=f"SRID=2180;{parcel_wkt}")
        )
        session.commit()
        area, srid = session.execute(
            sqlalchemy.text(
                "SELECT ST_Area(geom), ST_SRID(geom) FROM parcels WHERE id = :id"
            ),
            {"id": parcel_id},
        ).one()
        session.execute(
            sqlalchemy.text("DELETE FROM parcels WHERE id = :id"), {"id": parcel_id}
        )
        session.commit()
    assert srid == 2180
    assert area == pytest.approx(expected_area)


def test_wrong_srid_is_rejected(migrated) -> None:
    """The geometry column enforces SRID 2180 (no silent CRS mixing, §26.3)."""
    from plot_domain.db import ParcelORM
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import Session

    with Session(migrated) as session:
        session.add(
            ParcelORM(
                id=f"itest:{uuid.uuid4().hex[:8]}",
                geom="SRID=4326;POLYGON((21 52,21.001 52,21.001 52.001,21 52.001,21 52))",
            )
        )
        with pytest.raises(DBAPIError):
            session.commit()
