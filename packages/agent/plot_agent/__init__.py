"""Orchestration layer: self-improve loop + drawing loop + quick_screening (Phase 4/7).

IMPLEMENTATION_PLAN.md Phase 4 (§4.1–§4.4) and Phase 7 §C. Built on the Phase 3 renderer
(``plot_reports.render_map``), the Phase 6 connectors, and the Phase 7 envelope engine:

* :mod:`plot_agent.selfimprove` — golden-scenario dev-loop (edit → reload → re-score →
  before/after verdict). This is the loop Claude Code uses to build later phases.
* :mod:`plot_agent.drawing` — generative "draw like an architect & learn to draw" loop
  (propose typed layout → hard-constraint guard → render → score → critique → learn).
* :mod:`plot_agent.analysis` — the §27 shared use-cases. :func:`run_quick_screening`
  orchestrates parcel resolve → metrics → risk layers → overlay/envelope → red flags +
  decision → :class:`~plot_domain.AnalysisResult`, with INJECTED connectors (zero network).

As the orchestration layer (§9.2 layer 6 / Phase 7 §C) it MAY import plot_connectors and
plot_envelope; the decoupling rule that stays intact is connectors↛rules and rules↛connectors.
"""

from plot_agent.context import AnalysisContext

__version__ = "0.1.0"

__all__ = ["AnalysisContext"]
