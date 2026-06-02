"""Persistence layer: SQLAlchemy 2.x + GeoAlchemy2 ORM for the §26.1 tables.

Single source of the DB schema. Intentionally NOT imported by
``plot_domain.__init__`` so the pure domain models stay pydantic-only. Connectors
and rules must never import this module (Phase 1.4 decoupling).

Geometry storage rules (base_assumptions §26.3):
  * Analytical geometry is stored in EPSG:2180 (PUWG1992) — see ``srid=2180`` below.
  * Input geometry / CRS is kept as metadata columns, not as a second geometry.
  * Raster data is NOT stored in the DB — only object-storage URIs (no raster blobs).
  * Large GeoJSON is served as resources/files, not inlined.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

# GeoAlchemy2 geometry column type with explicit SRID (Phase 0.4 / base_assumptions §26.3).
from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Analytical CRS for Poland (PUWG1992). Input CRS is stored separately as metadata.
ANALYTICAL_SRID = 2180


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class AnalysisRunORM(Base):
    """analysis_runs (base_assumptions §26.1)."""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    input_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[str | None] = mapped_column(String)


class ParcelORM(Base):
    """parcels (base_assumptions §26.1). Analytical geom in EPSG:2180."""

    __tablename__ = "parcels"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String)
    teryt: Mapped[str | None] = mapped_column(String)
    obr: Mapped[str | None] = mapped_column(String)
    number: Mapped[str | None] = mapped_column(String)
    # Analytical geometry in EPSG:2180 (§26.3).
    geom: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=ANALYTICAL_SRID))
    # Input CRS kept as metadata (§26.3), not as a second geometry column.
    input_crs: Mapped[str | None] = mapped_column(String)
    area_m2: Mapped[float | None] = mapped_column(Float)
    source_id: Mapped[str | None] = mapped_column(String)
    snapshot_id: Mapped[str | None] = mapped_column(String)


class InvestmentAreaORM(Base):
    """investment_areas (base_assumptions §26.1)."""

    __tablename__ = "investment_areas"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    geom: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=ANALYTICAL_SRID))
    parcel_ids: Mapped[Any] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceRecordORM(Base):
    """source_records (base_assumptions §26.1)."""

    __tablename__ = "source_records"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String)
    url: Mapped[str | None] = mapped_column(String)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    license: Mapped[str | None] = mapped_column(String)
    legal_status: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[Any] = mapped_column(JSON, default=dict)


class EvidenceItemORM(Base):
    """evidence_items (base_assumptions §26.1). Has its own GIST-indexed geometry."""

    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.id"))
    subject_type: Mapped[str | None] = mapped_column(String)
    subject_id: Mapped[str | None] = mapped_column(String)
    claim: Mapped[str | None] = mapped_column(String)
    value_json: Mapped[Any] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    geometry: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=ANALYTICAL_SRID))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanningActORM(Base):
    """planning_acts (base_assumptions §26.1)."""

    __tablename__ = "planning_acts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    municipality_id: Mapped[str | None] = mapped_column(String)
    act_type: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    valid_from: Mapped[date | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[date | None] = mapped_column(DateTime(timezone=True))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.id"))
    metadata_json: Mapped[Any] = mapped_column(JSON, default=dict)


class PlanningZoneORM(Base):
    """planning_zones (base_assumptions §26.1). GIST-indexed geometry."""

    __tablename__ = "planning_zones"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    act_id: Mapped[str | None] = mapped_column(ForeignKey("planning_acts.id"))
    symbol: Mapped[str | None] = mapped_column(String)
    geom: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=ANALYTICAL_SRID))
    attributes_json: Mapped[Any] = mapped_column(JSON, default=dict)


class ConstraintORM(Base):
    """constraints (base_assumptions §26.1). GIST-indexed geometry."""

    __tablename__ = "constraints"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    constraint_type: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=ANALYTICAL_SRID))
    summary: Mapped[str | None] = mapped_column(String)
    rule_id: Mapped[str | None] = mapped_column(String)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.id"))


class BuildableEnvelopeORM(Base):
    """buildable_envelopes (base_assumptions §26.1). GIST-indexed geometry."""

    __tablename__ = "buildable_envelopes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    geom: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=ANALYTICAL_SRID))
    area_m2: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[Any] = mapped_column(JSON, default=dict)


class CapacityScenarioORM(Base):
    """capacity_scenarios (base_assumptions §26.1)."""

    __tablename__ = "capacity_scenarios"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    scenario_type: Mapped[str | None] = mapped_column(String)
    metrics_json: Mapped[Any] = mapped_column(JSON, default=dict)
    risks_json: Mapped[Any] = mapped_column(JSON, default=list)
    geom: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=ANALYTICAL_SRID))


class RiskItemORM(Base):
    """risk_items (base_assumptions §26.1)."""

    __tablename__ = "risk_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    risk_type: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(String)
    mitigation: Mapped[str | None] = mapped_column(String)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.id"))


class UnknownItemORM(Base):
    """unknown_items (base_assumptions §26.1)."""

    __tablename__ = "unknown_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    topic: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String)
    suggested_action: Mapped[str | None] = mapped_column(String)


class ReportArtifactORM(Base):
    """report_artifacts (base_assumptions §26.1). URIs only — no raster blobs (§26.3)."""

    __tablename__ = "report_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    artifact_type: Mapped[str | None] = mapped_column(String)
    uri: Mapped[str | None] = mapped_column(String)
    content_hash: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OverrideORM(Base):
    """overrides (base_assumptions §26.1). Audit fields per NFR-AUD-003."""

    __tablename__ = "overrides"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    user_id: Mapped[str | None] = mapped_column(String)
    target_type: Mapped[str | None] = mapped_column(String)
    target_id: Mapped[str | None] = mapped_column(String)
    before_json: Mapped[Any] = mapped_column(JSON, default=dict)
    after_json: Mapped[Any] = mapped_column(JSON, default=dict)
    reason: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Silence unused-import warnings for column types kept for future migrations.
_RESERVED_TYPES = (Integer,)
