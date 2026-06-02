"""Drawing-exemplar memory — "learn to draw" store (Phase 4 §4.1.B.5 / F-0438).

Persists ACCEPTED high-scoring valid proposals keyed by ``(shape_class, program_type)``
so similar future parcels can recall them as few-shot exemplars (§4.1.B.5). Also exports
a JSONL dataset to COLLECT a fine-tuning corpus — collection only, no training (§4.1.B.5).

Filesystem-backed via a JSON directory (one file per exemplar) so tests need no DB/MinIO.
The store records score + critique + parcel/program metadata, NOT secrets.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plot_agent.drawing.proposal import LayoutProposal

DEFAULT_EXEMPLAR_DIR = Path(".artifacts/drawing-exemplars")


@dataclass
class Exemplar:
    """A stored accepted proposal (§4.1.B.5 few-shot exemplar)."""

    exemplar_id: str
    shape_class: str
    program_type: str
    proposal: dict[str, Any]  # LayoutProposal.model_dump()
    score_total: float
    components: dict[str, float] = field(default_factory=dict)
    parcel_area_m2: float | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _key(shape_class: str, program_type: str) -> str:
    return f"{shape_class}__{program_type}"


class DrawingExemplarStore:
    """Filesystem-backed exemplar memory (§4.1.B.5)."""

    def __init__(self, base_dir: Path | str = DEFAULT_EXEMPLAR_DIR) -> None:
        self.base_dir = Path(base_dir)

    # ------------------------------------------------------------------ #
    # Store / recall
    # ------------------------------------------------------------------ #
    def store(
        self,
        *,
        shape_class: str,
        program_type: str,
        proposal: LayoutProposal,
        score_total: float,
        components: dict[str, float] | None = None,
        parcel_area_m2: float | None = None,
    ) -> Exemplar:
        """Persist an accepted exemplar and return it.

        Callers must only store VALID, accepted proposals — the loop enforces this
        (a hard-violating proposal can never be accepted, §4.4).
        """
        exemplar = Exemplar(
            exemplar_id=str(uuid.uuid4()),
            shape_class=shape_class,
            program_type=program_type,
            proposal=proposal.model_dump(mode="json"),
            score_total=round(float(score_total), 4),
            components=components or {},
            parcel_area_m2=parcel_area_m2,
            created_at=datetime.now(UTC).isoformat(),
        )
        target = self.base_dir / _key(shape_class, program_type)
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{exemplar.exemplar_id}.json"
        path.write_text(json.dumps(exemplar.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return exemplar

    def recall(self, shape_class: str, program_type: str, k: int = 3) -> list[Exemplar]:
        """Return up to ``k`` best exemplars for a ``(shape_class, program_type)`` key.

        Sorted by ``score_total`` descending (best drawings first) so they make strong
        few-shot exemplars for the next similar parcel (§4.1.B.5).
        """
        target = self.base_dir / _key(shape_class, program_type)
        if not target.exists():
            return []
        exemplars: list[Exemplar] = []
        for path in sorted(target.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                exemplars.append(Exemplar(**doc))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        exemplars.sort(key=lambda e: e.score_total, reverse=True)
        return exemplars[:k]

    def all_exemplars(self) -> list[Exemplar]:
        """Every stored exemplar across all keys (used by :meth:`export_jsonl`)."""
        out: list[Exemplar] = []
        if not self.base_dir.exists():
            return out
        for path in sorted(self.base_dir.rglob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                out.append(Exemplar(**doc))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return out

    # ------------------------------------------------------------------ #
    # Fine-tuning dataset collection (collect only — no training, §4.1.B.5)
    # ------------------------------------------------------------------ #
    def export_jsonl(self, path: Path | str) -> int:
        """Write all exemplars as JSONL (one object per line); return the line count.

        Each line is a ``{prompt, completion}``-style record for a future fine-tune. We
        COLLECT the dataset here; training is explicitly out of scope (§4.1.B.5).
        """
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out_path.open("w", encoding="utf-8") as fh:
            for ex in self.all_exemplars():
                record = {
                    "prompt": {
                        "shape_class": ex.shape_class,
                        "program_type": ex.program_type,
                        "parcel_area_m2": ex.parcel_area_m2,
                    },
                    "completion": ex.proposal,
                    "score_total": ex.score_total,
                }
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                count += 1
        return count
