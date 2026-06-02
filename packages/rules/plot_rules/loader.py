"""Fresh ruleset loader (Phase 2 hot-reload backbone; engine logic lands in Phase 8).

Rules are versioned declarative YAML files under ``rulesets/PL/**/*.yaml`` following
base_assumptions §12.2 (``id``/``title``/``jurisdiction``/``valid_from``/``valid_to``/
``source_reference``/``inputs``/``outputs``/``logic``). In dev we deliberately load
them *fresh on every call* (no cross-call cache) so an edit to a YAML value is
reflected on the very next analysis without restarting the MCP process
(IMPLEMENTATION_PLAN.md Phase 2 §2.1.2, F-0133 / F-0440).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Rule:
    """A single declarative rule (base_assumptions §12.2)."""

    id: str
    title: str
    jurisdiction: str
    category: str
    valid_from: str | None
    valid_to: str | None
    source_reference: str | None
    inputs: list[str]
    outputs: list[str]
    logic: Any
    path: str


@dataclass(frozen=True)
class RulesetRegistry:
    """In-memory registry of all loaded rules + a derived ``ruleset_version``.

    ``ruleset_version`` is a content hash over the loaded rule files so any edit to
    a YAML value changes the version — this is what ``ruleset_explain`` /
    ``diagnostics_run`` report and what proves a hot-reload took effect.
    """

    rules: tuple[Rule, ...] = field(default_factory=tuple)
    ruleset_version: str = "PL-empty"
    categories: tuple[str, ...] = field(default_factory=tuple)
    root: str = ""

    def by_category(self, category: str) -> list[Rule]:
        return [r for r in self.rules if r.category == category]

    def get(self, rule_id: str) -> Rule | None:
        for r in self.rules:
            if r.id == rule_id:
                return r
        return None


def _category_for(path: Path, root: Path) -> str:
    """Derive the ruleset category from the first path segment under the root.

    e.g. ``rulesets/PL/planning/zoning.yaml`` -> ``planning`` (base_assumptions §12.3).
    """
    rel = path.relative_to(root)
    return rel.parts[0] if len(rel.parts) > 1 else "uncategorized"


def load_rulesets(directory: str | Path = "rulesets/PL") -> RulesetRegistry:
    """Load every ``*.yaml``/``*.yml`` under ``directory`` fresh into a registry.

    No caching across calls (dev hot-reload requirement, Phase 2 §2.1.3): every call
    re-reads the files from disk and recomputes ``ruleset_version`` from their bytes.
    Missing directory yields an empty registry (so the server still boots in CI).
    """
    root = Path(directory)
    if not root.exists():
        return RulesetRegistry(root=str(root))

    rules: list[Rule] = []
    hasher = hashlib.sha256()
    for yaml_path in sorted(root.rglob("*.y*ml")):
        if not yaml_path.is_file():
            continue
        raw = yaml_path.read_bytes()
        hasher.update(yaml_path.name.encode("utf-8"))
        hasher.update(raw)
        doc = yaml.safe_load(raw) or {}
        if not isinstance(doc, dict) or "id" not in doc:
            # Tolerate sources.md-style sidecars / non-rule yaml without crashing.
            continue
        category = _category_for(yaml_path, root)
        rules.append(
            Rule(
                id=str(doc["id"]),
                title=str(doc.get("title", "")),
                jurisdiction=str(doc.get("jurisdiction", "PL")),
                category=category,
                valid_from=_as_str_or_none(doc.get("valid_from")),
                valid_to=_as_str_or_none(doc.get("valid_to")),
                source_reference=_as_str_or_none(doc.get("source_reference")),
                inputs=[str(x) for x in (doc.get("inputs") or [])],
                outputs=[str(x) for x in (doc.get("outputs") or [])],
                logic=doc.get("logic"),
                path=str(yaml_path),
            )
        )

    rules.sort(key=lambda r: r.id)
    version = "PL-empty" if not rules else f"PL-{hasher.hexdigest()[:12]}"
    categories = tuple(sorted({r.category for r in rules}))
    return RulesetRegistry(
        rules=tuple(rules),
        ruleset_version=version,
        categories=categories,
        root=str(root),
    )


def _as_str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
