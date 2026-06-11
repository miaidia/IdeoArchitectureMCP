"""Combined API+MCP ASGI process (Phase 14B; v1 Phase 0.1 mount pattern).

ONE process serves BOTH surfaces so they share the in-process domain stores
(see ``plot_api.__init__`` for the state-sharing decision):

* ``/api``  → the FastAPI app (``plot_api.app.create_app``) — §27 endpoints;
* ``/mcp``  → the MCP streamable-http transport (``mcp.streamable_http_app()``),
  run inside ``mcp.session_manager.run()`` per the Phase 0.1 doc:
  ``Mount("/mcp", app=mcp.streamable_http_app())`` within
  ``async with mcp.session_manager.run():``.

Run::

    uvicorn plot_api.main:build_combined_app --factory --host 0.0.0.0 --port 8000

(the Dockerfile CMD). For an API-only process use
``uvicorn "plot_api.app:create_app" --factory``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from plot_api.app import create_app


def build_combined_app() -> Starlette:
    """Factory: Starlette root mounting /api (FastAPI) + /mcp (streamable-http)."""
    # Import here so an API-only deployment never loads the MCP server module.
    from plot_mcp_server.server import mcp

    api = create_app()
    mcp_asgi = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # Phase 0.1: the streamable-http transport must run within the session
        # manager context (it owns the per-session task group).
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Mount("/api", app=api),
            Mount("/mcp", app=mcp_asgi),
        ],
        lifespan=lifespan,
    )


def main() -> None:  # pragma: no cover - thin uvicorn launcher
    """Console entry: serve the combined app on 0.0.0.0:8000."""
    import uvicorn

    uvicorn.run(build_combined_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":  # pragma: no cover
    main()
