"""DrawingLoop: propose → render → score → critique → learn (Phase 4 §4.1.B.6; v2 Phase 11).

Each :meth:`DrawingLoop.iterate`:

1. ``validate_hard`` — hard-constraint guard FIRST (§14.2, §4.4); a hard-violating
   proposal can NEVER be accepted no matter its score.
2. render — overlays the proposal footprint on parcel + envelope + constraints via the
   Phase 3 ``plot_reports.render_map`` so the model SEES its drawing.
3. score — ``score_proposal`` (explainable components).
4. critique — ``critique`` (which constraint/score, next-step suggestion).
5. learn — if valid AND above the acceptance threshold, store an exemplar (§4.1.B.5).

Every iteration is appended to an AUDIT log (F-0446): inputs (+ a canonical SHA-256
``inputs_hash``), scores, structured critique, the model's optional ``rationale`` and
the rendered artifact uri. :meth:`run` drives propose→…→learn over an iterable of
proposals until a plateau or the iteration budget is hit.

Phase 11 (§11.1.3) extends the SAME loop to :class:`MasterplanProposal` iterations
(:meth:`iterate_masterplan`, auto-dispatched from :meth:`iterate`):

validate (``validate_hard_masterplan`` inside ``score_masterplan``) → Phase 10
inter-building WT/ppoż checks (``run_inter_building_checks``) → Phase 11 staging
checks (``check_staging``, soft) → Phase 9 capacity metrics → score (hard-blocker
dominance §14.2) → **structured critique** (``critique_masterplan``: rule_ids +
subjects + required-vs-actual, capacity gap vs the ``capacity_generate_scenarios``
base-scenario PUM target, staging warnings, improvements vs the previous iteration)
→ render (renderer v2 with the red violation overlay) → learn (accepted masterplans
persist to :class:`ExemplarStoreV2` with a thumbnail, keyed
``shape__program__density``).

``rationale`` (plan §11.1.6) is the model's free-text design reasoning for the
iteration: it is recorded in the audit entry and surfaced in the deliverable ONLY —
it is never passed to validators, scoring or critique (NFR-SEC-003).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plot_reports import ArtifactStore, LocalArtifactStore, render_masterplan
from plot_reports.render import Layer, LayerRole, render_map
from plot_rules import RuleStatus

from plot_agent.context import AnalysisContext
from plot_agent.drawing.critique import StructuredCritique, critique, critique_masterplan
from plot_agent.drawing.exemplars import ExemplarStoreV2, density_class_for
from plot_agent.drawing.memory import DrawingExemplarStore, Exemplar
from plot_agent.drawing.proposal import (
    LayoutProposal,
    MasterplanProposal,
    masterplan_program_type,
    shape_class_for,
)
from plot_agent.drawing.score import ProposalScore, score_masterplan, score_proposal

# Proposals scoring at/above this total AND valid are accepted + stored as exemplars.
DEFAULT_ACCEPTANCE_THRESHOLD = 0.6
# run() stops when the best total improves by less than this over a full step (plateau).
DEFAULT_PLATEAU_EPS = 1e-4


def inputs_hash(inputs: dict[str, Any]) -> str:
    """Canonical SHA-256 of a proposal's JSON inputs (audit F-0446, Phase 11)."""
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class IterationResult:
    """One drawing-loop iteration (§4.1.B.6; masterplan extras Phase 11 §11.1.3)."""

    iteration: int
    render_image_bytes: bytes
    render_mime: str
    score: ProposalScore
    critique: StructuredCritique
    accepted: bool
    artifact_uri: str | None = None
    exemplar: Exemplar | None = None
    # Phase 11 masterplan extras (None/empty on the v1 LayoutProposal path).
    rationale: str | None = None
    metrics: Any | None = None  # plot_planning.MasterplanMetrics
    inter_building_checks: list[Any] = field(default_factory=list)  # RuleChecks
    staging_checks: list[Any] = field(default_factory=list)  # StageChecks
    style_metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "accepted": self.accepted,
            "artifact_uri": self.artifact_uri,
            "score": self.score.to_dict(),
            "critique": self.critique.to_dict(),
            "exemplar_id": self.exemplar.exemplar_id if self.exemplar else None,
            "rationale": self.rationale,
        }


