"""CLI + Python SDK tests (Phase 14B; F-0463–0466) — no subprocess, no socket.

The SDK client gets the starlette ``TestClient`` (an ``httpx.Client`` subclass
over an in-process transport) injected — the mocked-transport contract; the CLI
``main`` is invoked directly with that injected client.
"""

from __future__ import annotations

import httpx
import pytest
from plot_shared import PlotAnalyzerClient
from tests.api_helpers import ANALYST_KEY, build_api


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> PlotAnalyzerClient:
    harness = build_api(monkeypatch)
    client = PlotAnalyzerClient(
        base_url="http://testserver", api_key=ANALYST_KEY, client=harness.client
    )
    yield client
    from plot_mcp_server import usecases

    usecases.set_connectors(None)
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# SDK (PlotAnalyzerClient)
# --------------------------------------------------------------------------- #
def test_sdk_analyze_report_roundtrip(sdk: PlotAnalyzerClient) -> None:
    assert sdk.healthz()["status"] == "ok"

    result = sdk.analyze({"parcel_id": "141201_1.0001.1867/2"})
    analysis_id = result["analysis_id"]
    assert result["buildable_envelope"]["area_m2"] > 0

    assert sdk.get_status(analysis_id)["progress"] == 1.0
    assert sdk.get_result(analysis_id)["analysis_id"] == analysis_id
    assert "risks" in sdk.get_risks(analysis_id)

    report = sdk.report(analysis_id, format="md")
    assert report["status"] == "rendered"

    export = sdk.export(analysis_id, format="geojson")
    assert export["status"] == "exported"

    envelope = sdk.get_buildable_envelope(analysis_id)
    assert envelope["type"] == "FeatureCollection"


def test_sdk_raises_on_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = build_api(monkeypatch)
    client = PlotAnalyzerClient(
        base_url="http://testserver", api_key=None, client=harness.client
    )
    with pytest.raises(httpx.HTTPStatusError) as err:
        client.rulesets()
    assert err.value.response.status_code == 401


# --------------------------------------------------------------------------- #
# CLI (plot-analyzer) — direct main() with the injected client
# --------------------------------------------------------------------------- #
def test_cli_analyze_then_report(sdk: PlotAnalyzerClient, capsys: pytest.CaptureFixture) -> None:
    from plot_api.cli import main

    code = main(["analyze", "141201_1.0001.1867/2"], client=sdk)
    assert code == 0
    out = capsys.readouterr().out
    assert "analysis_id:" in out and "decision:" in out
    analysis_id = next(
        line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("analysis_id:")
    )

    code = main(["report", analysis_id, "--format", "md"], client=sdk)
    assert code == 0
    assert "# " in capsys.readouterr().out  # raw Markdown printed

    code = main(["status", analysis_id], client=sdk)
    assert code == 0
    assert "progress:" in capsys.readouterr().out


def test_cli_health_and_resolve(sdk: PlotAnalyzerClient, capsys: pytest.CaptureFixture) -> None:
    from plot_api.cli import main

    assert main(["health"], client=sdk) == 0
    assert "status: ok" in capsys.readouterr().out

    assert main(["resolve", "141201_1.0001.1867/2"], client=sdk) == 0
    assert "teryt:" in capsys.readouterr().out


def test_cli_api_error_is_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from plot_api.cli import main

    harness = build_api(monkeypatch)
    bad = PlotAnalyzerClient(
        base_url="http://testserver", api_key="wrong-key", client=harness.client
    )
    assert main(["analyze", "x"], client=bad) == 1
    assert "API error 401" in capsys.readouterr().err
