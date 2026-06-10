"""Planning stability score component (Phase 8 Task 3; F-0126).

A SIMPLE, fully-traced heuristic over act status + age — explicitly
``basis: heuristic`` (it is an analytic aid, not a legal judgment, §20.11). Every
factor that moved the score is listed in the trace so the report can explain the
number (NFR-AUD-006).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from plot_domain import PlanningAct

#: Status keywords → base score. Unrecognized status → 0.5 + an explicit factor.
#: ORDER MATTERS: negative/repealed statuses are checked FIRST because the
#: in-force keyword "obowiąz" is a substring of "nieobowiązujący" — a repealed
#: act must never score as "act in force".
_STATUS_BASE: tuple[tuple[tuple[str, ...], float, str], ...] = (
    (("uchylon", "nieobowiąz", "nieobowiaz", "archiw"), 0.1, "act repealed/archived"),
    (("projekt", "draft"), 0.3, "draft act — provisions may change before adoption"),
    (("procedur", "w toku", "wyłożon", "wylozon"), 0.4, "planning procedure in progress"),
    (("obowiąz", "obowiaz", "in_force", "prawnie wiążący", "prawnie wiazacy", "realizowany"), 0.8, "act in force"),
)


def stability_score(act: PlanningAct, *, today: date | None = None) -> dict[str, Any]:
    """Score the planning stability of ``act`` in [0, 1] with a factor trace.

    Heuristic components: lifecycle status (in force vs draft vs in-procedure)
    and act age (a very fresh act is unlikely to be amended soon: +0.1; an act
    older than 15 years is a frequent replacement candidate under the planning
    reform: −0.1). Unknown date → no age factor + an explicit unknown factor.
    """
    today = today or date.today()
    status_text = (act.status or "").lower()
    base = 0.5
    factors: list[dict[str, Any]] = []
    matched = False
    for keywords, score, note in _STATUS_BASE:
        if any(k in status_text for k in keywords):
            base = score
            factors.append({"factor": "status", "value": act.status, "score": score, "note": note})
            matched = True
            break
    if not matched:
        factors.append(
            {"factor": "status", "value": act.status, "score": base, "note": "unrecognized status → neutral base"}
        )

    score = base
    if act.valid_from is not None:
        age_years = (today - act.valid_from).days / 365.25
        if age_years < 1.0:
            score += 0.1
            factors.append({"factor": "age", "years": round(age_years, 2), "delta": 0.1, "note": "recently adopted"})
        elif age_years > 15.0:
            score -= 0.1
            factors.append(
                {"factor": "age", "years": round(age_years, 2), "delta": -0.1,
                 "note": "old act — elevated replacement likelihood (planning reform)"}
            )
        else:
            factors.append({"factor": "age", "years": round(age_years, 2), "delta": 0.0})
    else:
        factors.append({"factor": "age", "delta": 0.0, "note": "legal-act date unknown — no age component"})

    return {
        "act_id": act.id,
        "score": round(max(0.0, min(1.0, score)), 2),
        "basis": "heuristic",
        "factors": factors,
    }
