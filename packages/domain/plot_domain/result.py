"""Canonical analytical-module return type (base_assumptions §9.4).

Every analytical module returns ``{result, evidence, confidence, warnings, unknowns}``.
``ModuleResult`` is generic over the ``result`` payload so module authors keep
strong typing while the envelope shape stays uniform across the system.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ModuleResult(BaseModel, Generic[T]):
    """Uniform envelope returned by every analytical module (§9.4)."""

    model_config = ConfigDict(extra="forbid")

    result: T = Field(description="The module's primary structured result payload.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence item ids backing the result (NFR-AUD-001).",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall confidence in the result, 0.0-1.0 (confidence-first UX).",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings raised while producing the result.",
    )
    unknowns: list[str] = Field(
        default_factory=list,
        description="Things that could not be determined; must persist regardless of score (§20.10).",
    )
