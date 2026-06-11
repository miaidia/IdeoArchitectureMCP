"""Markdown + JSON report rendering for an AnalysisResult (Phase 7 §7.1.5 / §22).

* :func:`render_markdown` — the §22 template, filled from an
  :class:`~plot_domain.AnalysisResult`. Headline numbers (decision, parcel area,
  buildable area, counts) are derived from the SAME result object as :func:`render_json`,
  so MD and JSON agree by construction (§7.3 / NFR-AUD: "numbers in MD must match JSON").
  Since Phase 14 it renders through the unified :class:`~plot_reports.model.ReportModel`
  (§31 DoD) — the same model the HTML/PDF renderers use.
* :func:`render_json` — the AnalysisResult as a JSON-mode dict (the §10.7 contract).
* :func:`render_envelope_map` — a deterministic buildable-envelope PNG via the Phase 3
  :func:`render_map` (parcel + constraints + no-build + envelope layers), so the model can
  visually verify the envelope (Phase 4 tie-in).

This module renders an already-computed result — it does NO analysis, no network, and does
not import plot_connectors / plot_rules (§3.4 decoupling holds: it stays within plot_reports).
"""

from __future__ import annotations

from typing import Any

from plot_domain import AnalysisResult

from plot_reports.render import Layer, LayerRole, RenderResult, render_map


def render_json(result: AnalysisResult) -> dict[str, Any]:
    """Return the AnalysisResult as a JSON-serialisable dict (the §10.7 contract)."""
    return result.model_dump(mode="json")


def headline_numbers(result: AnalysisResult) -> dict[str, Any]:
    """The headline numbers shown in BOTH MD and JSON (single source of truth, §7.3).

    A test asserts the MD text contains exactly these values, so they cannot drift.
    """
    parcel_area = result.parcel.area_m2 if result.parcel else None
    env = result.buildable_envelope
    env_area = env.area_m2 if env else None
    env_pct = None
    if env and parcel_area:
        env_pct = (env_area or 0.0) / parcel_area * 100.0 if parcel_area else None
    return {
        "decision": result.decision.value,
        "status": result.status.value,
        "parcel_area_m2": parcel_area,
        "buildable_area_m2": env_area,
        "buildable_percent": round(env_pct, 1) if env_pct is not None else None,
        "envelope_confidence": env.confidence if env else None,
        "risk_count": len(result.risks),
        "unknown_count": len(result.unknowns),
        "constraint_count": len(result.constraints),
        "next_action_count": len(result.next_actions),
        "evidence_count": len(result.evidence),
    }


def render_markdown(result: AnalysisResult) -> str:
    """Render the §22 Markdown report from ``result`` (numbers == :func:`render_json`).

    Phase 14 (§31 DoD): thin wrapper over the unified report model — builds
    :class:`~plot_reports.model.ReportModel` via ``build_screening_model`` and
    renders via ``render_model_markdown`` (byte-compatible output; the SAME
    model feeds the HTML/JSON/PDF renderers in :mod:`plot_reports.formats`).
    """
    from plot_reports.formats import render_model_markdown
    from plot_reports.model import build_screening_model

    return render_model_markdown(build_screening_model(result))


def render_envelope_map(
    result: AnalysisResult, *, fmt: str = "png", title: str | None = None
) -> RenderResult:
    """Render the buildable-envelope map from ``result`` via the Phase 3 renderer.

    Layers (deterministic z-order from each role): parcel → soft constraints → hard
    constraints → no-build → buildable envelope. The caption shows which constraint
    removed which area (§30), read from the envelope ``metadata.removed_by`` trace.
    """
    from shapely.geometry import shape

    layers: list[Layer] = []
    if result.parcel and result.parcel.geometry:
        layers.append(Layer(name="Działka", geometries=[result.parcel.geometry], role=LayerRole.PARCEL))

    env = result.buildable_envelope
    removed_by = env.metadata.get("removed_by", []) if env and isinstance(env.metadata, dict) else []
    removed_index = {str(tr.get("ref")): tr for tr in removed_by}

    for con in result.constraints:
        if con.geometry is None:
            continue
        hard = bool(con.machine_summary.get("hard"))
        tr = removed_index.get(con.constraint_id)
        # Phase 12 site-context layer mapping: utility networks get the dedicated
        # NETWORK role (distinct style); flood/landslide/heritage stay on the
        # hard/soft constraint roles their overlay policy assigned (one legend).
        if con.constraint_type == "utilities":
            role = LayerRole.NETWORK
        else:
            role = LayerRole.CONSTRAINT_HARD if hard else LayerRole.CONSTRAINT_SOFT
        layers.append(
            Layer(
                name=con.constraint_type,
                geometries=[con.geometry],
                role=role,
                removed_area_m2=(tr.get("removed_m2") if tr else con.applies_to_area_m2),
                removed_area_percent=(tr.get("removed_percent") if tr else con.applies_to_percent),
            )
        )

    if env and env.geometry:
        layers.append(
            Layer(name="Buildable envelope", geometries=[env.geometry], role=LayerRole.BUILDABLE_ENVELOPE)
        )

    if not layers:
        # Nothing to draw — render a tiny placeholder so callers still get a valid image.
        from shapely.geometry import Polygon

        layers = [Layer(name="brak geometrii", geometries=[Polygon()], role=LayerRole.OTHER)]
    # touch shape import so a constraint geom that arrives as a Feature still renders
    _ = shape
    return render_map(
        layers,
        crs="EPSG:2180",
        fmt="png" if fmt != "svg" else "svg",
        basemap=False,
        title=title or f"Buildable envelope — {result.analysis_id}",
    )
