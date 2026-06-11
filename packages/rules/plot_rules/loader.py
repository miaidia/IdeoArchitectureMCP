"""Fresh ruleset loader (Phase 2 hot-reload backbone; evaluation engine in Phase 8).

Rules are versioned declarative YAML files under ``rulesets/PL/**/*.yaml`` following
base_assumptions §12.2 (``id``/``title``/``jurisdiction``/``valid_from``/``valid_to``/
``source_reference``/``inputs``/``outputs``/``logic``). In dev we deliberately load
them *fresh on every call* (no cross-call cache) so an edit to a YAML value is
reflected on the very next analysis without restarting the MCP process
(IMPLEMENTATION_PLAN.md Phase 2 §2.1.2, F-0133 / F-0440).

Phase 8 additions: every rule document is validated against the JSON Schema in
:mod:`plot_rules.schema` (invalid rules are skipped and reported on
``RulesetRegistry.errors`` — never half-loaded), and ``Rule`` carries ``severity``
plus the full normalized document (``raw``) so the evaluation engine in
:mod:`plot_rules.engine` can read the declarative ``applies_when``/``thresholds``/
``select``/``checks`` fields generically. Legal threshold VALUES live only in the
YAML files (Phase 8 anti-pattern guard) — this module stays value-free.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from plot_rules.schema import normalize_document, validate_rule_document


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
    # Phase 8: hard rules fail/blocker-note on violation or unknown; soft rules
    # surface warnings (§12.1 severity). Default "hard" = fail-closed.
    severity: str = "hard"
    # Phase 8: full normalized YAML document (dates -> ISO strings) — the engine
    # reads applies_when/thresholds/select/checks/input_defaults from here.
    raw: dict[str, Any] = field(default_factory=dict)


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
    # Phase 8: schema-validation failures ("<path>: <error>"); the offending rule
    # is skipped, never half-loaded. Surfaced via diagnostics/ruleset_explain.
    errors: tuple[str, ...] = field(default_factory=tuple)

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


# --------------------------------------------------------------------------- #
# Phase 14B (F-0509): in-memory registry cache, gated behind dev_hot_reload=False.
#
# * dev (PLOT_DEV_HOT_RELOAD=true): every call parses fresh — the Phase 2
#   hot-reload contract is byte-identical to the pre-cache behaviour.
# * prod (default, dev_hot_reload=False): the parsed registry is cached per
#   directory and re-validated by a cheap stat fingerprint (path, mtime_ns,
#   size of every YAML) on each call — an edited/added/removed rule file still
#   invalidates the cache (§18.3 "ruleset update without code change"), but the
#   hot path skips re-reading + re-validating every YAML document.
# --------------------------------------------------------------------------- #
_REGISTRY_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], RulesetRegistry]] = {}
_REGISTRY_CACHE_LOCK = threading.Lock()
_REGISTRY_CACHE_MAX = 4  # tiny LRU — one entry per ruleset dir in practice


def _registry_fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    """Cheap stat fingerprint over every rule YAML (no file reads)."""
    out: list[tuple[str, int, int]] = []
    for yaml_path in sorted(root.rglob("*.y*ml")):
        try:
            st = yaml_path.stat()
        except OSError:
            continue
        out.append((str(yaml_path), st.st_mtime_ns, st.st_size))
    return tuple(out)


def _cache_enabled() -> bool:
    """Cache only when dev hot-reload is OFF (plan: gate LRU behind DEV_HOT_RELOAD=false)."""
    from plot_shared import get_settings  # local import: keep module import light

    return not get_settings().dev_hot_reload


def clear_registry_cache() -> None:
    """Drop the cached registries (test seam / explicit invalidation)."""
    with _REGISTRY_CACHE_LOCK:
        _REGISTRY_CACHE.clear()


def load_rulesets(directory: str | Path = "rulesets/PL") -> RulesetRegistry:
    """Load every ``*.yaml``/``*.yml`` under ``directory`` into a registry.

    Dev (``dev_hot_reload=True``): no caching across calls (Phase 2 §2.1.3) —
    every call re-reads the files from disk and recomputes ``ruleset_version``
    from their bytes. Prod: a stat-fingerprint cache (F-0509) returns the parsed
    registry when no rule file changed; any mtime/size/path change re-parses.
    Missing directory yields an empty registry (so the server still boots in CI).
    """
    root = Path(directory)
    if not root.exists():
        return RulesetRegistry(root=str(root))

    if _cache_enabled():
        key = str(root.resolve())
        fingerprint = _registry_fingerprint(root)
        with _REGISTRY_CACHE_LOCK:
            cached = _REGISTRY_CACHE.get(key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
        registry = _load_rulesets_fresh(root)
        with _REGISTRY_CACHE_LOCK:
            _REGISTRY_CACHE.pop(key, None)
            _REGISTRY_CACHE[key] = (fingerprint, registry)
            while len(_REGISTRY_CACHE) > _REGISTRY_CACHE_MAX:
                _REGISTRY_CACHE.pop(next(iter(_REGISTRY_CACHE)))
        return registry

    return _load_rulesets_fresh(root)


def _load_rulesets_fresh(root: Path) -> RulesetRegistry:
    """The original fresh-per-call loader body (unchanged semantics)."""
    rules: list[Rule] = []
    errors: list[str] = []
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
        # Phase 8: structural JSON Schema validation; a malformed rule is skipped
        # and reported (never half-loaded into evaluations).
        normalized = normalize_document(doc)
        doc_errors = validate_rule_document(normalized)
        if doc_errors:
            errors.extend(f"{yaml_path}: {e}" for e in doc_errors)
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
                severity=str(doc.get("severity", "hard")),
                raw=normalized,
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
        errors=tuple(errors),
    )


def _as_str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)
