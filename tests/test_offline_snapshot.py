"""Offline-snapshot mode verification (Phase 16; §20.13/§20.14, v1 13.1.3).

The Phase 6 offline mechanisms, tested E2E with the network mocked OFF:

1. **snapshot replay** — a live fetch stores the raw body content-addressed in
   the ArtifactStore (``snapshots/<source_id>/<sha256>``); later, with ZERO
   network available (respx with no routes → any request would error), the
   stored bytes re-normalize into the SAME parcel payload/evidence — the
   connector "can work offline from it" (§20.14);
2. **offline capabilities** — the OGC connectors introspect layers from
   recorded GetCapabilities XML without any request;
3. **byte-exactness** — the snapshot is the verbatim body (sha256-addressed),
   so a re-fetch of identical content maps to the SAME artifact key (no
   duplicate snapshots, F-0090 versioning by content).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from plot_connectors import ParcelByIdQuery, ResultStatus
from plot_connectors.base.models import RawResult
from plot_reports import LocalArtifactStore
from tests.mocks import respx_uldk

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"
ULDK_URL = "https://uldk.gugik.gov.pl/"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_snapshot_replay_works_with_network_off(tmp_path: Path) -> None:
    body = (FIXTURES / "uldk_get_parcel_by_id.txt").read_text()
    connector = respx_uldk(tmp_path)
    store = LocalArtifactStore(tmp_path / "artifacts")

    # --- phase 1: one live (mocked) fetch stores the snapshot ---------------- #
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(ULDK_URL).mock(return_value=httpx.Response(200, text=body))
        online = await connector.resolve(ParcelByIdQuery(parcel_id="141201_1.0001.1867/2"))
    assert online.status is ResultStatus.OK
    assert online.snapshot_uri is not None

    digest = hashlib.sha256(body.encode()).hexdigest()
    key = f"snapshots/{connector.source_id}/{digest}"
    snapshot_bytes = store.get(key)
    assert snapshot_bytes == body.encode(), "snapshot must be the verbatim body"

    # --- phase 2: NETWORK OFF — replay normalizes the stored snapshot -------- #
    with respx.mock(assert_all_mocked=True):  # no routes: ANY request would raise
        replayed = connector.normalize(
            RawResult(
                source_id=connector.source_id,
                url=f"artifact://{key}",
                status_code=200,
                content=snapshot_bytes,
                content_type="text/plain",
                request_params={"replayed_from_snapshot": "true"},
                fetched_at=datetime.now(UTC).isoformat(),
            )
        )
    assert replayed.status is ResultStatus.OK
    assert replayed.payload["parcel"]["teryt"] == online.payload["parcel"]["teryt"]
    assert (
        replayed.payload["parcel"]["geometry_wkt"]
        == online.payload["parcel"]["geometry_wkt"]
    )
    assert {e.claim for e in replayed.evidence} == {e.claim for e in online.evidence}


@pytest.mark.anyio
async def test_snapshot_is_content_addressed_no_duplicates(tmp_path: Path) -> None:
    body = (FIXTURES / "uldk_get_parcel_by_id.txt").read_text()
    connector = respx_uldk(tmp_path)
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(ULDK_URL).mock(return_value=httpx.Response(200, text=body))
        first = await connector.resolve(ParcelByIdQuery(parcel_id="141201_1.0001.1867/2"))
        second = await connector.resolve(ParcelByIdQuery(parcel_id="141201_1.0001.1867/2"))
    # Identical content → identical content-addressed snapshot URI (F-0090).
    assert first.snapshot_uri == second.snapshot_uri


def test_wfs_capabilities_introspection_is_offline(tmp_path: Path) -> None:
    """Layer discovery from recorded GetCapabilities XML — zero requests."""
    from plot_connectors import WFSConnector
    from plot_connectors.profiles import get_profile
    from tests.mocks import allowlist_no_dns

    caps_xml = (FIXTURES / "wfs_capabilities.xml").read_bytes()
    connector = WFSConnector(
        get_profile("pl.gdos.wfs.crfop"),
        capabilities_xml=caps_xml,
        allowlist=allowlist_no_dns(),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
    )
    with respx.mock(assert_all_mocked=True):  # no routes: any request would raise
        layers = connector.discover_layers()
    assert layers, "capabilities XML must yield layers without network"
