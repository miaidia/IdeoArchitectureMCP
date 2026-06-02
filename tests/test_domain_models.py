"""Smoke test (a): every plot_domain model instantiates with example data."""

from __future__ import annotations

import enum
import inspect
from datetime import UTC, date, datetime
from typing import Any, get_args, get_origin

import plot_domain
import pydantic
import pytest
from plot_domain import (
    AnalysisResult,
    AnalysisRun,
    Constraint,
    ModuleResult,
    RiskItem,
    SourceRecord,
)
from plot_domain.enums import Decision, Severity

_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
}


def _all_domain_models() -> list[type[pydantic.BaseModel]]:
    """Collect every concrete pydantic model exported from plot_domain."""
    models: list[type[pydantic.BaseModel]] = []
    for obj in vars(plot_domain).values():
        if (
            inspect.isclass(obj)
            and issubclass(obj, pydantic.BaseModel)
            and obj is not pydantic.BaseModel
            and obj is not ModuleResult  # generic; covered separately
        ):
            models.append(obj)
    return models


def _example_for(field: pydantic.fields.FieldInfo) -> Any:
    """Best-effort example value for a required field, by annotation."""
    ann = field.annotation
    origin = get_origin(ann)
    # Enum -> first member
    if inspect.isclass(ann) and issubclass(ann, enum.Enum):
        return next(iter(ann))
    # Nested pydantic model -> build it recursively.
    if inspect.isclass(ann) and issubclass(ann, pydantic.BaseModel):
        return _build_example(ann)
    if ann is str:
        return "x"
    if ann is float:
        return 1.0
    if ann is int:
        return 1
    if ann is bool:
        return True
    if ann is datetime:
        return datetime(2026, 6, 3, tzinfo=UTC)
    if ann is date:
        return date(2026, 6, 3)
    if origin in (list,):
        return []
    if origin in (dict,):
        return {}
    # Unions / optionals already have defaults; fall back to a string.
    if get_args(ann):
        return "x"
    return "x"


def _build_example(model: type[pydantic.BaseModel]) -> pydantic.BaseModel:
    """Instantiate a model, supplying example values for its required fields."""
    kwargs: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if field.is_required():
            kwargs[name] = _example_for(field)
    return model(**kwargs)


@pytest.mark.parametrize("model", _all_domain_models(), ids=lambda m: m.__name__)
def test_every_model_instantiates(model: type[pydantic.BaseModel]) -> None:
    instance = _build_example(model)
    # Round-trips through JSON without error.
    dumped = instance.model_dump_json()
    assert isinstance(dumped, str) and dumped


def test_module_result_envelope_shape() -> None:
    """ModuleResult carries the canonical §9.4 keys."""
    mr: ModuleResult[dict[str, Any]] = ModuleResult(
        result={"area_m2": 100.0},
        evidence=["ev-1"],
        confidence=0.7,
        warnings=["w"],
        unknowns=["u"],
    )
    assert set(mr.model_dump().keys()) == {
        "result",
        "evidence",
        "confidence",
        "warnings",
        "unknowns",
    }


def test_rich_examples_instantiate() -> None:
    src = SourceRecord(
        source_id="s",
        source_type="official_register",  # type: ignore[arg-type]
        publisher="GUGiK",
        url_or_origin="https://uldk.gugik.gov.pl/",
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
        geometry=_SQUARE,
        severity=Severity.HIGH,
        confidence=0.5,
        human_summary="x",
    )
    run = AnalysisRun(
        id="a",
        input_hash="h",
        status="complete",  # type: ignore[arg-type]
        mode="quick_screening",  # type: ignore[arg-type]
        ruleset_version="v1",
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
        analysis_id=run.id,
        status="complete",  # type: ignore[arg-type]
        decision=Decision.OK_WITH_RISKS,
        constraints=[constraint],
        risks=[risk],
    )
    assert result.constraints[0].geometry == _SQUARE
    assert result.risks[0].risk_type.value == "flood"
