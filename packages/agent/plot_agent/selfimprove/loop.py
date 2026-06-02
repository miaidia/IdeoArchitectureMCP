"""DevLoop: edit → reload → re-score → before/after verdict (Phase 4 §4.1.A.4).

This is the loop Claude Code uses to build later phases:

1. :meth:`DevLoop.snapshot_before` runs the golden scenarios and records their scores.
2. an EXTERNAL edit happens (Claude Code edits code / a ruleset YAML).
3. :meth:`DevLoop.reload` re-loads rules the SAME way ``dev_reload`` does
   (fresh ``load_rulesets`` + ``importlib.reload`` of the MCP use-cases module, if
   importable) — it NEVER mutates ruleset legal values (§12, NFR-AUD-003).
4. :meth:`DevLoop.snapshot_after` re-runs the scenarios.
5. :meth:`DevLoop.verdict` emits a structured before/after :class:`Verdict` with a
   per-scenario improved | regressed | unchanged signal + screenshot uris + failures.

Reloading the use-cases module mirrors ``AppContext.reload`` (apps/mcp-server
runtime.py): ``importlib.reload`` of the reloadable module, never the transport
(Phase 0.5 / §2.4). ``load_rulesets`` reloads fresh per call (F-0133 / F-0440).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from plot_reports import ArtifactStore, LocalArtifactStore

from plot_agent.selfimprove.runner import DEFAULT_RULESET_DIR, ScenarioResult, run_scenario
from plot_agent.selfimprove.scenarios import GoldenScenario

# The reloadable MCP use-cases module (same path ``dev_reload`` reloads). Reloading it is
# optional — the harness still works if the MCP app isn't importable in this env.
_USECASES_MODULE = "plot_mcp_server.usecases"

# Aggregate-delta threshold below which a scenario counts as "unchanged" (noise floor).
_DELTA_EPS = 1e-6


@dataclass
class Verdict:
    """Structured before/after verdict for the dev-loop (§4.1.A.4)."""

    before_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    after_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    per_scenario: dict[str, str] = field(default_factory=dict)  # id -> improved|regressed|unchanged
    score_deltas: dict[str, dict[str, float]] = field(default_factory=dict)
    screenshots: dict[str, str] = field(default_factory=dict)  # id -> artifact uri
    failures: dict[str, list[str]] = field(default_factory=dict)  # id -> golden diff failures
    reloaded: bool = False
    reload_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reloaded": self.reloaded,
            "reload_count": self.reload_count,
            "per_scenario": self.per_scenario,
            "score_deltas": self.score_deltas,
            "before_scores": self.before_scores,
            "after_scores": self.after_scores,
            "screenshots": self.screenshots,
            "failures": self.failures,
        }

    def regressions(self) -> list[str]:
        return sorted(sid for sid, v in self.per_scenario.items() if v == "regressed")


@dataclass
class DevLoop:
    """Before/after self-improvement harness over a set of golden scenarios."""

    scenarios: list[GoldenScenario]
    ruleset_dir: str = DEFAULT_RULESET_DIR
    artifact_store: ArtifactStore = field(default_factory=LocalArtifactStore)
    reload_count: int = 0

    _before: dict[str, ScenarioResult] = field(default_factory=dict, repr=False)
    _after: dict[str, ScenarioResult] = field(default_factory=dict, repr=False)
    _reloaded: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------ #
    # Snapshots
    # ------------------------------------------------------------------ #
    def snapshot_before(self) -> dict[str, ScenarioResult]:
        """Run all scenarios and record the BEFORE snapshot."""
        self._before = {s.id: run_scenario(s, ruleset_dir=self.ruleset_dir) for s in self.scenarios}
        return self._before

    def reload(self) -> None:
        """Reload rules + use-cases the same way ``dev_reload`` does (no mutation).

        Rules are re-loaded fresh by each ``run_scenario`` call in :meth:`snapshot_after`
        (so an external YAML edit is already picked up). Here we ALSO ``importlib.reload``
        the MCP use-cases module if it is importable, mirroring ``AppContext.reload`` —
        proving the loop uses the same reload path. We never touch a transport, and we
        never write ruleset legal values (§12, NFR-AUD-003).
        """
        try:
            module = importlib.import_module(_USECASES_MODULE)
            importlib.reload(module)
        except Exception:  # noqa: BLE001 - MCP app may be absent in this env; non-fatal
            pass
        self.reload_count += 1
        self._reloaded = True

    def snapshot_after(self) -> dict[str, ScenarioResult]:
        """Re-run all scenarios and record the AFTER snapshot (fresh rules)."""
        self._after = {s.id: run_scenario(s, ruleset_dir=self.ruleset_dir) for s in self.scenarios}
        return self._after

    # ------------------------------------------------------------------ #
    # Verdict
    # ------------------------------------------------------------------ #
    def verdict(self, *, persist_screenshots: bool = True) -> Verdict:
        """Compute the before/after :class:`Verdict` (§4.1.A.4).

        ``improved`` / ``regressed`` / ``unchanged`` is decided on the SUM of known-score
        deltas (after − before) per scenario. Unknown scores never enter the delta so we
        never "improve" a scenario by hiding an unknown (§20.10).
        """
        if not self._before:
            self.snapshot_before()
        if not self._after:
            self.snapshot_after()

        v = Verdict(reloaded=self._reloaded, reload_count=self.reload_count)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        for sid in (s.id for s in self.scenarios):
            before = self._before[sid]
            after = self._after[sid]
            bvals = before.scores.known_values()
            avals = after.scores.known_values()
            v.before_scores[sid] = bvals
            v.after_scores[sid] = avals

            deltas = {
                name: round(avals.get(name, 0.0) - bvals.get(name, 0.0), 6)
                for name in sorted(set(bvals) | set(avals))
            }
            v.score_deltas[sid] = deltas
            total = sum(deltas.values())
            if total > _DELTA_EPS:
                v.per_scenario[sid] = "improved"
            elif total < -_DELTA_EPS:
                v.per_scenario[sid] = "regressed"
            else:
                v.per_scenario[sid] = "unchanged"

            golden_failures = after.diff_vs_expected.get("failures", [])
            if golden_failures:
                v.failures[sid] = list(golden_failures)

            if persist_screenshots:
                key = f"selfimprove/{ts}/{sid}.png"
                uri = self.artifact_store.put(key, after.render_bytes, after.render_mime)
                v.screenshots[sid] = uri
        return v
