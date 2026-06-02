"""Input hashing and immutable geometry snapshots (F-0026).

``input_hash`` produces a stable SHA-256 over the *normalised* geometry plus a
canonicalised parameter mapping, so the same logical input always hashes identically
(required for analysis idempotency, base_assumptions §9.4: every analysis carries an
``input_hash``). Geometry is normalised via ``shapely.normalize`` and serialised to WKB
so vertex ordering / float formatting cannot perturb the hash.

``GeometrySnapshot`` is a frozen, hashable record of a geometry at a point in time
(geometry WKB + CRS + hash + created_at), suitable for ``source_snapshot_id`` style
referencing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import shapely
from shapely.geometry.base import BaseGeometry

from .crs import ANALYTICAL_CRS


def _canonical_params(params: dict[str, Any] | None) -> str:
    """Deterministic JSON for a params mapping (sorted keys, compact separators)."""
    if not params:
        return "{}"
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def input_hash(geom: BaseGeometry | None, params: dict[str, Any] | None = None) -> str:
    """Stable SHA-256 hex digest of (normalised geometry, canonical params) — F-0026.

    The geometry is normalised first so two geometrically identical inputs with
    different vertex order or representation hash to the same value.
    """
    h = hashlib.sha256()
    if geom is not None and not geom.is_empty:
        normalised = shapely.normalize(geom)
        # WKB is a stable binary representation; precision rounding avoids float jitter.
        wkb = shapely.to_wkb(shapely.set_precision(normalised, 1e-6), include_srid=False)
        h.update(wkb)
    else:
        h.update(b"\x00empty")
    h.update(b"\x00")
    h.update(_canonical_params(params).encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class GeometrySnapshot:
    """Immutable snapshot of a geometry at a moment in time (F-0026).

    Stores the geometry as WKB (so the dataclass is hashable/immutable), the CRS it is
    expressed in, the :func:`input_hash`, and a UTC timestamp.
    """

    wkb: bytes
    crs: str
    hash: str
    created_at: datetime
    params: str  # canonical JSON of the params used

    @classmethod
    def create(
        cls,
        geom: BaseGeometry,
        *,
        crs: str = ANALYTICAL_CRS,
        params: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> GeometrySnapshot:
        ts = created_at or datetime.now(UTC)
        return cls(
            wkb=shapely.to_wkb(shapely.normalize(geom), include_srid=False),
            crs=crs,
            hash=input_hash(geom, params),
            created_at=ts,
            params=_canonical_params(params),
        )

    @property
    def geometry(self) -> BaseGeometry:
        """Rehydrate the shapely geometry from stored WKB."""
        return shapely.from_wkb(self.wkb)
