"""Initial schema: §26.1 tables + §26.2 indexes (PostGIS, EPSG:2180).

Creates all base_assumptions §26.1 tables and the 8 required §26.2 indexes
(5 GIST spatial + 3 btree). Analytical geometry columns use SRID 2180 (PUWG1992,
§26.3); input CRS is kept as the ``parcels.input_crs`` metadata column. No raster
blobs are stored — rasters live in object storage referenced by URI (§26.3).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# GeoAlchemy2 Geometry column type with explicit SRID (Phase 0.4 / §26.3).
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Analytical CRS for Poland (PUWG1992). Input CRS is stored as metadata (§26.3).
SRID = 2180


def upgrade() -> None:
    # PostGIS must be available for the Geometry columns + GIST indexes.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("input_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("ruleset_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("tenant_id", sa.String()),
    )

    op.create_table(
        "source_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("publisher", sa.String()),
        sa.Column("url", sa.String()),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("license", sa.String()),
        sa.Column("legal_status", sa.String()),
        sa.Column("confidence", sa.Float()),
        sa.Column("metadata_json", sa.JSON()),
    )

    op.create_table(
        "parcels",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("external_id", sa.String()),
        sa.Column("teryt", sa.String()),
        sa.Column("obr", sa.String()),
        sa.Column("number", sa.String()),
        # Analytical geometry in EPSG:2180 (§26.3).
        sa.Column("geom", Geometry("GEOMETRY", srid=SRID, spatial_index=False)),
        # Input CRS kept as metadata, not a second geometry column (§26.3).
        sa.Column("input_crs", sa.String()),
        sa.Column("area_m2", sa.Float()),
        sa.Column("source_id", sa.String()),
        sa.Column("snapshot_id", sa.String()),
    )

    op.create_table(
        "investment_areas",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("geom", Geometry("GEOMETRY", srid=SRID, spatial_index=False)),
        sa.Column("parcel_ids", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("source_id", sa.String(), sa.ForeignKey("source_records.id")),
        sa.Column("subject_type", sa.String()),
        sa.Column("subject_id", sa.String()),
        sa.Column("claim", sa.String()),
        sa.Column("value_json", sa.JSON()),
        sa.Column("confidence", sa.Float()),
        sa.Column("geometry", Geometry("GEOMETRY", srid=SRID, spatial_index=False)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "planning_acts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("municipality_id", sa.String()),
        sa.Column("act_type", sa.String()),
        sa.Column("title", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("source_id", sa.String(), sa.ForeignKey("source_records.id")),
        sa.Column("metadata_json", sa.JSON()),
    )

    op.create_table(
        "planning_zones",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("act_id", sa.String(), sa.ForeignKey("planning_acts.id")),
        sa.Column("symbol", sa.String()),
        sa.Column("geom", Geometry("GEOMETRY", srid=SRID, spatial_index=False)),
        sa.Column("attributes_json", sa.JSON()),
    )

    op.create_table(
        "constraints",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("constraint_type", sa.String()),
        sa.Column("severity", sa.String()),
        sa.Column("confidence", sa.Float()),
        sa.Column("geom", Geometry("GEOMETRY", srid=SRID, spatial_index=False)),
        sa.Column("summary", sa.String()),
        sa.Column("rule_id", sa.String()),
        sa.Column("source_id", sa.String(), sa.ForeignKey("source_records.id")),
    )

    op.create_table(
        "buildable_envelopes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("geom", Geometry("GEOMETRY", srid=SRID, spatial_index=False)),
        sa.Column("area_m2", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("metadata_json", sa.JSON()),
    )

    op.create_table(
        "capacity_scenarios",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("scenario_type", sa.String()),
        sa.Column("metrics_json", sa.JSON()),
        sa.Column("risks_json", sa.JSON()),
        sa.Column("geom", Geometry("GEOMETRY", srid=SRID, spatial_index=False)),
    )

    op.create_table(
        "risk_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("risk_type", sa.String()),
        sa.Column("severity", sa.String()),
        sa.Column("confidence", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("summary", sa.String()),
        sa.Column("mitigation", sa.String()),
        sa.Column("source_id", sa.String(), sa.ForeignKey("source_records.id")),
    )

    op.create_table(
        "unknown_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("topic", sa.String()),
        sa.Column("severity", sa.String()),
        sa.Column("reason", sa.String()),
        sa.Column("suggested_action", sa.String()),
    )

    op.create_table(
        "report_artifacts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("artifact_type", sa.String()),
        # URI only — no raster blobs in the DB (§26.3).
        sa.Column("uri", sa.String()),
        sa.Column("content_hash", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "overrides",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("analysis_runs.id")),
        sa.Column("user_id", sa.String()),
        sa.Column("target_type", sa.String()),
        sa.Column("target_id", sa.String()),
        sa.Column("before_json", sa.JSON()),
        sa.Column("after_json", sa.JSON()),
        sa.Column("reason", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    # --- §26.2 indexes: 5 GIST (spatial) + 3 btree ---
    op.create_index("parcels_geom_gix", "parcels", ["geom"], postgresql_using="gist")
    op.create_index(
        "planning_zones_geom_gix", "planning_zones", ["geom"], postgresql_using="gist"
    )
    op.create_index("constraints_geom_gix", "constraints", ["geom"], postgresql_using="gist")
    op.create_index(
        "buildable_envelopes_geom_gix",
        "buildable_envelopes",
        ["geom"],
        postgresql_using="gist",
    )
    op.create_index(
        "evidence_items_geometry_gix",
        "evidence_items",
        ["geometry"],
        postgresql_using="gist",
    )
    op.create_index("source_records_retrieved_idx", "source_records", ["retrieved_at"])
    op.create_index("analysis_runs_input_hash_idx", "analysis_runs", ["input_hash"])
    op.create_index(
        "planning_acts_municipality_idx",
        "planning_acts",
        ["municipality_id", "act_type", "status"],
    )


def downgrade() -> None:
    op.drop_index("planning_acts_municipality_idx", table_name="planning_acts")
    op.drop_index("analysis_runs_input_hash_idx", table_name="analysis_runs")
    op.drop_index("source_records_retrieved_idx", table_name="source_records")
    op.drop_index("evidence_items_geometry_gix", table_name="evidence_items")
    op.drop_index("buildable_envelopes_geom_gix", table_name="buildable_envelopes")
    op.drop_index("constraints_geom_gix", table_name="constraints")
    op.drop_index("planning_zones_geom_gix", table_name="planning_zones")
    op.drop_index("parcels_geom_gix", table_name="parcels")

    for table in (
        "overrides",
        "report_artifacts",
        "unknown_items",
        "risk_items",
        "capacity_scenarios",
        "buildable_envelopes",
        "constraints",
        "planning_zones",
        "planning_acts",
        "evidence_items",
        "investment_areas",
        "parcels",
        "source_records",
        "analysis_runs",
    ):
        op.drop_table(table)
