"""Orchestration layer: self-improvement dev-loop + generative drawing loop (Phase 4).

IMPLEMENTATION_PLAN.md Phase 4 (§4.1–§4.4). Two cooperating loops, both built on the
Phase 3 renderer (``plot_reports.render_map``) and the Phase 2 reloadable use-cases:

* :mod:`plot_agent.selfimprove` — golden-scenario dev-loop (edit → reload → re-score →
  before/after verdict). This is the loop Claude Code uses to build later phases.
* :mod:`plot_agent.drawing` — generative "draw like an architect & learn to draw" loop
  (propose typed layout → hard-constraint guard → render → score → critique → learn).

This package is the orchestration layer: it depends on plot_domain / plot_shared /
plot_rules / plot_reports, and MUST NOT import plot_connectors (Phase 4 §4.4, no cycle).
"""

from plot_agent.context import AnalysisContext

__version__ = "0.1.0"

__all__ = ["AnalysisContext"]
