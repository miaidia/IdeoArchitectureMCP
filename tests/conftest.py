"""Shared test fixtures and example builders for the domain models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from plot_domain import (
    AnalysisResult,
    AnalysisRun,
    Constraint,
    RiskItem,
    SourceRecord,
)
from plot_domain.enums import (
    AnalysisMode,
    AnalysisStatus,
    ConfidenceLevel,
    Decision,
    Freshness,
    GeometryPrecision,
    LegalStatus,
    RiskStatus,
    RiskType,
    Severity,
    SourceType,
)

_SQUARE_GEOJSON: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
}


@pytest.fixture
def example_source_record() -> SourceRecord:
    return SourceRecord(
        source_id="src-1",
        source_type=SourceType.OFFICIAL_REGISTER,
        publisher="GUGiK",
        url_or_origin="https://uldk.gugik.gov.pl/",
        retrieved_at=datetime(2026, 6, 3, tzinfo=UTC),
        valid_from=date(2026, 1, 1),
        valid_to=None,
        license="CC-BY-4.0",
        legal_status=LegalStatus.BINDING,
        geometry_precision=GeometryPrecision.CADASTRAL,
        freshness=Freshness.CURRENT,
        confidence=0.9,
        notes="Example cadastral source.",
    )


@pytest.fixture
def example_constraint() -> Constraint:
    return Constraint(
        constraint_id="c-1",
        constraint_type="flood_zone",
        source_id="src-1",
        source_legal_status=LegalStatus.BINDING,
        geometry=_SQUARE_GEOJSON,
        applies_to_area_m2=50.0,
        applies_to_percent=25.0,
        rule_id="rule-flood-1",
        rule_version="1.0.0",
        severity=Severity.HIGH,
        confidence=0.8,
        human_summary="Parcel partially within a 1% flood hazard zone.",
        machine_summary={"zone": "Q1%", "overlap_m2": 50.0},
        mitigation="Elevate finished floor level.",
    )


@pytest.fixture
def example_risk_item() -> RiskItem:
    return RiskItem(
        id="r-1",
        analysis_id="a-1",
        risk_type=RiskType.FLOOD,
        severity=Severity.HIGH,
        confidence=ConfidenceLevel.MEDIUM,
        status=RiskStatus.DETECTED,
        summary="Flood hazard overlaps the parcel.",
        mitigation="Hydrological assessment recommended.",
        source_id="src-1",
    )


@pytest.fixture
def example_analysis_run() -> AnalysisRun:
    return AnalysisRun(
        id="a-1",
        input_hash="deadbeef",
        status=AnalysisStatus.COMPLETE,
        mode=AnalysisMode.QUICK_SCREENING,
        ruleset_version="PL-2026.06",
        source_snapshot_id="snap-1",
        tenant_id="tenant-1",
        created_at=datetime(2026, 6, 3, tzinfo=UTC),
        updated_at=datetime(2026, 6, 3, tzinfo=UTC),
    )


@pytest.fixture
def example_analysis_result(example_constraint: Constraint, example_risk_item: RiskItem) -> AnalysisResult:
    return AnalysisResult(
        analysis_id="a-1",
        status=AnalysisStatus.COMPLETE,
        decision=Decision.OK_WITH_RISKS,
        constraints=[example_constraint],
        risks=[example_risk_item],
    )
