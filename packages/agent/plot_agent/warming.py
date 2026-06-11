"""Cache warming (Phase 13 / v1 Phase 11 §11.1.3; F-0087/0088, NFR-PERF-011/014).

``warm_cache`` pre-fetches the configured connector layers for a scope into the
EXISTING connector caches (Phase 6: each connector's :class:`plot_connectors
.base.cache.Cache` + capabilities cache; the fetched layers also seed the
process-shared connector bundle the analyses reuse). Backpressure is explicit:
themes are fetched SEQUENTIALLY with a configurable delay between requests so
public services are never hammered (NFR-PERF-014 — v1 §11.4 anti-pattern guard).
"""

from __future__ import annotations

import asyncio
from typing import Any

from plot_connectors import BBox, ParcelByIdQuery, ResultStatus, SourceUnavailable
from plot_shared import get_logger

from plot_agent.analysis import Connectors
from plot_agent.analysis.quick_screening import MVP_RISK_THEMES, _parcel_geometry

_log = get_logger(__name__)

_BBOX_PAD_M = 50.0


async def warm_cache(
    scope: str,
    target_id: str | None,
    *,
    connectors: Connectors,
    bbox: BBox | None = None,
    themes: tuple[Any, ...] = MVP_RISK_THEMES,
    delay_s: float = 0.0,
) -> dict[str, Any]:
    """Pre-fetch configured connector layers for a scope (sequential + delayed).

    * ``scope == "parcel"`` — the parcel is resolved (ULDK) and its padded bbox
      drives one fetch per configured risk theme (the same fetch path the
      analyses use, so their caches are the ones warmed);
    * ``scope == "municipality"`` — requires an explicit ``bbox`` (municipal
      boundary geometry is not a wired source yet — the gap is reported
      honestly, never warmed against a guessed extent).
    """
    if bbox is None and scope == "parcel" and target_id:
        try:
            norm = await connectors.uldk.resolve(ParcelByIdQuery(parcel_id=target_id), snapshot=False)
        except SourceUnavailable as exc:
            return {
                "scope": scope,
                "target_id": target_id,
                "warmed": [],
                "warmed_count": 0,
                "status": "source_unavailable",
                "note": f"ULDK niedostępny — nie można wyznaczyć bbox działki: {exc}",
            }
        geom = None
        if norm.status is ResultStatus.OK and "parcel" in norm.payload:
            geom = _parcel_geometry(norm.payload["parcel"])
        if geom is None:
            return {
                "scope": scope,
                "target_id": target_id,
                "warmed": [],
                "warmed_count": 0,
                "status": "parcel_not_resolved",
                "note": "Działka nierozwiązana — brak bbox do nagrzania cache (§21).",
            }
        minx, miny, maxx, maxy = geom.bounds
        bbox = BBox(
            minx=minx - _BBOX_PAD_M,
            miny=miny - _BBOX_PAD_M,
            maxx=maxx + _BBOX_PAD_M,
            maxy=maxy + _BBOX_PAD_M,
            crs="EPSG:2180",
        )
    if bbox is None:
        return {
            "scope": scope,
            "target_id": target_id,
            "warmed": [],
            "warmed_count": 0,
            "status": "bbox_required",
            "note": (
                "Scope bez geometrii (gmina) wymaga jawnego bbox — granice gmin "
                "nie są jeszcze podłączonym źródłem; nic nie jest zgadywane (§21)."
            ),
        }

    warmed: list[dict[str, Any]] = []
    for position, kind in enumerate(themes):
        if position > 0 and delay_s > 0:
            # Sequential warm with delay — backpressure (NFR-PERF-014).
            await asyncio.sleep(delay_s)
        fetch = await connectors.risk_layers.fetch(kind, bbox)
        warmed.append({"theme": kind.value, "status": fetch.status.value, "source_id": fetch.source_id})
    ok_count = sum(1 for w in warmed if w["status"] != "source_unavailable")
    _log.info("cache_warmed", scope=scope, target_id=target_id, themes=len(warmed), ok=ok_count)
    return {
        "scope": scope,
        "target_id": target_id,
        "bbox": bbox.to_param(),
        "warmed": warmed,
        "warmed_count": ok_count,
        "delay_s": delay_s,
        "status": "warmed",
        "note": (
            "Warstwy pobrane sekwencyjnie z opóźnieniem (backpressure, "
            "NFR-PERF-014) przez te same connectory/cache, których używa analiza "
            "(F-0087/0088, NFR-PERF-011)."
        ),
    }
