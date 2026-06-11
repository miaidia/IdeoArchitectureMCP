"""Phase 9 renderer-v2 golden tests (IMPLEMENTATION_PLAN_V2 §9.3; same mechanism as
``test_render_golden.py``).

Covers:
  * golden-image PASS: the masterplan render (status hatching + floor labels + legend +
    north arrow + scale bar + stage-table panel) matches the committed reference within
    the pixel tolerance; ``UPDATE_GOLDEN=1`` regenerates it;
  * byte determinism: same input → identical bytes (no randomness);
  * "bites": perturbing a FLOOR COUNT changes the snapshot bytes/hash (the floor label
    is part of the contract);
  * style metadata (NFR-AUD-009): masterplan renderer version, roles, annotations,
    CRS and table-row count.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage
from plot_agent.drawing import MasterplanProposal
from plot_reports import render_masterplan
from shapely.geometry import Polygon, mapping

GOLDEN_DIR = Path(__file__).resolve().parent / "golden-parcels" / "render"
GOLDEN_PATH = GOLDEN_DIR / "masterplan_v2.png"
TOLERANCE = 3.0  # mean-abs pixel diff, as in test_render_golden.py

PARCEL = Polygon([(0, 0), (100, 0), (100, 80), (0, 80)])
ENVELOPE = Polygon([(8, 8), (92, 8), (92, 72), (8, 72)])

# Hand-written stage table (the renderer consumes the rows; capacity arithmetic is
# covered in test_capacity.py — keeping the golden input fully static).
STAGE_TABLE = [
    {"etap": 1, "liczba_mieszkan": 61, "pum_m2": 3192.0, "puu_m2": 416.0, "pu_m2": 3608.0},
    {"etap": 2, "liczba_mieszkan": 0, "pum_m2": 0.0, "puu_m2": 320.0, "pu_m2": 320.0},
    {"etap": "SUMA", "liczba_mieszkan": 61, "pum_m2": 3192.0, "puu_m2": 736.0, "pu_m2": 3928.0},
]


def _rect(x: float, y: float, w: float, h: float) -> dict:
    return mapping(Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)]))


def _proposal(*, first_building_floors: int = 4) -> MasterplanProposal:
    """All four statuses + L-shape + roads/parking/greenery/playground/retention."""
    return MasterplanProposal.model_validate(
        {
            "buildings": [
                {
                    "name": "Budynek 1",
                    "stage": 1,
                    "segments": [
                        {"rectangles": [{"x": 12, "y": 12, "w": 24, "h": 8}],
                         "floors": first_building_floors, "use": "mieszkalny"},
                        {"rectangles": [{"x": 12, "y": 20, "w": 8, "h": 14}],
                         "floors": 7, "use": "mieszkalny"},
                    ],
                },
                {"name": "Budynek 2", "stage": 1, "status": "w_budowie",
                 "segments": [{"polygon": _rect(44, 12, 16, 10), "floors": 5,
                               "use": "mieszkalny"}]},
                {"name": "Budynek 3", "stage": 2, "status": "zrealizowany",
                 "segments": [{"polygon": _rect(68, 12, 16, 10), "floors": 3,
                               "use": "mieszkalny"}]},
                {"name": "Zabytek", "status": "zabytek_do_remontu",
                 "segments": [{"polygon": _rect(68, 50, 18, 14), "floors": 2,
                               "use": "uslugowy"}]},
            ],
            "roads": [
                {"centerline": {"type": "LineString", "coordinates": [[8, 40], [92, 40]]},
                 "width_m": 5.0, "function": "kdw"}
            ],
            "parking": [
                {"kind": "naziemny", "polygon": _rect(44, 26, 16, 8), "spaces": 20},
                {"kind": "hala_podziemna", "polygon": _rect(12, 12, 30, 22), "spaces": 80},
            ],
            "greenery_polygons": [_rect(12, 50, 30, 18)],
            "playgrounds": [_rect(48, 50, 14, 12)],
            "retention": [_rect(48, 66, 10, 5)],
        }
    )


def _render(*, first_building_floors: int = 4) -> bytes:
    return render_masterplan(
        _proposal(first_building_floors=first_building_floors),
        PARCEL,
        metrics={"stage_table": STAGE_TABLE},
        envelope=ENVELOPE,
        title="Masterplan golden",
    ).data


def _png_to_array(data: bytes) -> np.ndarray:
    with PILImage.open(io.BytesIO(data)) as img:
        return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _mean_abs_diff(a: bytes, b: bytes) -> float:
    arr_a = _png_to_array(a).astype(np.float64)
    arr_b = _png_to_array(b).astype(np.float64)
    if arr_a.shape != arr_b.shape:
        return float("inf")
    return float(np.mean(np.abs(arr_a - arr_b)))


@pytest.fixture(scope="session", autouse=True)
def _maybe_regenerate_golden() -> None:
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_bytes(_render())


def test_masterplan_golden_matches_reference() -> None:
    assert GOLDEN_PATH.exists(), (
        f"missing golden reference {GOLDEN_PATH}; run UPDATE_GOLDEN=1 pytest to create it"
    )
    diff = _mean_abs_diff(_render(), GOLDEN_PATH.read_bytes())
    assert diff <= TOLERANCE, f"mean-abs-diff {diff:.3f} exceeds tolerance {TOLERANCE}"


def test_masterplan_render_is_byte_deterministic() -> None:
    assert _render() == _render()


def test_perturbed_floor_count_changes_snapshot() -> None:
    """"Bites" test: floor label 4 → 6 must change the rendered bytes/hash."""
    baseline = _render(first_building_floors=4)
    perturbed = _render(first_building_floors=6)
    assert hashlib.sha256(baseline).hexdigest() != hashlib.sha256(perturbed).hexdigest()
    # And it must no longer be byte-identical to the committed reference.
    assert perturbed != GOLDEN_PATH.read_bytes()


def test_masterplan_style_metadata_contract() -> None:
    result = render_masterplan(
        _proposal(), PARCEL, metrics={"stage_table": STAGE_TABLE}, envelope=ENVELOPE
    )
    meta = result.style_metadata
    assert meta["crs"] == "EPSG:2180"
    assert meta["masterplan_renderer_version"]
    assert meta["table_rows"] == 3
    roles = {layer["role"] for layer in meta["layers"]}
    # Building-by-status roles + the masterplan layer kinds are all present.
    assert {
        "building_planned", "building_under_construction", "building_completed",
        "building_existing", "road", "parking", "playground", "greenery", "retention",
    } <= roles
    floor_texts = [a["text"] for a in meta["annotations"] if a["kind"] == "floors"]
    assert {"4", "7", "5", "3", "2"} <= set(floor_texts)
    names = [a["text"] for a in meta["annotations"] if a["kind"] == "name"]
    assert "Zabytek" in names
    # Status hatching is part of the recorded deterministic style (NFR-AUD-009).
    existing = next(
        layer for layer in meta["layers"] if layer["role"] == "building_existing"
        and layer["name"] == "Budynki istniejące"
    ) if any(
        layer["role"] == "building_existing" and layer["name"] == "Budynki istniejące"
        for layer in meta["layers"]
    ) else None
    zabytek = next(layer for layer in meta["layers"] if layer["name"] == "Zabytek do remontu")
    assert zabytek["style"]["edgecolor"] == "#7b1fa2"  # distinct heritage edge
    assert zabytek["style"]["hatch"] == "//"
    assert existing is None or existing["style"]["hatch"] == "//"


def test_variant_rerender_includes_roads_and_retention() -> None:
    """F3 regression: rendering from a STORED MasterplanVariant must include the road
    corridors (stored as ``RoadElement.model_dump()`` dicts keyed ``centerline``, NOT
    ``geometry``) and the persisted retention polygons — same layers as the live
    proposal path."""
    from plot_domain import BuildingRecord, MasterplanVariant
    from plot_reports.render.masterplan import masterplan_layers

    variant = MasterplanVariant(
        id="mvar:test",
        buildings=[
            BuildingRecord(
                id="b1", name="Budynek 1", geometry=_rect(12, 12, 24, 8),
                floors_by_segment=[4], uses=["mieszkalny"],
            )
        ],
        # Exactly what usecases persists: RoadElement.model_dump(mode="json").
        roads=[{"centerline": {"type": "LineString", "coordinates": [[8, 40], [92, 40]]},
                "width_m": 5.0, "function": "kdw"}],
        retention=[_rect(48, 66, 10, 5)],
    )
    layers, _ = masterplan_layers(variant, PARCEL)
    by_name = {layer.name: layer for layer in layers}
    assert "Drogi wewnętrzne" in by_name, "variant roads (centerline dicts) must render"
    assert "Retencja" in by_name, "persisted retention must render on the variant path"
    # Corridor = centerline buffered by width/2 with flat caps: 84 m × 5 m = 420 m².
    road_geom = by_name["Drogi wewnętrzne"].shapely_geometries()[0]
    assert float(road_geom.area) == pytest.approx(84.0 * 5.0)
    # Full re-render from the variant produces a PNG (smoke).
    result = render_masterplan(variant, PARCEL)
    assert result.data[:8] == b"\x89PNG\r\n\x1a\n"
    roles = {layer["role"] for layer in result.style_metadata["layers"]}
    assert {"road", "retention"} <= roles


def test_masterplan_svg_is_valid() -> None:
    result = render_masterplan(
        _proposal(), PARCEL, metrics={"stage_table": STAGE_TABLE}, envelope=ENVELOPE, fmt="svg"
    )
    head = result.data[:200].lstrip()
    assert head.startswith(b"<?xml") or head.startswith(b"<svg")
    assert result.mime_type == "image/svg+xml"
