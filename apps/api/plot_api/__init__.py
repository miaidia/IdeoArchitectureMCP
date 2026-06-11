"""plot_api — HTTP API for the Plot Analyzer (Phase 14B; §27, F-0463).

ARCHITECTURE DECISION (shared use-cases, §27 "API i MCP powinny korzystać z tych
samych use-case'ów domenowych"): every HTTP handler is a THIN call into
``plot_mcp_server.usecases`` — the SAME reloadable module the MCP tools
delegate to. ``plot_mcp_server.usecases`` holds zero MCP/transport state (it is
the documented shared layer; importing it does NOT import the FastMCP server),
so the import direction apps/api → plot_mcp_server.usecases is the §27 wiring,
not a layering violation. Extracting it into a third package was considered and
rejected: it would force the hot-reload machinery (``AppContext.reload``) to
track another module for zero behavioural gain.

STATE SHARING: the domain stores (``plot_agent.analysis.DEFAULT_STORE``, the
variant/brief/upload stores...) are process-local. The shipped deployment
(``plot_api.main.build_combined_app`` — the Dockerfile entrypoint) runs the API
and the MCP streamable-http transport in ONE process (Starlette mounts ``/api``
+ ``/mcp``), so both surfaces see the same analyses. Running them as SEPARATE
processes does NOT share in-memory state — analyses created over local MCP
stdio are not visible to a separately started API (durable cross-process
persistence is the PostGIS deployment concern, documented in the README).

AUTH (minimal honest RBAC, F-0479/0480): ``X-API-Key`` keys configured via the
``PLOT_API_KEYS`` env var ("key:role:tenant" triples; role ∈ read|analyst|admin;
empty ⇒ fail closed). Write endpoints need analyst+, overrides need admin; every
write is audit-logged with the sha256-derived key id (F-0481/0494); analyses are
tenant-tagged and cross-tenant reads 404. Full enterprise RBAC/multitenancy
(IdP integration, per-tenant storage, quotas) is a DEPLOYMENT concern beyond
this scope — documented, not faked.
"""

__version__ = "0.1.0"
