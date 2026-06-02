"""DrawingLoop: propose → render → score → critique → learn (Phase 4 §4.1.B.6).

Each :meth:`DrawingLoop.iterate`:

1. ``validate_hard`` — hard-constraint guard FIRST (§14.2, §4.4); a hard-violating
   proposal can NEVER be accepted no matter its score.
2. render — overlays the proposal footprint on parcel + envelope + constraints via the
   Phase 3 ``plot_reports.render_map`` so the model SEES its drawing.
3. score — ``score_proposal`` (explainable components).
4. critique — ``critique`` (which constraint/score, next-step suggestion).
5. learn — if valid AND above the acceptance threshold, store an exemplar (§4.1.B.5).

Every iteration is appended to an AUDIT log (F-0446): inputs, scores, artifact uri,
timestamp. :meth:`run` drives propose→…→learn over an iterable of proposals until a
plateau or the iteration budget is hit.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from plot_reports import ArtifactStore, LocalArtifactStore
from plot_reports.render import Layer, LayerRole, render_map

from plot_agent.context import AnalysisContext
from plot_agent.drawing.critique import StructuredCritique, critique
from plot_agent.drawing.memory import DrawingExemplarStore, Exemplar
from plot_agent.drawing.proposal import LayoutProposal, shape_class_for
from plot_agent.drawing.score import ProposalScore, score_proposal

# Proposals scoring at/above this total AND valid are accepted + stored as exemplars.
DEFAULT_ACCEPTANCE_THRESHOLD = 0.6
# run() stops when the best total improves by less than this over a full step (plateau).
DEFAULT_PLATEAU_EPS = 1e-4


@dataclass
class IterationResult:
    """One drawing-loop iteration (§4.1.B.6)."""

    iteration: int
    render_image_bytes: bytes
    render_mime: str
    score: ProposalScore
    critique: StructuredCritique
    accepted: bool
    artifact_uri: str | None = None
    exemplar: Exemplar | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "accepted": self.accepted,
            "artifact_uri": self.artifact_uri,
            "score": self.score.to_dict(),
            "critique": self.critique.to_dict(),
            "exemplar_id": self.exemplar.exemplar_id if self.exemplar else None,
        }


@dataclass
class AuditEntry:
    """One audit-trail record per iteration (F-0446)."""

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

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "program_type": self.program_type,
            "shape_class": self.shape_class,
            "inputs": self.inputs,
            "total": self.total,
            "valid": self.valid,
            "accepted": self.accepted,
            "violations": self.violations,
            "artifact_uri": self.artifact_uri,
        }


@dataclass
class DrawingLoop:
    """Generative drawing loop with hard guard, render, score, critique, learn, audit."""

    context: AnalysisContext
    exemplar_store: DrawingExemplarStore = field(default_factory=DrawingExemplarStore)
    artifact_store: ArtifactStore | None = None
    # Convenience: when given (and artifact_store is None), use a LocalArtifactStore rooted
    # here (tests pass a tmp dir so nothing lands in the repo's default .artifacts/).
    artifact_store_base: Path | str | None = None
    acceptance_threshold: float = DEFAULT_ACCEPTANCE_THRESHOLD
    audit_log: list[AuditEntry] = field(default_factory=list)
    _counter: int = field(default=0, repr=False)

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

    def iterate(self, proposal: LayoutProposal) -> IterationResult:
        """Run one full iteration over a single proposal (§4.1.B.6)."""
        self._counter += 1
        n = self._counter

        # 1) hard guard FIRST (validate_hard runs inside score_proposal too, but we score
        #    explicitly so the components stay visible even on a rejected proposal).
        score = score_proposal(proposal, self.context)
        crit = critique(proposal, score)

        # 2) render (the model sees its drawing) + persist the artifact.
        image_bytes, mime = self._render(proposal)
        ts = datetime.now(UTC)
        key = f"drawing/{ts.strftime('%Y%m%dT%H%M%S')}/iter-{n}.png"
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

        # 4) audit every iteration (F-0446): inputs, scores, artifact uri, timestamp.
        self.audit_log.append(
            AuditEntry(
                iteration=n,
                timestamp=ts.isoformat(),
                program_type=proposal.program_type,
                shape_class=shape_class,
                inputs=proposal.model_dump(mode="json"),
                total=score.total,
                valid=score.valid,
                accepted=accepted,
                violations=[v.to_dict() for v in score.violations],
                artifact_uri=artifact_uri,
            )
        )

        return IterationResult(
            iteration=n,
            render_image_bytes=image_bytes,
            render_mime=mime,
            score=score,
            critique=crit,
            accepted=accepted,
            artifact_uri=artifact_uri,
            exemplar=exemplar,
        )

    def run(
        self,
        proposals: Iterable[LayoutProposal],
        *,
        budget: int = 50,
        plateau_eps: float = DEFAULT_PLATEAU_EPS,
    ) -> list[IterationResult]:
        """Drive propose→render→score→critique→learn until plateau or budget (§4.1.B.6).

        Stops early when the best valid total stops improving (plateau) or the iteration
        ``budget`` is exhausted. Every iteration is still rendered, scored, and audited.
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
