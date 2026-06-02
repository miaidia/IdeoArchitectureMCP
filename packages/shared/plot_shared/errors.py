"""Error taxonomy for the Plot Analyzer.

Every analysis error must be classified into one of these categories
(NFR-REL-009: input | source | ruleset | geometry | parser | system).
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """Classification for analysis errors (NFR-REL-009)."""

    INPUT = "input"
    SOURCE = "source"
    RULESET = "ruleset"
    GEOMETRY = "geometry"
    PARSER = "parser"
    SYSTEM = "system"


class PlotAnalyzerError(Exception):
    """Base exception carrying an :class:`ErrorCategory`.

    All domain/infrastructure errors should subclass this so callers can route
    on ``category`` and surface a classified, auditable failure.
    """

    category: ErrorCategory = ErrorCategory.SYSTEM

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if category is not None:
            self.category = category

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.category.value}] {self.message}"
