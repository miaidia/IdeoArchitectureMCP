"""Portfolio batch tests (Phase 13 / v1 Phase 11 §11.1.3; §4.4, F-0419, NFR-REL-008).

5 parcels including one whose ULDK resolution EXPLODES (an unexpected error, not
a clean SourceUnavailable): the batch completes, the failed parcel is marked,
duplicates are analyzed once, ranking + CSV/JSON/GeoJSON artifacts exist, and
the backpressure delay is applied between parcels (NFR-PERF-014).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest
from plot_agent.portfolio import run_portfolio_analysis
from plot_reports import LocalArtifactStore
from tests.mocks import MockULDKConnector, mock_connectors

BAD_ID = "141201_1.0001.BAD"

PARCELS = [
    {"parcel_id": "141201_1.0001.1867/2"},
    {"parcel_id": "141201_1.0001.1867/3"},
    {"parcel_id": BAD_ID},  # ULDK error fixture — must NOT stop the batch
    {"parcel_id": "141201_1.0001.1867/4"},
    {"parcel_id": "141201_1.0001.1867/2"},  # duplicate of the first → deduped
]


class ExplodingULDK(MockULDKConnector):
    """TEST FIXTURE: raises an UNEXPECTED error for one parcel id."""

    async def resolve(self, query: Any, snapshot: bool = False) -> Any:
        if getattr(query, "parcel_id", None) == BAD_ID:
            raise RuntimeError("TEST FIXTURE: ULDK parser exploded")
        return await super().resolve(query, snapshot)


def _read_artifact(uri: str) -> bytes:
    return Path(unquote(urlparse(uri).path)).read_bytes()


@pytest.fixture()
def connectors() -> Any:
    bundle = mock_connectors()
    bundle.uldk = ExplodingULDK()  # type: ignore[assignment]
    return bundle


def test_portfolio_batch_isolates_failure_dedupes_ranks_and_exports(
    connectors: Any, tmp_path: Path
) -> None:
    result = asyncio.run(
        run_portfolio_analysis(
            PARCELS,
            connectors=connectors,
            artifact_store=LocalArtifactStore(tmp_path),
        )
    )

    # Batch completed despite the bad parcel (NFR-REL-008).
    assert result["status"] == "complete"
    assert result["submitted"] == 5
    assert result["deduplicated"] == 1  # duplicate analyzed once
    assert result["analyzed"] == 3
    assert result["failed"] == 1

    items = {item["parcel_id"]: item for item in result["items"]}
    assert items[BAD_ID]["status"] == "failed"
    assert "RuntimeError" in items[BAD_ID]["error"]
    assert items[BAD_ID]["needs_manual_review"] is True
    # The failed parcel ranks LAST; good parcels carry analysis ids + scores.
    assert result["items"][-1]["parcel_id"] == BAD_ID
    for pid in ("141201_1.0001.1867/2", "141201_1.0001.1867/3", "141201_1.0001.1867/4"):
        assert items[pid]["analysis_id"]
        assert items[pid]["decision"] in ("OK", "OK_WITH_RISKS", "NEEDS_MANUAL_REVIEW")
    # Ranking is a permutation of the 4 unique parcels.
    assert len(result["ranking"]) == 4
    assert result["ranking"][-1] == BAD_ID

    # Red-flag table includes the failed parcel; manual-review list names it.
    assert any(row["parcel_id"] == BAD_ID for row in result["red_flag_table"])
    assert BAD_ID in result["manual_review_required"]

    # Artifacts (F-0419): CSV + JSON + GeoJSON written; GPKG documented gap.
    artifacts = result["artifacts"]
    rows = list(csv.DictReader(io.StringIO(_read_artifact(artifacts["csv"]).decode("utf-8"))))
    assert len(rows) == 4
    assert rows[-1]["parcel_id"] == BAD_ID and rows[-1]["status"] == "failed"
    table = json.loads(_read_artifact(artifacts["json"]))
    assert [r["rank"] for r in table["rows"]] == [1, 2, 3, 4]
    geojson = json.loads(_read_artifact(artifacts["geojson"]))
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 3  # the failed parcel has no geometry
    assert artifacts["gpkg"] is None and "geojson" in artifacts["gpkg_note"].lower()


def test_duplicate_parcel_analyzed_once(connectors: Any, tmp_path: Path) -> None:
    result = asyncio.run(
        run_portfolio_analysis(
            [{"parcel_id": "P1"}, {"parcel_id": "P1"}, {"parcel_id": "P1"}],
            connectors=connectors,
            artifact_store=LocalArtifactStore(tmp_path),
        )
    )
    assert result["submitted"] == 3
    assert result["deduplicated"] == 2
    assert len(result["items"]) == 1
    assert [d["duplicate_of"] for d in result["duplicates"]] == [0, 0]


def test_backpressure_delay_between_parcels(
    connectors: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-PERF-014: sequential fan-out sleeps between parcels when configured."""
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    asyncio.run(
        run_portfolio_analysis(
            [{"parcel_id": "P1"}, {"parcel_id": "P2"}, {"parcel_id": "P3"}],
            connectors=connectors,
            artifact_store=LocalArtifactStore(tmp_path),
            delay_s=0.25,
        )
    )
    assert sleeps == [0.25, 0.25]  # between parcels, not before the first