@dataclass
class AuditEntry:
    """One audit-trail record per iteration (F-0446; Phase 11 extension §11.1.3/6).

    Phase 11 adds: ``inputs_hash`` (canonical SHA-256 of ``inputs``), the score
    ``components``, the full structured ``critique`` and the model's ``rationale``
    (documentation only — never an input to validation, NFR-SEC-003).
    ``analysis_id`` stamps the entry with the loop's owning analysis (today the
    MCP layer's adhoc id; real ids Phase 12) so session-scoped consumers (the
    koncepcja rationale section, previous-components seeding) can filter the
    process-global audit log instead of reading entries from unrelated sessions.
    """

    iteration: int
    timestamp: str
    program_type: str
    shape_class: str
    inputs: dict[str, object]
    total: float
    valid: bool
    accepted: bool
    violations: list[dict[str, object]]
    artifact_uri: str | None
    inputs_hash: str = ""
    components: dict[str, float] = field(default_factory=dict)
    critique: dict[str, object] = field(default_factory=dict)
    rationale: str | None = None
    analysis_id: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "program_type": self.program_type,
            "shape_class": self.shape_class,
            "inputs": self.inputs,
            "inputs_hash": self.inputs_hash,
            "total": self.total,
            "valid": self.valid,
            "accepted": self.accepted,
            "violations": self.violations,
            "artifact_uri": self.artifact_uri,
            "components": self.components,
            "critique": self.critique,
            "rationale": self.rationale,
            "analysis_id": self.analysis_id,
            "schema_version": self.schema_version,
        }


