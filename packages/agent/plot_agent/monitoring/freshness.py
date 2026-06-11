"""Freshness monitors + self-diagnostics (Phase 13 / v1 Phase 11 §11.1.2; F-0439–0446).

Three checks, all side-effect free:

* :func:`source_freshness` (F-0439) — every SourceRecord's ``retrieved_at`` vs a
  per-source max-age config; stale sources degrade the report;
* :func:`ruleset_freshness` (F-0440) — registry version + each rule's
  ``valid_from``/``valid_to`` span vs today (expired / not-yet-in-force rules
  degrade the report);
* :func:`connector_autotest` (F-0441) — runs the EXISTING connector
  ``healthcheck()`` contract (Phase 6, NFR-REL-005) over a named bundle.

Safe-failure defaults (F-0443/0445): a degraded source-freshness report yields
``evaluation_mode_for(...) == "conservative"`` so unknowns on hard rules are
reported as potential blockers, never silently passed. Fresh sources preserve
the caller's default mode. The verdict is consumed by (review M2):

* the chłonność composite — the freshness node stores the mode in the graph
  context; the ``capacity`` output and the ``koncepcja`` gate payload carry
  ``evaluation_mode`` plus a conservative banner the model sees;
* ``run_full_due_diligence`` — stores the verdict on the analysis
  (``planning['_freshness']``); the analysis-bound ``propose_layout`` path
  reads it and FORCES conservative mode in
  :func:`plot_planning.wt_validators.run_inter_building_checks` (which feeds
  the mode into :func:`plot_rules.evaluate`) when the verdict is degraded;
* ``diagnostics_run`` — reports the recommendation over all stored analyses.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from plot_shared import get_settings

#: Source staleness statuses.
FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class FreshnessConfig:
    """Max-age policy per source (config, not law)."""

    default_max_age_days: float = 90.0
    #: source_id PREFIX → max age in days (e.g. {"pl.gugik.uldk": 30}).
    per_source_max_age_days: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_settings(cls) -> FreshnessConfig:
        return cls(default_max_age_days=get_settings().source_max_age_days)

    def max_age_for(self, source_id: str) -> float:
        for prefix, days in self.per_source_max_age_days.items():
            if source_id.startswith(prefix):
                return float(days)
        return float(self.default_max_age_days)


@dataclass
class SourceFreshnessReport:
    """Per-source staleness assessment (F-0439)."""

    items: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    checked_at: str = ""

    @property
    def stale_sources(self) -> list[str]:
        return [str(i["source_id"]) for i in self.items if i["status"] == STALE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": list(self.items),
            "degraded": self.degraded,
            "stale_sources": self.stale_sources,
            "checked_at": self.checked_at,
        }


def _retrieved_at(source: Any) -> datetime | None:
    raw = source.get("retrieved_at") if isinstance(source, Mapping) else getattr(source, "retrieved_at", None)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _source_id(source: Any) -> str:
    if isinstance(source, Mapping):
        return str(source.get("source_id") or "unknown")
    return str(getattr(source, "source_id", "unknown"))


def source_freshness(
    sources: Iterable[Any],
    *,
    config: FreshnessConfig | None = None,
    now: datetime | None = None,
) -> SourceFreshnessReport:
    """Assess SourceRecord freshness (F-0439): ``retrieved_at`` vs max age.

    ``sources`` are SourceRecord models or their ``model_dump`` dicts (the
    ``planning['_sources']`` shape). A source without a parseable
    ``retrieved_at`` is UNKNOWN and degrades the report (fail-safe: an
    unverifiable age is never assumed fresh, §21).
    """
    cfg = config or FreshnessConfig.from_settings()
    now = now or datetime.now(UTC)
    report = SourceFreshnessReport(checked_at=now.isoformat())
    for source in sources:
        sid = _source_id(source)
        retrieved = _retrieved_at(source)
        max_age = cfg.max_age_for(sid)
        if retrieved is None:
            report.items.append(
                {"source_id": sid, "status": UNKNOWN, "age_days": None, "max_age_days": max_age}
            )
            report.degraded = True
            continue
        age_days = (now - retrieved).total_seconds() / 86400.0
        status = STALE if age_days > max_age else FRESH
        if status == STALE:
            report.degraded = True
        report.items.append(
            {
                "source_id": sid,
                "status": status,
                "age_days": round(age_days, 2),
                "max_age_days": max_age,
            }
        )
    return report


def evaluation_mode_for(report: SourceFreshnessReport, *, default: str = "strict") -> str:
    """Rule-evaluation mode from freshness (F-0443/0445 safe-failure default).

    Degraded freshness (stale/unverifiable sources) → ``"conservative"`` so
    unknowns on hard rules surface as potential blockers wherever the mode is
    consumed (chłonność composite payloads, the analysis-bound
    ``propose_layout`` inter-building checks, the diagnostics recommendation —
    see the module docstring). Fresh sources preserve the caller's ``default``
    unchanged — freshness only ever TIGHTENS the mode, it never relaxes it.
    """
    return "conservative" if report.degraded else default


# --------------------------------------------------------------------------- #
# Ruleset freshness (F-0440)
# --------------------------------------------------------------------------- #
@dataclass
class RulesetFreshnessReport:
    """Registry version + per-rule validity-span assessment (F-0440)."""

    ruleset_version: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleset_version": self.ruleset_version,
            "items": list(self.items),
            "degraded": self.degraded,
            "checked_at": self.checked_at,
        }


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def ruleset_freshness(registry: Any, *, today: date | None = None) -> RulesetFreshnessReport:
    """Check every loaded rule's ``valid_from``/``valid_to`` span vs today (F-0440).

    ``registry`` is a :class:`plot_rules.RulesetRegistry`. A rule whose span has
    EXPIRED or has not yet entered into force degrades the report (the ruleset
    needs an update — legal values are dated data, never constants, §0v2.2).
    """
    today = today or datetime.now(UTC).date()
    report = RulesetFreshnessReport(
        ruleset_version=str(getattr(registry, "ruleset_version", "")),
        checked_at=datetime.now(UTC).isoformat(),
    )
    for rule in getattr(registry, "rules", []):
        valid_from = _parse_date(getattr(rule, "valid_from", None))
        valid_to = _parse_date(getattr(rule, "valid_to", None))
        if valid_from is None:
            status = UNKNOWN  # undated rule — cannot prove it is in force
            report.degraded = True
        elif valid_from > today:
            status = "not_yet_in_force"
            report.degraded = True
        elif valid_to is not None and valid_to < today:
            status = "expired"
            report.degraded = True
        else:
            status = "in_force"
        report.items.append(
            {
                "rule_id": getattr(rule, "id", "unknown"),
                "status": status,
                "valid_from": getattr(rule, "valid_from", None),
                "valid_to": getattr(rule, "valid_to", None),
            }
        )
    return report


# --------------------------------------------------------------------------- #
# Connector autotest (F-0441) — runs the EXISTING healthcheck contract.
# --------------------------------------------------------------------------- #
async def connector_autotest(connectors: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Run ``healthcheck()`` (Phase 6 contract, NFR-REL-005) on a named bundle.

    Objects without a ``healthcheck`` attribute report ``no_healthcheck``;
    a probe that RAISES reports unhealthy with the error — the autotest itself
    never raises (self-diagnostics must not crash diagnostics, F-0442).
    """
    results: list[dict[str, Any]] = []
    for name, connector in connectors.items():
        probe = getattr(connector, "healthcheck", None)
        if not callable(probe):
            results.append({"name": name, "status": "no_healthcheck", "healthy": None})
            continue
        try:
            health = await probe()
        except Exception as exc:
            results.append(
                {
                    "name": name,
                    "status": "probe_error",
                    "healthy": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        dump = getattr(health, "model_dump", None)
        doc = dump(mode="json") if callable(dump) else dict(health)
        results.append({"name": name, **doc})
    return results