def test_csv_export_escapes_formula_injection(connectors: Any, tmp_path: Path) -> None:
    """Review m4 (OWASP CSV injection): a parcel id starting with '=' (or
    + - @) must not export as an executable spreadsheet formula — the CSV cell
    is prefixed with a quote; the JSON artifact keeps the raw value."""
    evil_id = '=HYPERLINK("http://evil.example/x";"klik")'
    result = asyncio.run(
        run_portfolio_analysis(
            [{"parcel_id": evil_id}, {"parcel_id": "141201_1.0001.1867/2"}],
            connectors=connectors,
            artifact_store=LocalArtifactStore(tmp_path),
        )
    )
    artifacts = result["artifacts"]
    rows = list(csv.DictReader(io.StringIO(_read_artifact(artifacts["csv"]).decode("utf-8"))))
    evil_rows = [r for r in rows if evil_id in r["parcel_id"]]
    assert evil_rows and evil_rows[0]["parcel_id"] == "'" + evil_id  # escaped
    assert not any(r["parcel_id"].startswith("=") for r in rows)
    # The JSON table is not a spreadsheet — raw values stay intact there.
    table = json.loads(_read_artifact(artifacts["json"]))
    assert any(r["parcel_id"] == evil_id for r in table["rows"])


def test_raising_status_callback_does_not_abort_batch(
    connectors: Any, tmp_path: Path
) -> None:
    """Review m7: a broken status observer must never kill the batch — the
    same guard as TaskGraph._emit."""
    calls = {"n": 0}

    def _bad_callback(event: dict[str, Any]) -> None:
        calls["n"] += 1
        raise RuntimeError("TEST FIXTURE: observer exploded")

    result = asyncio.run(
        run_portfolio_analysis(
            [{"parcel_id": "P1"}, {"parcel_id": "P2"}],
            connectors=connectors,
            artifact_store=LocalArtifactStore(tmp_path),
            status_callback=_bad_callback,
        )
    )
    assert calls["n"] == 2  # the callback WAS invoked per parcel…
    assert result["status"] == "complete"  # …and the batch still completed
    assert result["analyzed"] == 2 and result["failed"] == 0


def test_usecase_portfolio_analyze_runs_in_process_without_broker(
    connectors: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Graceful degradation: no broker configured → the full result inline."""
    from plot_mcp_server import usecases

    monkeypatch.setenv("PLOT_QUEUE_ENABLED", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    usecases.set_connectors(connectors)
    try:
        result = usecases.portfolio_analyze({"parcels": PARCELS})
    finally:
        usecases.set_connectors(None)
        cfg.get_settings.cache_clear()
    assert result["status"] == "complete"  # ran in-proc, not queued
    assert result["failed"] == 1
    assert result["artifacts"]["csv"]

    empty = usecases.portfolio_analyze({"parcels": []})
    assert empty["status"] == "empty"
