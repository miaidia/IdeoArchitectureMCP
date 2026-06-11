"""Exemplar memory v2 — persisted, density-keyed, few-shot ready (Phase 11 §11.1.4).

Extends the Phase 4 :class:`~plot_agent.drawing.memory.DrawingExemplarStore` (kept
byte-compatible — its tests stay green) with:

* the third key component ``density_class`` — a banding of intensywność
  (:func:`density_class_for`; band edges live in :class:`DensityBands` with
  ``basis: design_practice`` — a configuration choice, NOT a legal value);
* persistence to ``.artifacts/drawing-exemplars/`` as one JSON per exemplar
  (``<base>/<shape>__<program>[__<density>]/<uuid>.json``) plus an optional
  thumbnail PNG saved NEXT to it (``<uuid>.png``; ``Exemplar.thumbnail_path`` is
  relative to the base dir);
* an in-memory index loaded on store INIT — a process restart on the same directory
  reloads every persisted exemplar;
* :meth:`ExemplarStoreV2.recall` with exact-key → nearest-neighbour relaxation
  (same shape_class + program_type across density bands ordered by band distance,
  then same shape_class across programs);
* :func:`format_exemplars_for_prompt` — compact JSON + score summary for few-shot
  prompt assembly (part B wires it into the drawing prompt).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from plot_agent.drawing.memory import DEFAULT_EXEMPLAR_DIR, DrawingExemplarStore, Exemplar
from plot_agent.drawing.proposal import LayoutProposal, MasterplanProposal

#: Ordered density bands (low → high) used for nearest-neighbour band distance.
DENSITY_ORDER: tuple[str, ...] = ("low", "mid", "high")

#: Marker for an exemplar stored without a density key (v1 records, v1 store calls).
DENSITY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class DensityBands:
    """Intensywność banding for the exemplar key (plan §11.1.4).

    ``basis: design_practice`` — the band edges are a memory-bucketing CONFIG choice
    (coarse so similar-density plans collide on the same key), not a legal threshold;
    nothing may validate against them.
    """

    low_max: float = 0.5  # intensywność < low_max  -> "low"
    high_min: float = 1.5  # intensywność > high_min -> "high"; else "mid"
    basis: str = "design_practice"


def density_class_for(intensity: float | None, bands: DensityBands | None = None) -> str:
    """Band an intensywność value into low/mid/high (``unknown`` for None)."""
    if intensity is None:
        return DENSITY_UNKNOWN
    b = bands or DensityBands()
    value = float(intensity)
    if value < b.low_max:
        return "low"
    if value > b.high_min:
        return "high"
    return "mid"


def _band_distance(a: str | None, b: str | None) -> int:
    """Distance between two density bands (unknown is far from everything)."""
    if a in DENSITY_ORDER and b in DENSITY_ORDER:
        return abs(DENSITY_ORDER.index(a) - DENSITY_ORDER.index(b))
    return len(DENSITY_ORDER)


def _key_v2(shape_class: str, program_type: str, density_class: str | None) -> str:
    base = f"{shape_class}__{program_type}"
    return f"{base}__{density_class}" if density_class else base


class ExemplarStoreV2(DrawingExemplarStore):
    """Filesystem-backed exemplar memory v2 with an in-memory index.

    IS-A :class:`DrawingExemplarStore`, so it drops into ``DrawingLoop.exemplar_store``
    unchanged; the v1 ``store(...)``/``recall(...)`` call shapes keep working (a call
    without ``density_class`` reads/writes the v1 ``<shape>__<program>`` directory).
    """

    def __init__(self, base_dir: Path | str = DEFAULT_EXEMPLAR_DIR) -> None:
        super().__init__(base_dir)
        # Load on init: a process restart on the same dir reloads every exemplar
        # (v1 files load with density_class=None thanks to the dataclass defaults).
        self._index: list[Exemplar] = self.all_exemplars()

    # ------------------------------------------------------------------ #
    # Store (extends the v1 signature with optional kw-only v2 fields)
    # ------------------------------------------------------------------ #
    def store(
        self,
        *,
        shape_class: str,
        program_type: str,
        proposal: LayoutProposal | MasterplanProposal,
        score_total: float,
        components: dict[str, float] | None = None,
        parcel_area_m2: float | None = None,
        density_class: str | None = None,
        thumbnail_png: bytes | None = None,
    ) -> Exemplar:
        """Persist an accepted exemplar (+ optional thumbnail PNG next to the JSON).

        Callers must only store VALID, accepted proposals — the loop enforces this
        (a hard-violating proposal can never be accepted, §4.4).
        """
        exemplar_id = str(uuid.uuid4())
        key_dir = self.base_dir / _key_v2(shape_class, program_type, density_class)
        key_dir.mkdir(parents=True, exist_ok=True)

        thumbnail_path: str | None = None
        if thumbnail_png is not None:
            png_file = key_dir / f"{exemplar_id}.png"
            png_file.write_bytes(thumbnail_png)
            thumbnail_path = str(png_file.relative_to(self.base_dir))

        exemplar = Exemplar(
            exemplar_id=exemplar_id,
            shape_class=shape_class,
            program_type=program_type,
            proposal=proposal.model_dump(mode="json"),
            score_total=round(float(score_total), 4),
            components=components or {},
            parcel_area_m2=parcel_area_m2,
            created_at=datetime.now(UTC).isoformat(),
            density_class=density_class,
            thumbnail_path=thumbnail_path,
        )
        path = key_dir / f"{exemplar_id}.json"
        path.write_text(
            json.dumps(exemplar.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        self._index.append(exemplar)
        return exemplar

    # ------------------------------------------------------------------ #
    # Recall: exact key, then nearest-neighbour relaxation
    # ------------------------------------------------------------------ #
    def recall(
        self,
        shape_class: str,
        program_type: str,
        k: int = 3,
        *,
        density_class: str | None = None,
    ) -> list[Exemplar]:
        """Up to ``k`` best exemplars for ``(shape_class, program_type[, density])``.

        Exact-key matches first (best score first); when fewer than ``k`` exist the
        key is RELAXED: same shape_class + program_type across density bands (closest
        band first), then same shape_class across programs — so a similar parcel
        always recalls the most comparable accepted drawings (plan §11.1.4).
        Without ``density_class`` the behaviour is the v1 contract (any density,
        score-sorted) served from the in-memory index.
        """
        same_shape = [e for e in self._index if e.shape_class == shape_class]
        same_key = [e for e in same_shape if e.program_type == program_type]
        if density_class is None:
            ranked = sorted(same_key, key=lambda e: -e.score_total)
            ranked += sorted(
                (e for e in same_shape if e.program_type != program_type),
                key=lambda e: -e.score_total,
            )
            return ranked[:k]

        exact = sorted(
            (e for e in same_key if e.density_class == density_class),
            key=lambda e: -e.score_total,
        )
        relaxed_density = sorted(
            (e for e in same_key if e.density_class != density_class),
            key=lambda e: (_band_distance(e.density_class, density_class), -e.score_total),
        )
        relaxed_program = sorted(
            (e for e in same_shape if e.program_type != program_type),
            key=lambda e: (_band_distance(e.density_class, density_class), -e.score_total),
        )
        return (exact + relaxed_density + relaxed_program)[:k]


def format_exemplars_for_prompt(exemplars: list[Exemplar]) -> str:
    """Format recalled exemplars as few-shot blocks for the drawing prompt (part B).

    Each exemplar becomes a score summary line plus its proposal as COMPACT JSON —
    typed DSL data the model can imitate directly (never free-form, NFR-SEC-003).
    """
    if not exemplars:
        return "Brak zapisanych exemplarów dla tej klasy działki."
    blocks: list[str] = [
        "Przykłady zaakceptowanych propozycji (few-shot, posortowane wg score):"
    ]
    for i, ex in enumerate(exemplars, start=1):
        comp = ", ".join(f"{k}={v:.2f}" for k, v in sorted(ex.components.items()))
        blocks.append(
            f"### Exemplar {i} — score {ex.score_total:.3f} "
            f"(shape={ex.shape_class}, program={ex.program_type}, "
            f"density={ex.density_class or DENSITY_UNKNOWN})"
            + (f"\nkomponenty: {comp}" if comp else "")
        )
        blocks.append(json.dumps(ex.proposal, separators=(",", ":"), sort_keys=True))
    return "\n".join(blocks)
