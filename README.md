# Plot Analyzer MCP Server

Greenfield monorepo for the Plot Analyzer MCP server for Claude Code.

Phase 1 (foundation) is implemented: uv workspace, domain models, JSON Schemas,
SQLAlchemy + GeoAlchemy2 DB layer with Alembic, infra compose, and CI.
See `AGENTS/IMPLEMENTATION_PLAN.md` for the full phased plan and
`AGENTS/base_assumptions.md` for the authoritative specification.

## Layout

- `apps/{api,mcp-server,worker,web}` — process entry points.
- `packages/*` — decoupled domain/infrastructure packages (`plot_*` import names).
- `rulesets/PL/**` — versioned legal/technical rulesets (YAML, later phases).
- `schemas/` — JSON Schema contracts generated from `plot_domain`.
- `migrations/` — Alembic migrations (analytical geometry CRS = EPSG:2180).
- `infra/` — docker-compose (PostGIS + Redis + MinIO), k8s/terraform placeholders.

## Quick start (dev)

```bash
export PATH="$HOME/.local/bin:$PATH"
uv sync
uv run ruff check .
uv run mypy packages
uv run pytest -q
```
