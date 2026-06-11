"""Change monitoring (Phase 13 / v1 Phase 11 §11.1.3; §4.5 ``monitoring_changes``).

A :class:`Monitor` registers a watch over a parcel/municipality target for an
explicit purpose. :func:`run_monitor_check` re-fetches the relevant state,
diffs it against the stored snapshot (content hash + a SEMANTIC diff for
planning acts: new / changed / removed act ids), appends to the snapshot
archive and — on change — records a :class:`ChangeAlert` and optionally POSTs
it to a webhook **through the EXISTING egress allowlist** (F-0418: a
non-allowlisted webhook host is blocked, recorded, never silently sent).

Scheduling is an IN-MEMORY interface only (:meth:`MonitorStore.due`): real
cron/periodic execution is a deployment concern (the worker's
``monitoring_check_task`` is the unit a scheduler invokes) — documented, not
faked.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from plot_shared import get_logger

_log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


@dataclass
class ChangeAlert:
    """One detected change (F-0411-style alert record)."""

    alert_id: str
    monitor_id: str
    detected_at: str
    diff: dict[str, Any]
    webhook_status: str = "not_configured"  # not_configured | sent | blocked_egress | post_failed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Monitor:
    """A registered change monitor (§4.5): scope + target + purpose + interval."""

    monitor_id: str
    scope: str  # parcel | municipality | analysis
    target_id: str
    purpose: str
    interval_s: float
    webhook_url: str | None = None
    active: bool = True
    created_at: str = field(default_factory=lambda: _now().isoformat())
    last_checked_at: str | None = None
    last_snapshot: dict[str, Any] | None = None
    #: Append-only archive of {checked_at, snapshot_hash} entries (§4.5).
    snapshot_archive: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[ChangeAlert] = field(default_factory=list)

    def config_echo(self) -> dict[str, Any]:
        return {
            "monitoring_id": self.monitor_id,
            "scope": self.scope,
            "target_id": self.target_id,
            "purpose": self.purpose,
            "interval_s": self.interval_s,
            "webhook_url": self.webhook_url,
            "active": self.active,
            "created_at": self.created_at,
        }


@dataclass
class MonitorStore:
    """Process-local monitor registry (same idiom as the other default stores)."""

    _monitors: dict[str, Monitor] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, monitor: Monitor) -> None:
        with self._lock:
            self._monitors[monitor.monitor_id] = monitor

    def get(self, monitor_id: str) -> Monitor | None:
        with self._lock:
            return self._monitors.get(monitor_id)

    def all_monitors(self) -> list[Monitor]:
        with self._lock:
            return list(self._monitors.values())

    def due(self, now: datetime | None = None) -> list[Monitor]:
        """Monitors whose interval elapsed (in-memory scheduler INTERFACE).

        A deployment-level scheduler (cron/systemd timer/worker beat) calls
        this and enqueues ``monitoring_check_task`` per due monitor — running
        the clock loop itself is out of scope here (documented).
        """
        now = now or _now()
        due: list[Monitor] = []
        for monitor in self.all_monitors():
            if not monitor.active:
                continue
            if monitor.last_checked_at is None:
                due.append(monitor)
                continue
            last = datetime.fromisoformat(monitor.last_checked_at)
            if (now - last).total_seconds() >= monitor.interval_s:
                due.append(monitor)
        return due

    def clear(self) -> None:
        with self._lock:
            self._monitors.clear()


#: Module-level default registry shared by the MCP use-case + worker actors.
DEFAULT_MONITOR_STORE = MonitorStore()


def create_monitor(
    scope: str,
    target_id: str,
    purpose: str,
    *,
    interval_s: float = 24 * 3600.0,
    webhook_url: str | None = None,
    store: MonitorStore | None = None,
) -> Monitor:
    """Register a monitor (the real ``monitoring_create`` use-case, §4.5)."""
    monitor = Monitor(
        monitor_id=f"mon:{uuid.uuid4().hex[:12]}",
        scope=scope,
        target_id=target_id,
        purpose=purpose,
        interval_s=float(interval_s),
        webhook_url=webhook_url,
    )
    (store or DEFAULT_MONITOR_STORE).put(monitor)
    _log.info(
        "monitor_created",
        monitor_id=monitor.monitor_id,
        scope=scope,
        target_id=target_id,
        purpose=purpose,
    )
    return monitor


# --------------------------------------------------------------------------- #
# State fetchers + semantic diff
# --------------------------------------------------------------------------- #
def planning_acts_state(municipality_id: str, *, planning_store: Any | None = None) -> dict[str, Any]:
    """Current planning-act state for a municipality (the default municipality fetcher).

    Reads the planning store (ingested APP/GML — the production path re-ingests
    from the registered source URL first via ``planning_ingest_from_url``).
    Each act is hashed over its full model dump, so ANY attribute change is a
    semantic "changed act".
    """
    from plot_planning import DEFAULT_PLANNING_STORE

    store = planning_store if planning_store is not None else DEFAULT_PLANNING_STORE
    acts: dict[str, dict[str, Any]] = {}
    for act in store.acts_for(municipality_id):
        dump = act.model_dump(mode="json")
        acts[act.id] = {
            "hash": _hash_payload(dump),
            "status": act.status,
            "act_type": act.act_type.value,
            "title": act.title,
        }
    return {"kind": "planning_acts", "municipality_id": municipality_id, "acts": acts}


def diff_states(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """Hash + semantic diff between two monitor states.

    For planning-act states the diff lists ``new_acts`` / ``changed_acts`` /
    ``removed_acts`` by act id (§4.5 "alerty o zmianach MPZP/POG/projektów
    planów"); other states fall back to the content-hash comparison.
    """
    new_hash = _hash_payload(new)
    if old is None:
        return {"changed": False, "initial_snapshot": True, "snapshot_hash": new_hash}
    old_hash = _hash_payload(old)
    diff: dict[str, Any] = {
        "changed": old_hash != new_hash,
        "snapshot_hash": new_hash,
        "previous_hash": old_hash,
    }
    old_acts = old.get("acts") if isinstance(old.get("acts"), dict) else None
    new_acts = new.get("acts") if isinstance(new.get("acts"), dict) else None
    if old_acts is not None and new_acts is not None:
        diff["new_acts"] = sorted(set(new_acts) - set(old_acts))
        diff["removed_acts"] = sorted(set(old_acts) - set(new_acts))
        diff["changed_acts"] = sorted(
            aid
            for aid in set(old_acts) & set(new_acts)
            if old_acts[aid].get("hash") != new_acts[aid].get("hash")
        )
    return diff


def _post_webhook(
    monitor: Monitor,
    alert: ChangeAlert,
    *,
    allowlist: Any | None,
    client: httpx.Client | None,
) -> str:
    """POST the alert through the EXISTING egress allowlist (F-0418).

    Returns the webhook status string. A host outside the allowlist raises
    ``EgressBlocked`` inside ``check_url`` → recorded as ``blocked_egress``;
    transport errors are recorded as ``post_failed`` — a webhook failure never
    fails the check itself.
    """
    from plot_connectors.base.egress import EgressAllowlist
    from plot_connectors.base.errors import EgressBlocked

    assert monitor.webhook_url is not None
    guard = allowlist or EgressAllowlist.from_settings()
    try:
        guard.check_url(monitor.webhook_url)
    except EgressBlocked as exc:
        _log.warning("webhook_blocked", monitor_id=monitor.monitor_id, error=str(exc))
        return "blocked_egress"
    payload = {"monitor": monitor.config_echo(), "alert": alert.to_dict()}
    try:
        if client is not None:
            response = client.post(monitor.webhook_url, json=payload)
        else:
            with httpx.Client(timeout=10.0) as own:
                response = own.post(monitor.webhook_url, json=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _log.warning("webhook_post_failed", monitor_id=monitor.monitor_id, error=str(exc))
        return "post_failed"
    return "sent"


def run_monitor_check(
    monitor_id: str,
    *,
    store: MonitorStore | None = None,
    fetch_state: Callable[[Monitor], dict[str, Any]] | None = None,
    allowlist: Any | None = None,
    webhook_client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One monitoring check: fetch → diff → (alert + webhook) → archive (§4.5).

    ``fetch_state`` is injectable (tests pass fixtures); the default for
    ``scope == "municipality"`` is :func:`planning_acts_state`. The FIRST check
    establishes the baseline snapshot (no alert). No change → no alert.
    """
    registry = store or DEFAULT_MONITOR_STORE
    monitor = registry.get(monitor_id)
    if monitor is None:
        return {"monitor_id": monitor_id, "status": "not_found"}
    now = now or _now()

    if fetch_state is not None:
        state = fetch_state(monitor)
    elif monitor.scope == "municipality":
        state = planning_acts_state(monitor.target_id)
    else:
        # Honest gap: parcel/analysis re-fetch needs the bound connector set —
        # callers supply fetch_state (worker wires it); never a fabricated state.
        return {
            "monitor_id": monitor_id,
            "status": "fetch_not_configured",
            "note": (
                f"Brak domyślnego fetchera dla scope='{monitor.scope}' — przekaż "
                "fetch_state (worker konfiguruje go per źródło)."
            ),
        }

    diff = diff_states(monitor.last_snapshot, state)
    monitor.snapshot_archive.append(
        {"checked_at": now.isoformat(), "snapshot_hash": diff.get("snapshot_hash")}
    )
    monitor.last_snapshot = state
    monitor.last_checked_at = now.isoformat()

    alert_doc: dict[str, Any] | None = None
    if diff.get("changed"):
        alert = ChangeAlert(
            alert_id=f"alert:{uuid.uuid4().hex[:12]}",
            monitor_id=monitor_id,
            detected_at=now.isoformat(),
            diff=diff,
        )
        if monitor.webhook_url:
            alert.webhook_status = _post_webhook(
                monitor, alert, allowlist=allowlist, client=webhook_client
            )
        monitor.alerts.append(alert)
        alert_doc = alert.to_dict()
        _log.info("monitor_change_detected", monitor_id=monitor_id, diff=diff)
    registry.put(monitor)
    return {
        "monitor_id": monitor_id,
        "status": "changed" if diff.get("changed") else "no_change",
        "diff": diff,
        "alert": alert_doc,
        "snapshot_archive_length": len(monitor.snapshot_archive),
        "checked_at": now.isoformat(),
    }
