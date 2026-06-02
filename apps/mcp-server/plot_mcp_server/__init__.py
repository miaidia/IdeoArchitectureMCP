"""Thin FastMCP server for Claude Code + hot-reload backbone (Phase 2).

Exposes the 20 public tools (base_assumptions §10.3), resource templates (§10.4) and
prompts (§10.5) as typed stubs, delegating all domain work to the reloadable
``usecases`` module so hot-reload never touches the transport (Phase 0.5 / §2.4).
"""

__version__ = "0.1.0"
