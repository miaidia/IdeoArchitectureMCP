"""In-memory planning store: acts + zones keyed by municipality (Phase 8 §8.1.1).

The MVP-grade store behind ``planning_fetch`` and the ``planning://{municipality_id}``
resources: parsed APP/GML results (:class:`~plot_planning.gml.ParsedPlanning`) are
ingested per municipality and read back by tools/resources. Process-local and
thread-safe like :class:`plot_agent.analysis.factory.AnalysisStore`; PostGIS
persistence lands in Phase 12 (§26).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from plot_domain import PlanningAct, PlanningZone

from plot_planning.gml import ParsedPlanning


@dataclass
class PlanningStore:
    """Process-local store of parsed planning acts + zones per municipality."""

    _acts: dict[str, list[PlanningAct]] = field(default_factory=dict)
    _zones: dict[str, list[PlanningZone]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def ingest(self, municipality_id: str, parsed: ParsedPlanning) -> None:
        """Store a parse result for ``municipality_id`` (idempotent re-ingest).

        Same-id acts are replaced together with their zones; incoming zones also
        replace stored zones with the same zone id, so re-ingesting the same
        parse result (including act-less documents) never duplicates zones.
        """
        with self._lock:
            acts = self._acts.setdefault(municipality_id, [])
            zones = self._zones.setdefault(municipality_id, [])
            new_act_ids = {a.id for a in parsed.acts}
            new_zone_ids = {z.id for z in parsed.zones}
            acts[:] = [a for a in acts if a.id not in new_act_ids] + list(parsed.acts)
            zones[:] = [
                z for z in zones if z.act_id not in new_act_ids and z.id not in new_zone_ids
            ] + list(parsed.zones)

    def acts_for(self, municipality_id: str) -> list[PlanningAct]:
        with self._lock:
            return list(self._acts.get(municipality_id, []))

    def act(self, municipality_id: str, act_id: str) -> PlanningAct | None:
        with self._lock:
            for act in self._acts.get(municipality_id, []):
                if act.id == act_id:
                    return act
        return None

    def act_by_id(self, act_id: str) -> PlanningAct | None:
        with self._lock:
            for acts in self._acts.values():
                for act in acts:
                    if act.id == act_id:
                        return act
        return None

    def zones_for(self, municipality_id: str | None = None) -> list[PlanningZone]:
        """Zones for one municipality, or all stored zones when ``None``."""
        with self._lock:
            if municipality_id is not None:
                return list(self._zones.get(municipality_id, []))
            return [z for zones in self._zones.values() for z in zones]

    def zones_for_act(self, act_id: str) -> list[PlanningZone]:
        with self._lock:
            return [z for zones in self._zones.values() for z in zones if z.act_id == act_id]

    def municipalities(self) -> list[str]:
        with self._lock:
            return sorted(self._acts.keys())

    def is_empty(self) -> bool:
        with self._lock:
            return not any(self._acts.values())

    def clear(self) -> None:
        with self._lock:
            self._acts.clear()
            self._zones.clear()


#: Module-level default store shared by the MCP tools/resources within a process.
DEFAULT_PLANNING_STORE = PlanningStore()
