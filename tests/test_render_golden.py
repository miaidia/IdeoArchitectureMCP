"""Golden-image snapshot tests for the deterministic map renderer (Phase 3 §3.3).

Three golden parcels (simple rectangle, narrow/long, corner/irregular) are rendered
to PNG with ``basemap=False`` (no network, deterministic) and compared to committed
reference PNGs under ``tests/golden-parcels/render/`` using a Pillow + numpy mean
absolute pixel-difference tolerance.

Covered:
  * PASS test: render == reference within tolerance.
  * "bites" test: a perturbed geometry produces a diff that EXCEEDS the tolerance,
    proving the snapshot test is real (Phase 3 §3.3).
  * SVG validity + ``style_metadata.crs == "EPSG:2180"``.
  * ``UPDATE_GOLDEN=1`` regenerates the reference PNGs.

Determinism guarantees come from the renderer (Agg backend, fixed figsize/dpi/colours
/z-order — Phase 3 §3.4). The tolerance absorbs only tiny cross-version font/antialias
jitter, NOT geometry changes (which the bites test confirms it catches).
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage
from plot_reports import Layer, LayerRole, render_map
from shapely.geometry import Polygon

GOLDEN_DIR = Path(__file__).resolve().parent / "golden-parcels" / "render"

# Mean-abs-diff tolerance (0..255 scale). Small enough that any geometry change is
# caught (bites test asserts a perturbation exceeds it), large enough to absorb
# sub-pixel antialiasing jitter across matplotlib/freetype builds.
TOLERANCE = 3.0


def _simple_rectangle() -> list[Layer]:
    parcel = Polygon([(0, 0), (50, 0), (50, 40), (0, 40)])
    envelope = Polygon([(5, 5), (45, 5), (45, 35), (5, 35)])
    return [
        Layer(name="Parcel", geometries=parcel, role=LayerRole.PARCEL),
        Layer(name="Envelope", geometries=envelope, role=LayerRole.BUILDABLE_ENVELOPE),
    ]


def _narrow_long() -> list[Layer]:
    parcel = Polygon([(0, 0), (120, 0), (120, 12), (0, 12)])
    no_build = Polygon([(0, 0), (120, 0), (120, 4), (0, 4)])
    return [
        Layer(name="Parcel", geometries=parcel, role=LayerRole.PARCEL),
        Layer(
            name="Setback",
            geometries=no_build,
            role=LayerRole.NO_BUILD,
            removed_area_m2=480.0,
            removed_area_percent=33.3,
        ),
    ]


def _corner_irregular() -> list[Layer]:
    # An L-shaped corner parcel with a soft constraint patch.
    parcel = Polygon([(0, 0), (60, 0), (60, 30), (30, 30), (30, 50), (0, 50)])
    soft = Polygon([(40, 5), (58, 5), (58, 22), (40, 22)])
    return [
        Layer(name="Parcel", geometries=parcel, role=LayerRole.PARCEL),
        Layer(
            name="Flood buffer",
            geometries=soft,
            role=LayerRole.CONSTRAINT_SOFT,
            removed_area_m2=306.0,
            removed_area_percent=9.0,
        ),
    ]


GOLDEN_CASES = {
    "simple_rectangle": _simple_rectangle,
    "narrow_long": _narrow_long,
    "corner_irregular": _corner_irregular,
}


def _png_to_array(data: bytes) -> np.ndarray:
    """Decode PNG bytes to an RGB uint8 array."""
    with PILImage.open(io.BytesIO(data)) as img:
        return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _mean_abs_diff(a: bytes, b: bytes) -> float:
    """Mean absolute pixel difference between two PNGs (0..255). Inf on shape mismatch."""
    arr_a = _png_to_array(a).astype(np.float64)
    arr_b = _png_to_array(b).astype(np.float64)
    if arr_a.shape != arr_b.shape:
        return float("inf")
    return float(np.mean(np.abs(arr_a - arr_b)))


def _render(name: str, *, title: str | None = None) -> bytes:
    layers = GOLDEN_CASES[name]()
    return render_map(layers, fmt="png", basemap=False, title=title or name).data


def _update_golden() -> bool:
    return os.environ.get("UPDATE_GOLDEN") == "1"


@pytest.fixture(scope="session", autouse=True)
def _maybe_regenerate_goldens() -> None:
    """Regenerate every reference PNG when UPDATE_GOLDEN=1 (Phase 3 §3.3)."""
    if not _update_golden():
        return
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name in GOLDEN_CASES:
        (GOLDEN_DIR / f"{name}.png").write_bytes(_render(name))


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_golden_render_matches_reference(name: str) -> None:
    """PASS test: each render matches its committed reference within tolerance."""
    ref_path = GOLDEN_DIR / f"{name}.png"
    assert ref_path.exists(), (
        f"missing golden reference {ref_path}; run UPDATE_GOLDEN=1 pytest to create it"
    )
    reference = ref_path.read_bytes()
    rendered = _render(name)
    diff = _mean_abs_diff(rendered, reference)
    assert diff <= TOLERANCE, f"{name}: mean-abs-diff {diff:.3f} exceeds tolerance {TOLERANCE}"


def test_golden_render_is_byte_deterministic() -> None:
    """Re-rendering the same case twice yields identical bytes (Phase 3 §3.4)."""
    first = _render("simple_rectangle")
    second = _render("simple_rectangle")
    assert first == second


def test_perturbed_geometry_exceeds_tolerance() -> None:
    """"Bites" test: a perturbed parcel must EXCEED the tolerance vs the reference,
    proving the snapshot comparison actually detects geometry changes (Phase 3 §3.3)."""
    ref_path = GOLDEN_DIR / "simple_rectangle.png"
    assert ref_path.exists(), "run UPDATE_GOLDEN=1 pytest to create golden references first"
    reference = ref_path.read_bytes()

    # Perturb the parcel shape into a clearly different pentagon — same style, new
    # shape. (The renderer autoscales to the geometry bounds, so the perturbation must
    # change the *shape*, not merely the scale, to be a meaningful regression signal.)
    perturbed_parcel = Polygon([(0, 0), (50, 0), (60, 25), (25, 50), (-5, 25)])
    envelope = Polygon([(5, 5), (45, 5), (45, 35), (5, 35)])
    perturbed = render_map(
        [
            Layer(name="Parcel", geometries=perturbed_parcel, role=LayerRole.PARCEL),
            Layer(name="Envelope", geometries=envelope, role=LayerRole.BUILDABLE_ENVELOPE),
        ],
        fmt="png",
        basemap=False,
        title="simple_rectangle",
    ).data

    diff = _mean_abs_diff(perturbed, reference)
    assert diff > TOLERANCE, (
        f"perturbed geometry diff {diff:.3f} did NOT exceed tolerance {TOLERANCE} — "
        "the snapshot test would not catch a real regression"
    )


def test_svg_path_is_valid_and_crs_is_2180() -> None:
    """SVG render is valid XML and style metadata records the analytical CRS."""
    layers = GOLDEN_CASES["simple_rectangle"]()
    result = render_map(layers, fmt="svg", basemap=False)
    head = result.data[:200].lstrip()
    assert head.startswith(b"<?xml") or head.startswith(b"<svg"), "SVG must start with <?xml/<svg"
    assert result.mime_type == "image/svg+xml"
    assert result.style_metadata["crs"] == "EPSG:2180"


def test_style_metadata_contract() -> None:
    """Style metadata carries CRS, ordered layers (name+role), params, version (NFR-AUD-009)."""
    layers = GOLDEN_CASES["corner_irregular"]()
    meta = render_map(layers, fmt="png", basemap=False).style_metadata
    assert meta["crs"] == "EPSG:2180"
    assert meta["renderer_version"]
    assert "dpi" in meta and "figsize" in meta
    names_roles = [(layer["name"], layer["role"]) for layer in meta["layers"]]
    assert names_roles == [("Parcel", "parcel"), ("Flood buffer", "constraint_soft")]
    # Hard/soft distinction is recorded for the legend contract (§30).
    assert meta["layers"][1]["hard"] is False
