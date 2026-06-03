"""Report rendering tests (Phase 7 §7.1.D / §22): MD+JSON stable, numbers match, PNG valid.

ZERO network. Builds a deterministic AnalysisResult via the orchestrator over the pure
mock bundle, then asserts the Markdown report is stable (snapshot) and its headline
numbers equal the JSON, and that the buildable-envelope PNG (Phase 3 renderer) is valid.
"""

from __future__ import annotations

import pytest
from plot_agent.analysis import run_quick_screening
from plot_domain import AnalysisInput
from plot_envelope import RiskKind
from plot_reports import headline_numbers, render_envelope_map, render_json, render_markdown
from tests.mocks import mock_connectors

# A fixed 40x30 parcel (1200 m²) with a flood feature on the left → deterministic numbers.
PARCEL_WKT = "POLYGON((0 0,40 0,40 30,0 30,0 0))"
FLOOD_GEOM = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [15, 0], [15, 30], [0, 30], [0, 0]]],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _result():
    connectors = mock_connectors(parcel_wkt=PARCEL_WKT, feature_geoms={RiskKind.FLOOD: [FLOOD_GEOM]})
    inp = AnalysisInput.model_validate(
        {"input": {"parcel_id": "141201_1.0001.1867/2"}, "analysis_mode": "quick_screening"}
    )
    return await run_quick_screening(inp, connectors=connectors, analysis_id="a-report")


@pytest.mark.anyio
async def test_markdown_numbers_match_json() -> None:
    result = await _result()
    md = render_markdown(result)
    js = render_json(result)
    h = headline_numbers(result)

    # headline numbers are the single source of truth for both renders (§7.3).
    assert js["decision"] == h["decision"]
    assert js["buildable_envelope"]["area_m2"] == h["buildable_area_m2"]
    assert js["parcel"]["area_m2"] == h["parcel_area_m2"]
    # the MD text contains the SAME formatted numbers as the JSON.
    assert f"{h['parcel_area_m2']:,.0f} m" in md
    assert f"{h['buildable_area_m2']:,.0f} m" in md
    assert h["decision"] in md
    # every §22 section heading is present.
    for heading in (
        "## Decyzja screeningowa",
        "## Najważniejsze wnioski",
        "## Czerwone flagi",
        "## Buildable envelope",
        "## Następne kroki",
        "## Źródła i confidence",
    ):
        assert heading in md, f"missing section: {heading}"


@pytest.mark.anyio
async def test_markdown_snapshot_stable() -> None:
    """The MD render is deterministic except for the (random) analysis/source ids."""
    result = await _result()
    md1 = render_markdown(result)
    md2 = render_markdown(result)
    assert md1 == md2  # stable for the same result object

    # Decision + headline-number lines are fixed for these deterministic inputs.
    assert "LIKELY_BLOCKED" in md1  # flood (hard) on the parcel dominates
    assert "1,200 m²" in md1  # parcel area
    # buildable area: 1200 − flood(450) − setback ring outside flood ≈ 528 m²
    assert "528 m²" in md1


@pytest.mark.anyio
async def test_envelope_png_is_valid(tmp_path) -> None:
    result = await _result()
    render = render_envelope_map(result, fmt="png")
    assert render.mime_type == "image/png"
    # PNG magic number.
    assert render.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(render.data) > 1000
    # style metadata records CRS + the layers drawn (NFR-AUD-009).
    assert render.style_metadata["crs"] == "EPSG:2180"
    roles = {layer["role"] for layer in render.style_metadata["layers"]}
    assert "parcel" in roles and "buildable_envelope" in roles


@pytest.mark.anyio
async def test_self_improve_screenshot_is_decodable_png(tmp_path) -> None:
    """Phase 4 tie-in (evidence #8): the quick_screening result → render_map → a PNG the
    model can VISUALLY verify. Persist it and decode it with Pillow to prove it is valid."""
    from PIL import Image

    result = await _result()
    render = render_envelope_map(result, fmt="png", title="MVP buildable envelope")
    out = tmp_path / "buildable-envelope.png"
    out.write_bytes(render.data)

    with Image.open(out) as img:
        img.verify()  # raises if the PNG is corrupt
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.size[0] > 0 and img.size[1] > 0
