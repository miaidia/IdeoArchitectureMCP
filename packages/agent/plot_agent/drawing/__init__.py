"""Generative architect-drawing loop (IMPLEMENTATION_PLAN.md Phase 4 §4.1.B).

The "model draws & learns to draw" loop. Every proposal is typed DATA validated by
rules — never trusted free-form output (§20.11, NFR-SEC-003):

* :mod:`plot_agent.drawing.proposal`  — typed :class:`LayoutProposal` + draw-DSL→shapely.
* :mod:`plot_agent.drawing.validate`  — :func:`validate_hard` (hard-constraint guard).
* :mod:`plot_agent.drawing.score`     — :func:`score_proposal` (explainable components).
* :mod:`plot_agent.drawing.critique`  — :func:`critique` (which constraint/score, next step).
* :mod:`plot_agent.drawing.memory`    — :class:`DrawingExemplarStore` (learn-to-draw memory).
* :mod:`plot_agent.drawing.loop`      — :class:`DrawingLoop` (propose→render→score→critique→learn).
"""

from plot_agent.drawing.critique import StructuredCritique, critique, critique_masterplan
from plot_agent.drawing.exemplars import (
    DensityBands,
    ExemplarStoreV2,
    density_class_for,
    format_exemplars_for_prompt,
)
from plot_agent.drawing.loop import AuditEntry, DrawingLoop, IterationResult
from plot_agent.drawing.memory import DrawingExemplarStore, Exemplar
from plot_agent.drawing.proposal import (
    BuildingSegment,
    BuildingSpec,
    LayoutProposal,
    MasterplanProposal,
    ParkingElement,
    PlacedRectangle,
    RoadElement,
    masterplan_program_type,
    parse_proposal,
    shape_class_for,
)
from plot_agent.drawing.score import ProposalScore, score_masterplan, score_proposal
from plot_agent.drawing.validate import Violation, validate_hard, validate_hard_masterplan
from plot_agent.drawing.variants import (
    DEFAULT_MASTERPLAN_AUDIT,
    DEFAULT_VARIANT_STORE,
    MasterplanAuditLog,
    MasterplanVariantStore,
)

__all__ = [
    "BuildingSegment",
    "BuildingSpec",
    "LayoutProposal",
    "MasterplanProposal",
    "ParkingElement",
    "PlacedRectangle",
    "RoadElement",
    "masterplan_program_type",
    "parse_proposal",
    "shape_class_for",
    "Violation",
    "validate_hard",
    "validate_hard_masterplan",
    "ProposalScore",
    "score_masterplan",
    "score_proposal",
    "StructuredCritique",
    "critique",
    "critique_masterplan",
    "DrawingExemplarStore",
    "DensityBands",
    "Exemplar",
    "ExemplarStoreV2",
    "density_class_for",
    "format_exemplars_for_prompt",
    "AuditEntry",
    "DrawingLoop",
    "IterationResult",
    "DEFAULT_MASTERPLAN_AUDIT",
    "DEFAULT_VARIANT_STORE",
    "MasterplanAuditLog",
    "MasterplanVariantStore",
]
