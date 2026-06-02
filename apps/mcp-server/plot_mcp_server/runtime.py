"""Server runtime state holder + reloadable-registry handle (Phase 2 §2.1).

``AppContext`` is what the FastMCP lifespan yields (Phase 0.1 lifespan pattern) and
what tools reach via ``ctx.request_context.lifespan_context``. It deliberately holds
NO transport state — only:

  * the loaded :class:`plot_rules.RulesetRegistry` (reloadable, fresh-per-reload),
  * a handle to the reloadable ``usecases`` module (``importlib.reload`` target),
  * the last-reload timestamp + server version (for ``diagnostics_run`` / F-0442),
  * the (optional/lazy) DB pool — stubbed in Phase 2.

Reloading happens here (rulesets + ``importlib.reload(usecases)``); the stdio/HTTP
transport is never touched (Phase 0.5 / §2.4 anti-pattern).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from plot_rules import RulesetRegistry, load_rulesets
from plot_shared import Settings

from plot_mcp_server import usecases as _usecases_module

SERVER_VERSION = "0.1.0"


def _repo_root() -> Path:
    """Locate the repo root (where ``rulesets/`` lives) from this file's location.

    apps/mcp-server/plot_mcp_server/runtime.py -> repo root is 3 parents up.
    Falls back to CWD so ``mcp dev`` launched from the root also works.
    """
    here = Path(__file__).resolve()
    candidate = here.parents[3]
    if (candidate / "rulesets").exists():
        return candidate
    return Path.cwd()


@dataclass
class AppContext:
    """Reloadable application context yielded by the FastMCP lifespan (Phase 0.1)."""

    settings: Settings
    ruleset_registry: RulesetRegistry
    usecases: ModuleType
    ruleset_dir: Path
    server_version: str = SERVER_VERSION
    last_reload_at: datetime | None = None
    reload_count: int = 0
    # DB pool is optional/lazy in Phase 2 (stub). Real pool wired in Phase 7/12.
    db: object | None = field(default=None, repr=False)

    @classmethod
    def create(cls, settings: Settings) -> AppContext:
        ruleset_dir = _repo_root() / "rulesets" / "PL"
        return cls(
            settings=settings,
            ruleset_registry=load_rulesets(ruleset_dir),
            usecases=_usecases_module,
            ruleset_dir=ruleset_dir,
        )

    def reload(self) -> RulesetRegistry:
        """Reload rulesets fresh + ``importlib.reload`` the usecases module.

        Returns the new :class:`RulesetRegistry`. Does NOT touch the MCP transport
        (Phase 0.5 / §2.4). Safe to call from the ``dev_reload`` tool and the watcher.
        """
        # Reload the domain/usecase logic in place (the registry, not the transport).
        self.usecases = importlib.reload(self.usecases)
        # Rulesets are always read fresh from disk (no cross-call cache in dev).
        self.ruleset_registry = load_rulesets(self.ruleset_dir)
        self.last_reload_at = datetime.now(UTC)
        self.reload_count += 1
        return self.ruleset_registry

    def refresh_rulesets(self) -> RulesetRegistry:
        """Reload only the rulesets from disk (used by the watcher on YAML changes)."""
        self.ruleset_registry = load_rulesets(self.ruleset_dir)
        self.last_reload_at = datetime.now(UTC)
        return self.ruleset_registry
