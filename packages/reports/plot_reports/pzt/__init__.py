"""PZT draft package (Phase 15 — Dz.U. 2020 poz. 1609, t.j. 2022 poz. 1679, §13–18).

* :mod:`plot_reports.pzt.opisowa` — §14 część opisowa (one model → MD + JSON);
* :mod:`plot_reports.pzt.rysunkowa` — §15 część rysunkowa (scaled vector SVG +
  PDF via matplotlib's PDF backend; e-form naming ``PZT_{rrrr.mm.dd}_…``);
* :mod:`plot_reports.pzt.checklist` — the honest §13–18 done/missing/
  requires_projektant/requires_uprawnienia checklist.

The output is a DRAFT PACKAGE for a projektant — NEVER a projekt budowlany
(:data:`PZT_DISCLAIMER` is mandatory in every artifact; plan §15.4).
"""

from plot_reports.pzt.checklist import CHECKLIST_STATUSES, build_pzt_checklist
from plot_reports.pzt.opisowa import (
    PZT_CITATION_BASE,
    PZT_DISCLAIMER,
    PztOpisowa,
    PztSection,
    build_pzt_opisowa,
    render_pzt_opisowa_json,
    render_pzt_opisowa_markdown,
)
from plot_reports.pzt.rysunkowa import (
    PZT_DPI,
    PZT_MAX_PDF_BYTES,
    PZT_SCALE_DENOMINATOR,
    PztDrawing,
    pzt_filename,
    render_pzt_rysunkowa,
)

__all__ = [
    "CHECKLIST_STATUSES",
    "PZT_CITATION_BASE",
    "PZT_DISCLAIMER",
    "PZT_DPI",
    "PZT_MAX_PDF_BYTES",
    "PZT_SCALE_DENOMINATOR",
    "PztDrawing",
    "PztOpisowa",
    "PztSection",
    "build_pzt_checklist",
    "build_pzt_opisowa",
    "pzt_filename",
    "render_pzt_opisowa_json",
    "render_pzt_opisowa_markdown",
    "render_pzt_rysunkowa",
]
