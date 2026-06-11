"""Terrain analysis from a DEM/NMT raster (Phase 12 / v1 Phase 10 §10.1.1, §13).

Consumes an already-fetched GeoTIFF (bytes or an object-storage/local path — the
raster itself stays in object storage per §26.3; only the ``storage_uri``
reference travels in :class:`~plot_domain.TerrainModel`) and derives:

* **windowed sampling** over the parcel bbox + pad ONLY (NFR-PERF-007 — never the
  full county raster). rasterio API per the rasterio docs "Windowed reading and
  writing" (https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html):
  ``rasterio.windows.from_bounds(...)`` → ``dataset.read(1, window=...)``; the
  parcel mask uses ``rasterio.features.geometry_mask`` (docs: rasterio.features).
* **slope / aspect** via ``numpy.gradient`` (§13 "slope, aspect"): with rows
  ordered north→south, ``np.gradient(z, res_y, res_x)`` returns
  ``(d/d_row, d/d_col)``; ``dz/dx = d/d_col``, ``dz/dy = -d/d_row``. Slope% =
  ``100·hypot(dz/dx, dz/dy)``; aspect = azimuth of steepest DESCENT, clockwise
  from north: ``atan2(-dz/dx, -dz/dy)``.
* **elevation profiles** along the parcel's main axis and its perpendicular
  (coords → raster indices via ``rasterio.transform.rowcol``, rasterio docs).
* **depressions** — cells lower than all 8 neighbours by ≥ a config depth
  (local-minima proxy for "lokalne zagłębienia i ryzyko podtopień", §7.9).
* **earthworks risk class** per footprint from mean slope bands — a CONFIG
  heuristic (:class:`TerrainConfig`, ``basis: industry_heuristic``), not law.
* **basement precheck** (§7.9): slope + groundwater honesty (groundwater data is
  NOT wired — the precheck never claims feasibility, it flags what must be
  verified).

No legal values here; no network; no ``plot_connectors`` import (§9.4).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import plot_geo
from plot_domain import Severity, TerrainModel, UnknownItem
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

#: Earthworks risk classes, ordered (used by scoring + masterplan integration).
EARTHWORKS_CLASSES: tuple[str, ...] = ("niski", "umiarkowany", "wysoki", "bardzo_wysoki")


@dataclass(frozen=True)
class TerrainConfig:
    """Sampling + classification parameters (config heuristics, NOT law).

    Slope bands follow common earthworks practice for kubaturowe investments
    (flat site <2% → minimal niwelacja; 2–8% → stopniowanie/nasypy; 8–15% →
    retaining walls likely; >15% → major earthworks / split levels). They carry
    ``basis: industry_heuristic`` in every output — they are comparative risk
    classes, never a legal or geotechnical determination.
    """

    bbox_pad_m: float = 20.0  # analysis window pad around the parcel (NFR-PERF-007)
    profile_samples: int = 21  # points per elevation profile (geometry_sampling)
    depression_min_depth_m: float = 0.2  # local-minima depth to count as zagłębienie
    # Earthworks slope bands (% — mean slope inside a footprint / the parcel).
    slope_flat_pct: float = 2.0
    slope_moderate_pct: float = 8.0
    slope_high_pct: float = 15.0
    # Basement precheck: above this mean slope a basement implies stepped/retaining
    # structures (heuristic flag only — groundwater stays an explicit unknown).
    basement_max_slope_pct: float = 12.0

    def basis_block(self) -> dict[str, Any]:
        return {
            "slope_bands_pct": {
                "flat": self.slope_flat_pct,
                "moderate": self.slope_moderate_pct,
                "high": self.slope_high_pct,
            },
            "basement_max_slope_pct": self.basement_max_slope_pct,
            "depression_min_depth_m": self.depression_min_depth_m,
            "basis": "industry_heuristic",
            "note": "Klasy ryzyka robot ziemnych — heurystyka porownawcza, nie ocena geotechniczna.",
        }


@dataclass
class TerrainAnalysis:
    """Derived terrain context for one parcel (v1 Phase 10 §10.1.1).

    The raw grids (`_elev`, `_slope_pct`, `_transform`, `_parcel_mask`) are kept
    on the object (NOT serialized) so the masterplan integration can classify
    earthworks per building footprint without re-reading the raster.
    """

    status: str  # "ok" | "no_data"
    terrain_model: TerrainModel | None = None
    elevation: dict[str, float] = field(default_factory=dict)
    slope: dict[str, float] = field(default_factory=dict)
    aspect: dict[str, Any] = field(default_factory=dict)
    profiles: list[dict[str, Any]] = field(default_factory=list)
    depressions: dict[str, Any] = field(default_factory=dict)
    earthworks: dict[str, Any] = field(default_factory=dict)
    basement_precheck: dict[str, Any] = field(default_factory=dict)
    unknowns: list[UnknownItem] = field(default_factory=list)
    config_basis: dict[str, Any] = field(default_factory=dict)
    # Internal grids for per-footprint queries (masterplan integration, delta 1).
    _elev: Any = None
    _slope_pct: Any = None
    _transform: Any = None
    _nodata_mask: Any = None
    _config: TerrainConfig = field(default_factory=TerrainConfig)

    def earthworks_for_footprint(self, footprint: BaseGeometry) -> dict[str, Any]:
        """Earthworks risk class for ONE building footprint (delta 1).

        Mean/max slope% of the raster cells inside the footprint → config band.
        Returns an honest ``unknown`` block when the terrain grids are absent or
        the footprint covers no sampled cell.
        """
        if self.status != "ok" or self._slope_pct is None:
            return {
                "class": None,
                "status": "unknown",
                "reason": "terrain_data_unavailable",
            }
        from rasterio.features import geometry_mask  # rasterio.features docs

        mask = geometry_mask(
            [footprint.__geo_interface__],
            out_shape=self._slope_pct.shape,
            transform=self._transform,
            invert=True,  # True INSIDE the footprint
        )
        mask = mask & ~self._nodata_mask
        if not bool(mask.any()):
            return {"class": None, "status": "unknown", "reason": "footprint_outside_raster"}
        cells = self._slope_pct[mask]
        # Review B1: nodata-adjacent cells carry NaN gradients — exclude them; a
        # footprint with no finite slope cell is honestly unknown, never flat.
        if not bool(np.isfinite(cells).any()):
            return {"class": None, "status": "unknown", "reason": "insufficient_valid_cells"}
        mean_pct = float(np.nanmean(cells))
        max_pct = float(np.nanmax(cells))
        return {
            "class": self._classify_slope(mean_pct),
            "mean_slope_pct": round(mean_pct, 2),
            "max_slope_pct": round(max_pct, 2),
            "status": "ok",
            "basis": "industry_heuristic",
        }

    def _classify_slope(self, mean_pct: float) -> str:
        cfg = self._config
        if mean_pct < cfg.slope_flat_pct:
            return EARTHWORKS_CLASSES[0]
        if mean_pct < cfg.slope_moderate_pct:
            return EARTHWORKS_CLASSES[1]
        if mean_pct < cfg.slope_high_pct:
            return EARTHWORKS_CLASSES[2]
        return EARTHWORKS_CLASSES[3]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "terrain_model": (
                self.terrain_model.model_dump(mode="json") if self.terrain_model else None
            ),
            "elevation": self.elevation,
            "slope": self.slope,
            "aspect": self.aspect,
            "profiles": self.profiles,
            "depressions": self.depressions,
            "earthworks": self.earthworks,
            "basement_precheck": self.basement_precheck,
            "config_basis": self.config_basis,
        }


def terrain_unavailable(reason: str) -> TerrainAnalysis:
    """Honest no-data terrain result (source down / not configured — §21)."""
    return TerrainAnalysis(
        status="no_data",
        unknowns=[
            UnknownItem(
                id=f"unk:terrain:{uuid.uuid4().hex[:8]}",
                topic="Ukształtowanie terenu (NMT/DEM)",
                severity=Severity.MEDIUM,
                reason=reason,
                suggested_action=(
                    "Pobrać NMT (WCS GUGiK) albo dostarczyć raster wysokościowy — "
                    "spadki/roboty ziemne pozostają nieznane, nie zakładać terenu płaskiego."
                ),
            )
        ],
    )


def analyze_terrain(
    raster: bytes | str | Path,
    parcel: BaseGeometry,
    *,
    storage_uri: str | None = None,
    source_id: str | None = None,
    model_type: str = "NMT",
    config: TerrainConfig | None = None,
) -> TerrainAnalysis:
    """Analyze the DEM over the parcel bbox + pad (windowed read ONLY).

    ``raster`` is GeoTIFF bytes (the snapshotted WCS coverage) or a path to the
    object-storage copy; ``storage_uri`` is recorded on the
    :class:`~plot_domain.TerrainModel` (no raster blobs in the DB, §26.3).
    """
    import rasterio  # rasterio docs: rasterio.open / MemoryFile
    from rasterio.features import geometry_mask
    from rasterio.io import MemoryFile
    from rasterio.windows import from_bounds

    cfg = config or TerrainConfig()

    def _analyze(src: Any) -> TerrainAnalysis:
        minx, miny, maxx, maxy = parcel.bounds
        pad = cfg.bbox_pad_m
        # Windowed read of the parcel bbox + pad — NEVER the full raster
        # (anti-pattern guard v1 §10.4 / NFR-PERF-007). boundless=True keeps the
        # window shape stable at raster edges (rasterio windowed-rw docs).
        window = from_bounds(
            minx - pad, miny - pad, maxx + pad, maxy + pad, transform=src.transform
        )
        nodata = src.nodata if src.nodata is not None else -9999.0
        z = src.read(1, window=window, boundless=True, fill_value=nodata).astype("float64")
        transform = src.window_transform(window)
        res_x = abs(transform.a)
        res_y = abs(transform.e)
        nodata_mask = np.isclose(z, nodata) | ~np.isfinite(z)
        if z.size == 0 or bool(nodata_mask.all()):
            return terrain_unavailable("raster_window_empty")
        # Review B1: blank nodata to NaN BEFORE differentiating — otherwise a
        # -9999 fill value next to a valid cell produces a garbage gradient
        # (slope ≈ 10⁴ %) silently attributed to the valid cell. NaN propagates
        # into the gradients of nodata-ADJACENT cells, which the nan-aware stats
        # below then honestly exclude.
        z[nodata_mask] = np.nan

        # Parcel mask (cells whose center falls inside the parcel polygon).
        parcel_mask = geometry_mask(
            [parcel.__geo_interface__], out_shape=z.shape, transform=transform, invert=True
        )
        valid = parcel_mask & ~nodata_mask
        if not bool(valid.any()):
            return terrain_unavailable("parcel_outside_raster")

        # --- slope / aspect (numpy.gradient — module docstring formula) ------- #
        d_row, d_col = np.gradient(z, res_y, res_x)
        dzdx = d_col
        dzdy = -d_row  # rows grow southward in a north-up raster
        slope_pct = 100.0 * np.hypot(dzdx, dzdy)
        slope_cells = slope_pct[valid]
        if not bool(np.isfinite(slope_cells).any()):
            # Every parcel cell borders nodata → no finite gradient (B1 honesty).
            return terrain_unavailable("insufficient_valid_cells")
        mean_slope = float(np.nanmean(slope_cells))
        max_slope = float(np.nanmax(slope_cells))

        # Dominant aspect: mean downslope vector over the parcel (circular mean;
        # nan-aware — nodata-adjacent cells carry NaN gradients, B1).
        down_e = float(np.nanmean(-dzdx[valid]))
        down_n = float(np.nanmean(-dzdy[valid]))
        if math.hypot(down_e, down_n) > 1e-9:
            aspect_deg = (math.degrees(math.atan2(down_e, down_n)) + 360.0) % 360.0
            aspect_block: dict[str, Any] = {
                "dominant_downslope_azimuth_deg": round(aspect_deg, 1),
                "cardinal": _cardinal(aspect_deg),
            }
        else:
            aspect_block = {"dominant_downslope_azimuth_deg": None, "cardinal": "plaski"}

        elev_cells = z[valid]
        elevation = {
            "min_m": round(float(elev_cells.min()), 2),
            "max_m": round(float(elev_cells.max()), 2),
            "mean_m": round(float(elev_cells.mean()), 2),
            "range_m": round(float(elev_cells.max() - elev_cells.min()), 2),
        }

        # --- elevation profiles along the main axis + its perpendicular ------- #
        axis = plot_geo.main_axis(parcel)
        profiles = [
            _profile(parcel, z, transform, nodata_mask, axis.azimuth_deg, cfg, "os_glowna"),
            _profile(
                parcel, z, transform, nodata_mask, axis.azimuth_deg + 90.0, cfg, "os_poprzeczna"
            ),
        ]

        # --- depressions (8-neighbour local minima, ≥ config depth) ----------- #
        depressions = _depressions(z, valid, cfg)

        # --- earthworks class for the parcel as a whole ------------------------ #
        analysis = TerrainAnalysis(
            status="ok",
            elevation=elevation,
            slope={"mean_pct": round(mean_slope, 2), "max_pct": round(max_slope, 2)},
            aspect=aspect_block,
            profiles=profiles,
            depressions=depressions,
            config_basis=cfg.basis_block(),
            _elev=z,
            _slope_pct=slope_pct,
            _transform=transform,
            _nodata_mask=nodata_mask,
            _config=cfg,
        )
        parcel_class = analysis._classify_slope(mean_slope)
        analysis.earthworks = {
            "parcel_class": parcel_class,
            "mean_slope_pct": round(mean_slope, 2),
            "max_slope_pct": round(max_slope, 2),
            "retaining_wall_risk": parcel_class in EARTHWORKS_CLASSES[2:],
            "basis": "industry_heuristic",
        }
        analysis.basement_precheck = _basement_precheck(mean_slope, cfg)
        # Groundwater is NOT wired (no PIG hydro source) — explicit unknown, the
        # basement precheck must never silently assume dry ground (§21).
        analysis.unknowns.append(
            UnknownItem(
                id=f"unk:groundwater:{uuid.uuid4().hex[:8]}",
                topic="Poziom wód gruntowych (podpiwniczenie/garaż podziemny)",
                severity=Severity.MEDIUM,
                reason="source_not_wired",
                suggested_action=(
                    "Zlecić badania geotechniczne / sprawdzić PIG-PIB hydro — "
                    "wykonalność kondygnacji podziemnych pozostaje nieznana."
                ),
            )
        )
        analysis.terrain_model = TerrainModel(
            id=f"terrain:{uuid.uuid4().hex[:8]}",
            model_type=model_type,
            resolution_m=round(float(res_x), 3),
            storage_uri=storage_uri,  # object-storage reference only (§26.3)
            slope_summary={
                "elevation": elevation,
                "slope": analysis.slope,
                "aspect": aspect_block,
                "earthworks": analysis.earthworks,
                "depressions": depressions,
            },
            source_id=source_id,
        )
        return analysis

    if isinstance(raster, bytes):
        with MemoryFile(raster) as mem, mem.open() as src:
            return _analyze(src)
    with rasterio.open(str(raster)) as src:
        return _analyze(src)


def _cardinal(azimuth_deg: float) -> str:
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int(((azimuth_deg + 22.5) % 360.0) // 45.0)]


def _profile(
    parcel: BaseGeometry,
    z: Any,
    transform: Any,
    nodata_mask: Any,
    azimuth_deg: float,
    cfg: TerrainConfig,
    label: str,
) -> dict[str, Any]:
    """Elevation profile along an axis through the parcel centroid (§10.1.1)."""
    from rasterio.transform import rowcol  # rasterio docs: rasterio.transform.rowcol

    c = parcel.centroid
    rad = math.radians(azimuth_deg)
    dx, dy = math.sin(rad), math.cos(rad)
    minx, miny, maxx, maxy = parcel.bounds
    half = math.hypot(maxx - minx, maxy - miny)  # long enough to span the parcel
    line = LineString(
        [(c.x - dx * half, c.y - dy * half), (c.x + dx * half, c.y + dy * half)]
    ).intersection(parcel)
    if line.is_empty or line.length <= 0:
        return {"label": label, "azimuth_deg": round(azimuth_deg % 360.0, 1), "points": []}
    if line.geom_type != "LineString":  # MultiLineString on concave parcels
        line = max(line.geoms, key=lambda g: g.length)
    points: list[dict[str, float]] = []
    n = cfg.profile_samples
    for i in range(n):
        p = line.interpolate(i / (n - 1), normalized=True)
        row, col = rowcol(transform, p.x, p.y)
        if 0 <= row < z.shape[0] and 0 <= col < z.shape[1] and not nodata_mask[row, col]:
            points.append(
                {
                    "distance_m": round(line.length * i / (n - 1), 2),
                    "elevation_m": round(float(z[row, col]), 2),
                }
            )
    return {
        "label": label,
        "azimuth_deg": round(azimuth_deg % 360.0, 1),
        "length_m": round(float(line.length), 2),
        "points": points,
    }


def _depressions(z: Any, valid: Any, cfg: TerrainConfig) -> dict[str, Any]:
    """Local minima ≥ depth threshold inside the parcel (zagłębienia, §7.9)."""
    padded = np.pad(z, 1, mode="edge")
    neighbor_min = np.full_like(z, np.inf)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            shifted = padded[1 + dr : 1 + dr + z.shape[0], 1 + dc : 1 + dc + z.shape[1]]
            neighbor_min = np.minimum(neighbor_min, shifted)
    sinks = valid & ((neighbor_min - z) >= cfg.depression_min_depth_m)
    count = int(sinks.sum())
    return {
        "detected": count > 0,
        "cell_count": count,
        "min_depth_threshold_m": cfg.depression_min_depth_m,
        "note": (
            "Lokalne zaglebienia (minima 8-sasiedztwa) — ryzyko stagnacji wod opadowych; "
            "kierunki splywu zweryfikowac na pelnym NMT."
            if count
            else "Brak lokalnych zaglebien w probkowanym oknie."
        ),
    }


def _basement_precheck(mean_slope_pct: float, cfg: TerrainConfig) -> dict[str, Any]:
    """§7.9 basement/underground-parking precheck — flags, never a feasibility claim."""
    slope_ok = mean_slope_pct <= cfg.basement_max_slope_pct
    return {
        "slope_within_basement_band": slope_ok,
        "mean_slope_pct": round(mean_slope_pct, 2),
        "groundwater": "unknown",  # honesty: no hydro source wired (§21)
        "verdict": "wymaga_badan_gruntowych",
        "note": (
            "Spadek terenu "
            + ("nie wyklucza" if slope_ok else "komplikuje (mury oporowe/poziomy dzielone)")
            + " kondygnacji podziemnych; poziom wod gruntowych NIEZNANY — decyzja po "
            "badaniach geotechnicznych."
        ),
        "basis": "industry_heuristic",
    }
