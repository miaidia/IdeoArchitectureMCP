"""Portfolio batch analysis (Phase 13 / v1 Phase 11 §11.1.3; §4.4 ``portfolio_batch``).

``run_portfolio_analysis`` takes a list of parcel inputs and:

1. **dedupes** them (same parcel id / identical input payload hash) — a
   duplicate is recorded as ``duplicate_of``, analyzed ONCE;
2. **fans out quick screenings** sequentially with an optional backpressure
   delay between parcels (NFR-PERF-014 — public services are never hammered;
   queued execution via Dramatiq is the worker's concern, this function is the
   shared in-proc unit both paths run);
3. **isolates failures** (NFR-REL-008): one crashing parcel is marked
   ``failed`` with its error and the batch CONTINUES;
4. **ranks** the analyzed parcels by decision class then scores, builds the
   red-flag table + manual-review list (§4.4);
5. **exports** the result table as CSV + JSON + GeoJSON artifacts through the
   existing ArtifactStore (F-0419). GPKG export is intentionally NOT generated
   here: writing GeoPackage pulls the heavy geopandas/fiona IO stack into the
   batch path — the GeoJSON artifact carries the same features and converts
   losslessly; the gap is reported in the result, never hidden.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import uuid
from typing import Any

from plot_domain import AnalysisInput
from plot_shared import get_logger

from plot_agent.analysis import Connectors, run_quick_screening
from plot_agent.analysis.factory import DEFAULT_STORE

_log = get_logger(__name__)

#: Decision → rank (lower = better). Failed/unknown ranks last.
_DECISION_RANK = {
    "OK": 0,
    "OK_WITH_RISKS": 1,
    "NEEDS_MANUAL_REVIEW": 2,
    "LIKELY_BLOCKED": 3,
}
_FAILED_RANK = 4


def _dedupe_key(parcel: dict[str, Any]) -> str:
    """Stable identity of one parcel input: parcel id, else the payload hash."""
    pid = parcel.get("parcel_id")
    if pid:
        return f"id:{pid}"
    blob = json.dumps(parcel, sort_keys=True, default=str)
    return f"hash:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]}"


def _item_sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
    rank = float(item.get("decision_rank", _FAILED_RANK))
    scores = item.get("scores") or {}
    return (
        rank,
        -float(scores.get("buildability", 0.0)),
        -float(scores.get("data_confidence", 0.0)),
    )


async def run_portfolio_analysis(
    parcels: list[dict[str, Any]],
    *,
    connectors: Connectors,
    ruleset_dir: str = "rulesets/PL",
    batch_id: str | None = None,
    store: Any | None = None,
    artifact_store: Any | None = None,
    delay_s: float = 0.0,
    status_callback: Any | None = None,
) -> dict[str, Any]:
    """Run the §4.4 portfolio batch over injected connectors (zero network in tests)."""
    batch_id = batch_id or f"batch:{uuid.uuid4().hex[:12]}"
    result_store = store if store is not None else DEFAULT_STORE

    # ------------------------------------------------------------------ #
    # 1) Dedupe (duplicate parcel analyzed once).
    # ------------------------------------------------------------------ #
    unique: list[tuple[str, dict[str, Any]]] = []
    duplicates: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, parcel in enumerate(parcels):
        key = _dedupe_key(parcel)
        if key in seen:
            duplicates.append({"index": index, "duplicate_of": seen[key], "key": key})
            continue
        seen[key] = index
        unique.append((key, parcel))

    # ------------------------------------------------------------------ #
    # 2+3) Sequential fan-out with backpressure; one bad parcel ≠ batch failure.
    # ------------------------------------------------------------------ #
    items: list[dict[str, Any]] = []
    for position, (key, parcel) in enumerate(unique):
        if position > 0 and delay_s > 0:
            await asyncio.sleep(delay_s)  # backpressure between fetch bursts (NFR-PERF-014)
        if status_callback is not None:
            # Review m7: status streaming must never abort the batch — the same
            # guard as TaskGraph._emit (one broken observer ≠ a dead portfolio).
            try:
                status_callback(
                    {
                        "batch_id": batch_id,
                        "state": "analyzing",
                        "parcel": parcel.get("parcel_id"),
                        "position": position,
                        "total": len(unique),
                    }
                )
            except Exception:
                _log.warning(
                    "portfolio_status_callback_failed",
                    batch_id=batch_id,
                    parcel=parcel.get("parcel_id"),
                )
        item: dict[str, Any] = {
            "index": seen[key],
            "input": parcel,
            "parcel_id": parcel.get("parcel_id"),
        }
        try:
            payload = AnalysisInput.model_validate(
                {"input": parcel, "analysis_mode": "quick_screening"}
            )
            result = await run_quick_screening(
                payload, connectors=connectors, ruleset_dir=ruleset_dir
            )
            result_store.put(result)
            red_flags = [
                {"risk_type": r.risk_type.value, "severity": r.severity.value, "summary": r.summary}
                for r in result.risks
            ]
            item.update(
                {
                    "status": result.status.value,
                    "analysis_id": result.analysis_id,
                    "decision": result.decision.value,
                    "decision_rank": _DECISION_RANK.get(result.decision.value, _FAILED_RANK),
                    "scores": {
                        "buildability": result.scores.buildability,
                        "data_confidence": result.scores.data_confidence,
                    },
                    "red_flags": red_flags,
                    "unknowns": len(result.unknowns),
                    "needs_manual_review": result.decision.value == "NEEDS_MANUAL_REVIEW",
                    "geometry": result.parcel.geometry if result.parcel else None,
                    "area_m2": result.parcel.area_m2 if result.parcel else None,
                }
            )
        except Exception as exc:
            # NFR-REL-008: the failed parcel is MARKED, the batch continues.
            _log.error(
                "portfolio_parcel_failed",
                batch_id=batch_id,
                parcel=parcel.get("parcel_id"),
                error=str(exc),
            )
            item.update(
                {
                    "status": "failed",
                    "analysis_id": None,
                    "decision": None,
                    "decision_rank": _FAILED_RANK,
                    "scores": {},
                    "red_flags": [],
                    "unknowns": 0,
                    "needs_manual_review": True,  # a failed parcel needs human eyes
                    "error": f"{type(exc).__name__}: {exc}",
                    "geometry": None,
                    "area_m2": None,
                }
            )
        items.append(item)

    # ------------------------------------------------------------------ #
    # 4) Ranking + red-flag table + manual-review list (§4.4).
    # ------------------------------------------------------------------ #
    ranked = sorted(items, key=_item_sort_key)
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    red_flag_table = [
        {
            "rank": item["rank"],
            "parcel_id": item.get("parcel_id"),
            "decision": item.get("decision"),
            "red_flags": item.get("red_flags", []),
        }
        for item in ranked
        if item.get("red_flags") or item.get("status") == "failed"
    ]
    manual_review = [
        item.get("parcel_id") or f"index:{item['index']}"
        for item in ranked
        if item.get("needs_manual_review")
    ]

    # ------------------------------------------------------------------ #
    # 5) Export artifacts: CSV + JSON + GeoJSON (F-0419); GPKG documented gap.
    # ------------------------------------------------------------------ #
    artifacts = _export_artifacts(batch_id, ranked, artifact_store=artifact_store)

    failed_count = sum(1 for item in items if item.get("status") == "failed")
    return {
        "batch_id": batch_id,
        "submitted": len(parcels),
        "deduplicated": len(duplicates),
        "duplicates": duplicates,
        "analyzed": len(items) - failed_count,
        "failed": failed_count,
        "items": ranked,
        "ranking": [item.get("parcel_id") or f"index:{item['index']}" for item in ranked],
        "red_flag_table": red_flag_table,
        "manual_review_required": manual_review,
        "artifacts": artifacts,
        "status": "complete",
        "note": (
            "Batch §4.4: deduplikacja + sekwencyjny fan-out z backpressure "
            "(NFR-PERF-014); awaria jednej działki nie przerywa portfela "
            "(NFR-REL-008); ranking wg decyzji i score; eksport CSV/JSON/GeoJSON "
            "(F-0419, GPKG przez konwersję z GeoJSON — udokumentowana luka)."
        ),
    }


def _csv_safe(value: Any) -> Any:
    """Neutralize CSV formula injection (review m4; OWASP CSV injection).

    A cell beginning with ``= + - @`` would execute as a formula when the
    exported ranking is opened in a spreadsheet — parcel ids/errors come from
    external inputs, so such cells are prefixed with ``'`` (the standard
    spreadsheet escape). Non-strings (ranks, scores) pass through unchanged.
    """
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _export_artifacts(
    batch_id: str, ranked: list[dict[str, Any]], *, artifact_store: Any | None = None
) -> dict[str, Any]:
    """Write the ranking table as CSV/JSON/GeoJSON artifacts (F-0419)."""
    from plot_reports import get_artifact_store

    store = artifact_store if artifact_store is not None else get_artifact_store()
    columns = [
        "rank",
        "parcel_id",
        "analysis_id",
        "status",
        "decision",
        "buildability",
        "data_confidence",
        "red_flag_count",
        "unknowns",
        "needs_manual_review",
        "error",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    rows = []
    for item in ranked:
        scores = item.get("scores") or {}
        row = {
            "rank": item.get("rank"),
            "parcel_id": item.get("parcel_id"),
            "analysis_id": item.get("analysis_id"),
            "status": item.get("status"),
            "decision": item.get("decision"),
            "buildability": scores.get("buildability"),
            "data_confidence": scores.get("data_confidence"),
            "red_flag_count": len(item.get("red_flags") or []),
            "unknowns": item.get("unknowns"),
            "needs_manual_review": item.get("needs_manual_review"),
            "error": item.get("error"),
        }
        rows.append(row)  # JSON keeps raw values; only the CSV cells are escaped
        writer.writerow({k: _csv_safe(v) for k, v in row.items()})

    csv_uri = store.put(
        f"portfolio/{batch_id}/ranking.csv", buffer.getvalue().encode("utf-8"), "text/csv"
    )
    json_uri = store.put(
        f"portfolio/{batch_id}/ranking.json",
        json.dumps({"batch_id": batch_id, "rows": rows}, indent=2, default=str).encode("utf-8"),
        "application/json",
    )
    features = [
        {
            "type": "Feature",
            "geometry": item.get("geometry"),
            "properties": {
                "rank": item.get("rank"),
                "parcel_id": item.get("parcel_id"),
                "decision": item.get("decision"),
                "status": item.get("status"),
                "area_m2": item.get("area_m2"),
            },
        }
        for item in ranked
        if item.get("geometry") is not None
    ]
    geojson_uri = store.put(
        f"portfolio/{batch_id}/portfolio.geojson",
        json.dumps(
            {"type": "FeatureCollection", "features": features, "crs_note": "EPSG:2180"},
            default=str,
        ).encode("utf-8"),
        "application/geo+json",
    )
    return {
        "csv": csv_uri,
        "json": json_uri,
        "geojson": geojson_uri,
        # Documented decision (plan Phase 13): GPKG via conversion from the
        # GeoJSON artifact — the heavy geopandas/fiona write stack stays out of
        # the batch path; the gap is visible, not silent.
        "gpkg": None,
        "gpkg_note": "Konwersja z portfolio.geojson (ogr2ogr/geopandas) — nie generowany w batchu.",
    }
