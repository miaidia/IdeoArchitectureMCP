"""Dev-only hot-reload file watcher (Phase 2 §2.1.3 / §2.4).

Watches ``rulesets/`` plus the ``packages/`` and ``apps/`` source trees. On any
change it refreshes the in-memory ruleset registry (rulesets are always read fresh)
and marks reload-needed. It is started ONLY when ``Settings.dev_hot_reload`` is True
(gated in ``server.py``), so production never runs it (§16).

Anti-patterns honoured (Phase 0.5 / §2.4):
  * It NEVER reloads the stdio transport — it only bumps the registry / rulesets.
  * It is a background task that must not crash the server: all exceptions from the
    watch loop are swallowed and logged.

API: ``watchfiles.awatch(*paths, stop_event=...)`` — verified via
``uv run python -c 'import watchfiles, inspect; print(inspect.signature(watchfiles.awatch))'``
against the installed watchfiles 1.2.0.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import anyio
from plot_shared import get_logger
from watchfiles import awatch

from plot_mcp_server.runtime import AppContext

_log = get_logger(__name__)


def _force_polling() -> bool:
    """Decide whether watchfiles should poll instead of using inotify.

    inotify is unreliable on WSL2 / network mounts (events are silently dropped),
    so we fall back to polling there. ``PLOT_WATCH_FORCE_POLLING=1`` forces it on.
    Verified on this host: ``awatch(force_polling=True)`` detects edits that the
    default inotify backend misses under WSL2.
    """
    override = os.getenv("PLOT_WATCH_FORCE_POLLING")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes"}
    return "microsoft" in platform.uname().release.lower()  # WSL signature


def _watch_paths(ctx: AppContext) -> list[str]:
    """Resolve existing watch targets relative to the repo root."""
    # ruleset_dir is .../rulesets/PL; repo root is two parents up.
    repo_root = ctx.ruleset_dir.parents[1]
    candidates = [
        repo_root / "rulesets",
        repo_root / "packages",
        repo_root / "apps",
    ]
    return [str(p) for p in candidates if p.exists()]


async def run_watcher(ctx: AppContext, stop_event: anyio.Event) -> None:
    """Background watch loop: on change, refresh rulesets and log it.

    Domain code (``packages``/``apps``) edits flag a reload-needed via the log; an
    explicit ``dev_reload`` call performs the ``importlib.reload`` so the change is
    re-listed to the client. Ruleset YAML edits are applied immediately (fresh load)
    because rulesets carry no Python identity to re-import.

    Shutdown is cooperative via ``stop_event`` (an ``anyio.Event``, which is what
    ``awatch`` accepts as its ``stop_event``) so we never cancel the generator
    mid-yield. The whole loop is defensive: it must never crash the server (§2.4).
    """
    paths = _watch_paths(ctx)
    if not paths:  # pragma: no cover - defensive
        _log.warning("hot_reload_watcher_no_paths")
        return

    force_polling = _force_polling()
    _log.info("hot_reload_watcher_started", paths=paths, force_polling=force_polling)
    try:
        async for changes in awatch(*paths, stop_event=stop_event, force_polling=force_polling):
            try:
                changed = {Path(p).as_posix() for _change, p in changes}
                touched_rulesets = any(
                    "/rulesets/" in p or p.endswith(".yaml") or p.endswith(".yml") for p in changed
                )
                if touched_rulesets:
                    reg = ctx.refresh_rulesets()
                    _log.info(
                        "hot_reload_rulesets_refreshed",
                        ruleset_version=reg.ruleset_version,
                        rule_count=len(reg.rules),
                        changed=sorted(changed)[:5],
                    )
                else:
                    # Source change: mark reload-needed; dev_reload performs importlib.reload.
                    _log.info("hot_reload_source_changed", changed=sorted(changed)[:5])
            except Exception as exc:  # noqa: BLE001 - watcher must never crash the server
                _log.warning("hot_reload_watch_iteration_failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - watcher must never crash the server
        # watchfiles can raise during cooperative stop; never let it crash the server.
        _log.warning("hot_reload_watcher_failed", error=str(exc))
    finally:
        _log.info("hot_reload_watcher_stopped")