@dataclass
class DrawingLoop:
    """Generative drawing loop with hard guard, render, score, critique, learn, audit.

    Phase 11 fields: ``indicators`` (the Phase 8 canonical MPZP/WZ map feeding
    capacity metrics + staging + the critique's capacity target), ``pum_target_m2``
    (explicit base-scenario PUM target; computed lazily from the context envelope +
    indicators via ``generate_capacity_scenarios`` when None), ``override_store`` /
    ``analysis_id`` (audited expert overrides consumed by the Phase 10 validators).
    The default exemplar store is the persisted :class:`ExemplarStoreV2`
    (IS-A v1 store, plan §11.1.4).
    """

    context: AnalysisContext
    exemplar_store: DrawingExemplarStore = field(default_factory=ExemplarStoreV2)
    artifact_store: ArtifactStore | None = None
    # Convenience: when given (and artifact_store is None), use a LocalArtifactStore rooted
    # here (tests pass a tmp dir so nothing lands in the repo's default .artifacts/).
    artifact_store_base: Path | str | None = None
    acceptance_threshold: float = DEFAULT_ACCEPTANCE_THRESHOLD
    audit_log: list[AuditEntry] = field(default_factory=list)
    indicators: dict[str, Any] = field(default_factory=dict)
    pum_target_m2: float | None = None
    override_store: Any | None = None
    analysis_id: str | None = None
    # Seedable previous-iteration score components: the critique's positive
    # reinforcement compares against these (within a loop instance they update per
    # iteration; the MCP path seeds them from the last masterplan audit entry so
    # one-iteration-per-call sessions still get the "improved vs previous" signal).
    previous_components: dict[str, float] | None = None
    _counter: int = field(default=0, repr=False)
    _pum_target_cache: float | None = field(default=None, repr=False)
    _pum_target_computed: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.artifact_store is None:
            self.artifact_store = (
                LocalArtifactStore(self.artifact_store_base)
                if self.artifact_store_base is not None
                else LocalArtifactStore()
            )

    # ------------------------------------------------------------------ #
    def _render(self, proposal: LayoutProposal) -> tuple[bytes, str]:
        """Render parcel + envelope + constraints + proposal footprint (Phase 3 §3.1)."""
        layers = [Layer(name="Parcel", geometries=[self.context.parcel], role=LayerRole.PARCEL)]
        for i, hc in enumerate(self.context.hard_constraints):
            layers.append(Layer(name=f"No-build {i + 1}", geometries=[hc], role=LayerRole.NO_BUILD))
        for i, sc in enumerate(self.context.soft_constraints):
            layers.append(
                Layer(name=f"Soft {i + 1}", geometries=[sc], role=LayerRole.CONSTRAINT_SOFT)
            )
        layers.append(
            Layer(
                name="Buildable envelope",
                geometries=[self.context.buildable_envelope],
                role=LayerRole.BUILDABLE_ENVELOPE,
            )
        )
        # The proposed footprint is drawn as a distinct OTHER-role layer (blue-grey) so
        # the model can see where it placed the building relative to the envelope.
        try:
            footprint = proposal.footprint_geometry()
            layers.append(
                Layer(
                    name=f"Proposed footprint ({proposal.program_type})",
                    geometries=[footprint],
                    role=LayerRole.OTHER,
                    style={"facecolor": "#1f77b4", "edgecolor": "#0b3d66", "alpha": 0.6},
                )
            )
        except ValueError:
            pass  # empty proposal: render context only; the guard will reject it
        result = render_map(layers, title=f"Drawing iteration {self._counter + 1}")
        return result.data, result.mime_type

    def iterate(
        self,
        proposal: LayoutProposal | MasterplanProposal,
        *,
        rationale: str | None = None,
    ) -> IterationResult:
        """Run one full iteration over a single proposal (§4.1.B.6; v2 dispatch §11.1.3).

        A :class:`MasterplanProposal` takes the masterplan path
        (:meth:`iterate_masterplan`); a v1 :class:`LayoutProposal` keeps the Phase 4
        behaviour byte-for-byte. ``rationale`` is audit-only (NFR-SEC-003).
        """
        if isinstance(proposal, MasterplanProposal):
            return self.iterate_masterplan(proposal, rationale=rationale)
        self._counter += 1
        n = self._counter

        # 1) hard guard FIRST (validate_hard runs inside score_proposal too, but we score
        #    explicitly so the components stay visible even on a rejected proposal).
        score = score_proposal(proposal, self.context)
        crit = critique(proposal, score)

        # 2) render (the model sees its drawing) + persist the artifact.
        image_bytes, mime = self._render(proposal)
        ts = datetime.now(UTC)
        # uuid suffix: keys must stay unique at sub-second granularity — the MCP
        # path builds a fresh loop per call (always iter-1), so two same-second
        # calls would otherwise overwrite the audited render (F-0446 integrity).
        key = f"drawing/{ts.strftime('%Y%m%dT%H%M%S')}/iter-{n}-{uuid.uuid4().hex[:8]}.png"
        assert self.artifact_store is not None  # set in __post_init__
        artifact_uri = self.artifact_store.put(key, image_bytes, mime)

        # 3) learn — accept + store ONLY if valid AND above threshold (§4.4: a hard
        #    violation makes score.valid False, so it can never be accepted).
        accepted = bool(score.valid and score.total >= self.acceptance_threshold)
        shape_class = shape_class_for(self.context.parcel_geom())
        exemplar: Exemplar | None = None
        if accepted:
            exemplar = self.exemplar_store.store(
                shape_class=shape_class,
                program_type=proposal.program_type,
                proposal=proposal,
                score_total=score.total,
                components=score.components,
                parcel_area_m2=self.context.parcel_area_m2(),
            )

        # 4) audit every iteration (F-0446): inputs (+hash), scores, critique,
        #    rationale, artifact uri, timestamp.
        inputs = proposal.model_dump(mode="json")
        self.audit_log.append(
            AuditEntry(
                iteration=n,
                timestamp=ts.isoformat(),
                program_type=proposal.program_type,
                shape_class=shape_class,
                inputs=inputs,
                inputs_hash=inputs_hash(inputs),
                total=score.total,
                valid=score.valid,
                accepted=accepted,
                violations=[v.to_dict() for v in score.violations],
                artifact_uri=artifact_uri,
                components=dict(score.components),
                critique=crit.to_dict(),
                rationale=rationale,
                analysis_id=self.analysis_id,
                schema_version=1,
            )
        )
        self.previous_components = dict(score.components)

        return IterationResult(
            iteration=n,
            render_image_bytes=image_bytes,
            render_mime=mime,
            score=score,
            critique=crit,
            accepted=accepted,
            artifact_uri=artifact_uri,
            exemplar=exemplar,
            rationale=rationale,
        )

    # ------------------------------------------------------------------ #
    # Phase 11 §11.1.3 — masterplan iteration
    # ------------------------------------------------------------------ #
    def _pum_target(self) -> float | None:
        """The base-scenario PUM target (explicit field, else computed once).

        Mirrors what ``capacity_generate_scenarios`` returns for the same context:
        the loop computes it from the context's envelope/parcel areas + the
        indicator map, so the critique's capacity gap cites the SAME number the
        model saw in step 3 of the target workflow (plan §11.1.3).
        """
        if self.pum_target_m2 is not None:
            return self.pum_target_m2
        if self._pum_target_computed:
            return self._pum_target_cache
        self._pum_target_computed = True
        from plot_planning import generate_capacity_scenarios

        scenario_set = generate_capacity_scenarios(
            envelope_area_m2=self.context.envelope_area_m2(),
            parcel_area_m2=self.context.parcel_area_m2(),
            indicators=self.indicators,
            analysis_id=self.analysis_id,
        )
        for scenario in scenario_set.scenarios:
            if scenario.scenario_type == "base":
                pum = scenario.metrics.get("pum_m2")
                if isinstance(pum, int | float):
                    self._pum_target_cache = float(pum)
        return self._pum_target_cache

    def iterate_masterplan(
        self,
        proposal: MasterplanProposal,
        *,
        rationale: str | None = None,
    ) -> IterationResult:
        """One masterplan iteration: validate → checks → staging → metrics → score →
        critique → render → learn → audit (plan §11.1.3).

        The Phase 9/10/11 engines are REUSED, never re-implemented: capacity
        metrics (``masterplan_metrics``), inter-building WT/ppoż validators
        (``run_inter_building_checks`` with audited overrides) and the soft
        staging checks (``check_staging``). ``rationale`` flows to audit/result
        only — validators and scoring never see it (NFR-SEC-003).
        """
        from plot_planning import CapacityConfig, check_staging, masterplan_metrics
        from plot_planning.wt_validators import run_inter_building_checks

        self._counter += 1
        n = self._counter
        parcel = self.context.parcel_geom()
        indicator_map = dict(self.indicators)

        # 1) capacity metrics (Phase 9) + inter-building checks (Phase 10) + staging
        #    (Phase 11, soft) — all pure engines over the typed proposal.
        metrics = masterplan_metrics(
            proposal,
            parcel,
            indicator_map,
            config=CapacityConfig(),
            registry=self.context.ruleset,
        )
        checks = run_inter_building_checks(
            proposal,
            parcel,
            self.context.ruleset,
            srodmiejska=proposal.zabudowa_srodmiejska,
            overrides=self.override_store,
            analysis_id=self.analysis_id,
            metrics=metrics,
            indicators=indicator_map,
        )
        staging_checks = check_staging(
            proposal, metrics, self.context.ruleset, indicators=indicator_map
        )

        # 2) score (validate_hard_masterplan runs FIRST inside; failing hard checks
        #    become hard violations — §14.2 dominance) + structured critique.
        score = score_masterplan(
            proposal, self.context, metrics, inter_building_checks=checks
        )
        crit = critique_masterplan(
            proposal,
            score,
            metrics=metrics,
            checks=checks,
            staging_checks=staging_checks,
            pum_target_m2=self._pum_target(),
            previous_components=self.previous_components,
        )

        # 3) render (renderer v2; failing checks' evidence = red violation overlay).
        violation_geoms = [
            c.geometry_evidence["geometry"]
            for c in checks
            if c.status is RuleStatus.FAIL
            and c.geometry_evidence
            and c.geometry_evidence.get("geometry")
        ]
        render = render_masterplan(
            proposal,
            parcel,
            metrics=metrics,
            envelope=self.context.envelope_geom(),
            violations=violation_geoms or None,
        )
        ts = datetime.now(UTC)
        # uuid suffix: same-second uniqueness (see iterate() — F-0446 integrity).
        key = f"masterplan/{ts.strftime('%Y%m%dT%H%M%S')}/iter-{n}-{uuid.uuid4().hex[:8]}.png"
        assert self.artifact_store is not None  # set in __post_init__
        artifact_uri = self.artifact_store.put_render(key, render)

        # 4) learn — accepted masterplans persist to the v2 exemplar store with the
        #    rendered thumbnail, keyed (shape_class, program_type, density_class).
        accepted = bool(score.valid and score.total >= self.acceptance_threshold)
        shape_class = shape_class_for(parcel)
        program_type = masterplan_program_type(proposal)
        exemplar: Exemplar | None = None
        if accepted:
            if isinstance(self.exemplar_store, ExemplarStoreV2):
                exemplar = self.exemplar_store.store(
                    shape_class=shape_class,
                    program_type=program_type,
                    proposal=proposal,
                    score_total=score.total,
                    components=score.components,
                    parcel_area_m2=self.context.parcel_area_m2(),
                    density_class=density_class_for(
                        metrics.totals.get("intensywnosc")
                    ),
                    thumbnail_png=render.data,
                )
            else:  # v1 store injected — keep its byte-compatible contract
                exemplar = self.exemplar_store.store(
                    shape_class=shape_class,
                    program_type=program_type,
                    proposal=proposal,  # type: ignore[arg-type]
                    score_total=score.total,
                    components=score.components,
                    parcel_area_m2=self.context.parcel_area_m2(),
                )

        # 5) audit every iteration (F-0446): inputs hash, scores, critique,
        #    rationale, rendered artifact uri.
        inputs = proposal.model_dump(mode="json")
        self.audit_log.append(
            AuditEntry(
                iteration=n,
                timestamp=ts.isoformat(),
                program_type=program_type,
                shape_class=shape_class,
                inputs=inputs,
                inputs_hash=inputs_hash(inputs),
                total=score.total,
                valid=score.valid,
                accepted=accepted,
                violations=[v.to_dict() for v in score.violations],
                artifact_uri=artifact_uri,
                components=dict(score.components),
                critique=crit.to_dict(),
                rationale=rationale,
                analysis_id=self.analysis_id,
                schema_version=2,
            )
        )
        self.previous_components = dict(score.components)

        return IterationResult(
            iteration=n,
            render_image_bytes=render.data,
            render_mime=render.mime_type,
            score=score,
            critique=crit,
            accepted=accepted,
            artifact_uri=artifact_uri,
            exemplar=exemplar,
            rationale=rationale,
            metrics=metrics,
            inter_building_checks=list(checks),
            staging_checks=list(staging_checks),
            style_metadata=render.style_metadata,
        )

    def run(
        self,
        proposals: Iterable[LayoutProposal | MasterplanProposal],
        *,
        budget: int = 50,
        plateau_eps: float = DEFAULT_PLATEAU_EPS,
    ) -> list[IterationResult]:
        """Drive propose→render→score→critique→learn until plateau or budget (§4.1.B.6).

        Stops early when the best valid total stops improving (plateau) or the iteration
        ``budget`` is exhausted. Every iteration is still rendered, scored, and audited.
        Works over v1 layouts AND v2 masterplans (the iterate dispatch).
        """
        results: list[IterationResult] = []
        best_total = float("-inf")
        for proposal in proposals:
            if len(results) >= budget:
                break
            res = self.iterate(proposal)
            results.append(res)
            if res.score.valid:
                if res.score.total <= best_total + plateau_eps and best_total > float("-inf"):
                    break  # plateau: no meaningful improvement
                best_total = max(best_total, res.score.total)
        return results
