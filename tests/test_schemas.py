"""Smoke tests (b) + (c): JSON Schema files validate examples; Pydantic round-trip.

(b) Each schemas/*.json validates a hand-written example dict via jsonschema.
(c) A Pydantic -> JSON Schema agreement test for analysis-result: a model instance
    serialized to JSON validates against the committed schema, and the committed
    schema matches a freshly generated one (kept in sync by schemas/generate.py).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from plot_domain import AnalysisResult, Constraint, RiskItem, SourceRecord

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"


def _load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_source_record_example_validates() -> None:
    schema = _load("source-record.schema.json")
    example = {
        "source_id": "src-1",
        "source_type": "official_register",
        "publisher": "GUGiK",
        "url_or_origin": "https://uldk.gugik.gov.pl/",
        "retrieved_at": "2026-06-03T00:00:00Z",
        "valid_from": "2026-01-01",
        "valid_to": None,
        "license": "CC-BY-4.0",
        "legal_status": "binding",
        "geometry_precision": "cadastral",
        "freshness": "current",
        "confidence": 0.9,
        "notes": "Example.",
    }
    Draft202012Validator(schema).validate(example)


def test_constraint_example_validates() -> None:
    schema = _load("constraint.schema.json")
    example = {
        "constraint_id": "c-1",
        "constraint_type": "flood_zone",
        "source_id": "src-1",
        "source_legal_status": "binding",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        "applies_to_area_m2": 50.0,
        "applies_to_percent": 25.0,
        "rule_id": "rule-1",
        "rule_version": "1.0.0",
        "severity": "high",
        "confidence": 0.8,
        "human_summary": "Partially within flood zone.",
        "machine_summary": {"overlap_m2": 50.0},
        "mitigation": None,
    }
    Draft202012Validator(schema).validate(example)


def test_risk_register_example_validates() -> None:
    schema = _load("risk-register.schema.json")
    example = {
        "id": "r-1",
        "analysis_id": "a-1",
        "risk_type": "flood",
        "severity": "high",
        "confidence": "medium",
        "status": "detected",
        "summary": "Flood hazard overlaps the parcel.",
        "mitigation": None,
        "source_id": "src-1",
    }
    Draft202012Validator(schema).validate(example)


def test_analysis_input_example_validates() -> None:
    schema = _load("analysis-input.schema.json")
    example = {
        "input": {
            "parcel_id": "141201_1.0001.1867/2",
            "address": None,
            "point": None,
            "geometry": None,
            "uploaded_files": [],
        },
        "analysis_mode": "quick_screening",
        "investment_goal": {
            "type": "single_family",
            "target_gfa_m2": None,
            "target_units": None,
            "risk_preference": "balanced",
        },
        "options": {
            "country": "PL",
            "ruleset_version": "latest",
            "strict_sources_only": False,
            "include_auxiliary_sources": True,
            "return_maps": True,
            "return_evidence": True,
            "max_runtime_profile": "standard",
        },
    }
    Draft202012Validator(schema).validate(example)


def test_analysis_result_example_validates() -> None:
    schema = _load("analysis-result.schema.json")
    example = {
        "analysis_id": "a-1",
        "status": "complete",
        "decision": "OK_WITH_RISKS",
        "scores": {
            "buildability": 0.0,
            "planning_certainty": 0.0,
            "infrastructure": 0.0,
            "terrain": 0.0,
            "environmental_risk": 0.0,
            "procedural_risk": 0.0,
            "data_confidence": 0.0,
        },
        "parcel": None,
        "planning": {},
        "constraints": [],
        "buildable_envelope": None,
        "capacity_scenarios": [],
        "risks": [],
        "unknowns": [],
        "next_actions": [],
        "evidence": [],
        "artifacts": [],
    }
    Draft202012Validator(schema).validate(example)


def test_mcp_tools_schema_lists_public_tools() -> None:
    schema = _load("mcp-tools.schema.json")
    tools = schema["properties"]["tools"]["properties"]
    # base_assumptions §10.3 enumerates 20 public tools.
    assert len(tools) == 20
    assert "parcel_analyze" in tools
    assert "diagnostics_run" in tools


def test_pydantic_instance_round_trips_against_committed_schema() -> None:
    """(c) A real AnalysisResult instance validates against the committed schema."""
    src = SourceRecord(
        source_id="s",
        source_type="official_register",  # type: ignore[arg-type]
        publisher="GUGiK",
        url_or_origin="x",
        retrieved_at=datetime(2026, 6, 3, tzinfo=UTC),
        license="unknown",
        legal_status="binding",  # type: ignore[arg-type]
        geometry_precision="cadastral",  # type: ignore[arg-type]
        freshness="current",  # type: ignore[arg-type]
        confidence=0.5,
    )
    constraint = Constraint(
        constraint_id="c",
        constraint_type="flood",
        source_id=src.source_id,
        source_legal_status="binding",  # type: ignore[arg-type]
        severity="high",  # type: ignore[arg-type]
        confidence=0.5,
        human_summary="x",
    )
    risk = RiskItem(
        id="r",
        risk_type="flood",  # type: ignore[arg-type]
        severity="high",  # type: ignore[arg-type]
        confidence="medium",  # type: ignore[arg-type]
        status="detected",  # type: ignore[arg-type]
        summary="x",
    )
    result = AnalysisResult(
        analysis_id="a",
        status="complete",  # type: ignore[arg-type]
        decision="OK_WITH_RISKS",  # type: ignore[arg-type]
        constraints=[constraint],
        risks=[risk],
    )
    payload = json.loads(result.model_dump_json())
    schema = _load("analysis-result.schema.json")
    Draft202012Validator(schema).validate(payload)


def test_committed_schema_matches_generator() -> None:
    """(c) Committed analysis-result schema equals a freshly generated one."""
    committed = _load("analysis-result.schema.json")
    fresh = AnalysisResult.model_json_schema()
    # Compare the model-derived body (committed file adds $schema/$id/title meta).
    for key, value in fresh.items():
        assert committed.get(key) == value, f"schema drift on key: {key}"
