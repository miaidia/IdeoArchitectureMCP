"""Streamable-HTTP transport mounted under a Starlette app (Phase 2 §2.1 / Phase 0.1).

For remote/team deployment the MCP server is exposed over streamable-HTTP and mounted
under a parent Starlette app so the future HTTP API and MCP can share one process
(§27 shared use-cases). This copies the Phase 0.1 mount pattern:

    Mount("/mcp", app=mcp.streamable_http_app())   # within `async with mcp.session_manager.run():`

``FastMCP.streamable_http_app()`` (mcp/server/fastmcp/server.py:950) returns a Starlette
app whose own ``lifespan`` already runs ``self.session_manager.run()``
(server.py:1044 ``lifespan=lambda app: self.session_manager.run()``). To mount it we
forward that child lifespan to the parent app so the session manager is started.

Run with:  ``uv run uvicorn plot_mcp_server.http_app:app --port 8000``
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from plot_mcp_server.server import mcp

# Build the child streamable-HTTP ASGI app once (lazily creates the session manager).
_mcp_http_app = mcp.streamable_http_app()


@asynccontextmanager
async def _lifespan(app: Starlette) -> AsyncIterator[None]:
    # Drive the MCP streamable-HTTP session manager for the life of the parent app
    # (Phase 0.1: `async with mcp.session_manager.run():`).
    async with mcp.session_manager.run():
        yield


# Parent Starlette app: MCP under /mcp; the HTTP API will mount alongside in a later phase.
app = Starlette(
    routes=[Mount("/mcp", app=_mcp_http_app)],
    lifespan=_lifespan,
)
