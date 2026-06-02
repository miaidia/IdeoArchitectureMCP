"""Tests for the ArtifactStore + style sidecar (Phase 3 §3.1 deliverable 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from plot_reports import (
    Layer,
    LayerRole,
    LocalArtifactStore,
    get_artifact_store,
    render_map,
)
from shapely.geometry import Polygon


def test_local_put_get_roundtrip(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    uri = store.put("maps/a.png", b"\x89PNG_data", "image/png")
    assert uri.startswith("file://")
    assert store.get("maps/a.png") == b"\x89PNG_data"


def test_put_render_writes_image_and_style_sidecar(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    parcel = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    result = render_map([Layer(name="P", geometries=parcel, role=LayerRole.PARCEL)], fmt="png")

    uri = store.put_render("analysis/x/map.png", result)
    assert uri.startswith("file://")

    # Image bytes are stored, plus a <name>.style.json sidecar (NFR-AUD-009).
    assert (tmp_path / "analysis" / "x" / "map.png").exists()
    sidecar = tmp_path / "analysis" / "x" / "map.png.style.json"
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert meta["crs"] == "EPSG:2180"
    assert meta["renderer_version"]
    assert store.get_style_metadata("analysis/x/map.png")["crs"] == "EPSG:2180"


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("../escape.png", b"x", "image/png")


def test_get_artifact_store_defaults_to_filesystem(tmp_path: Path) -> None:
    # Filesystem is the default backend so tests / dev need no MinIO (Phase 3 §3.1).
    store = get_artifact_store(base_dir=tmp_path)
    assert isinstance(store, LocalArtifactStore)
