"""Artifact storage for rendered maps + style sidecars (Phase 3 §3.1 deliverable 2).

Writes image bytes (PNG/SVG) and a ``<name>.style.json`` sidecar (NFR-AUD-009). The
**default backend is the local filesystem** under a configurable artifacts directory,
so tests need no MinIO. An optional S3/MinIO backend uses ``plot_shared`` S3 settings.

Public contract (Phase 3 §3.1):

* ``put(key, data, content_type) -> uri`` — store bytes, return a stable URI.
* ``get(key) -> bytes`` — read bytes back.
* :meth:`put_render` — convenience: store a :class:`RenderResult` + its style sidecar.

This module must NOT import ``plot_connectors`` or ``plot_rules`` (Phase 3 §3.4).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from plot_shared import Settings, get_settings

if TYPE_CHECKING:
    from plot_reports.render.renderer import RenderResult

# Default local artifacts directory (overridable via the constructor / env). Kept out
# of the DB — object storage / filesystem only (§26.3 "no raster blobs in DB").
DEFAULT_ARTIFACTS_DIR = Path(".artifacts")

_SIDECAR_SUFFIX = ".style.json"


def _sidecar_key(key: str) -> str:
    """Sidecar key for an artifact: ``maps/x.png`` -> ``maps/x.png.style.json``."""
    return f"{key}{_SIDECAR_SUFFIX}"


class ArtifactStore(ABC):
    """Abstract artifact store (Phase 3 §3.1 deliverable 2)."""

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store ``data`` under ``key`` and return its URI."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the bytes stored under ``key``."""

    def put_render(self, key: str, result: RenderResult) -> str:
        """Store a render plus its style-metadata sidecar; return the image URI.

        The sidecar lands next to the image at ``<key>.style.json`` (NFR-AUD-009).
        """
        uri = self.put(key, result.data, result.mime_type)
        sidecar = json.dumps(result.style_metadata, indent=2, sort_keys=True).encode("utf-8")
        self.put(_sidecar_key(key), sidecar, "application/json")
        return uri

    def get_style_metadata(self, key: str) -> dict[str, object]:
        """Read back the style sidecar for an artifact ``key``."""
        raw = self.get(_sidecar_key(key))
        return json.loads(raw.decode("utf-8"))


class LocalArtifactStore(ArtifactStore):
    """Filesystem-backed artifact store (the default; needs no MinIO for tests)."""

    def __init__(self, base_dir: Path | str = DEFAULT_ARTIFACTS_DIR) -> None:
        self.base_dir = Path(base_dir)

    def _path(self, key: str) -> Path:
        # Guard against path traversal escaping the artifacts dir (defensive; §16).
        target = (self.base_dir / key).resolve()
        base = self.base_dir.resolve()
        if base not in target.parents and target != base:
            raise ValueError(f"Artifact key escapes the artifacts dir: {key!r}")
        return target

    def put(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.as_uri()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class S3ArtifactStore(ArtifactStore):  # pragma: no cover - requires a live S3/MinIO
    """Optional S3/MinIO-backed artifact store using ``plot_shared`` S3 settings.

    Lazy-imports ``boto3`` so the dependency is only needed when this backend is
    actually selected (the filesystem backend is the default — Phase 3 §3.1).
    """

    def __init__(self, settings: Settings | None = None, *, bucket: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.bucket = bucket or self.settings.s3_bucket

    def _client(self) -> object:
        import boto3  # lazy: only needed for the S3 backend

        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
        )

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self._client().put_object(  # type: ignore[attr-defined]
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        resp = self._client().get_object(Bucket=self.bucket, Key=key)  # type: ignore[attr-defined]
        body: bytes = resp["Body"].read()
        return body


def get_artifact_store(
    settings: Settings | None = None, *, base_dir: Path | str | None = None
) -> ArtifactStore:
    """Return the default artifact store (filesystem) for the given settings.

    The filesystem backend is the default so tests / local dev need no MinIO
    (Phase 3 §3.1). Switch to S3 explicitly by constructing :class:`S3ArtifactStore`.
    """
    if base_dir is not None:
        return LocalArtifactStore(base_dir)
    return LocalArtifactStore(DEFAULT_ARTIFACTS_DIR)
