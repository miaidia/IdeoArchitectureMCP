# Plot Analyzer MCP Server

Greenfield monorepo for the Plot Analyzer MCP server for Claude Code.

Phase 1 (foundation) is implemented: uv workspace, domain models, JSON Schemas,
SQLAlchemy + GeoAlchemy2 DB layer with Alembic, infra compose, and CI.
See `AGENTS/IMPLEMENTATION_PLAN.md` for the full phased plan and
`AGENTS/base_assumptions.md` for the authoritative specification.

## Tests, calibration & acceptance (Phase 16)

- **Masterplan golden corpus** — `tests/corpus/`: five plot classes (riverside
  ~5 ha, narrow śródmiejska infill, 1 ha MN, corner mixed-use, 2-parcel
  assembly), each with synthetic MPZP indicators, expected capacity RANGES, a
  hand-written compliant masterplan and a committed golden render
  (`UPDATE_GOLDEN=1 uv run pytest tests/corpus` regenerates the references).
- **Confidence calibration (§25.1)** — `plot_domain.confidence` composes the
  eight spec components into a banded composite (high/moderate/low/hint);
  calibrated against the hand-labeled `tests/calibration/manual_review_set.json`
  (≥90% band accuracy enforced). Envelope, rule-engine traces and parser
  indicators all emit the decomposition; reports flag `confidence < 0.60`
  outcomes for manual review.
- **Acceptance demo** — `tests/test_acceptance_v2.py::test_full_version_gate`
  replays the canonical architect session (full DD → planning parse → capacity
  → brief → two masterplan iterations → koncepcja report in every format →
  DXF/IFC/GeoJSON export → PZT draft → ruleset edit without code change →
  reproducibility + audit) over the in-memory MCP server, zero network.
- **Ops docs** — `docs/RUNBOOK.md` (start/stop/health/state caveats),
  `docs/RULESET_UPDATE.md` (the no-code-change ruleset workflow; regression
  runner fails on rules without golden vectors), `infra/k8s/` (minimal honest
  manifests, structurally tested).

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
uv run mypy apps
uv run pytest -q
```

## HTTP API + MCP (Phase 14B)

Both surfaces share the SAME domain use-cases (§27 — `plot_mcp_server.usecases`);
the API adds zero logic (a parity test enforces it).

```bash
# API only (dev):
PLOT_API_KEYS="devkey:admin:local" uv run uvicorn "plot_api.app:create_app" --factory --port 8000
# Combined API (/api) + MCP streamable-http (/mcp) — one process, shared stores:
PLOT_API_KEYS="devkey:admin:local" uv run uvicorn plot_api.main:build_combined_app --factory --port 8000

# CLI (thin client over the API via the plot_shared SDK):
export PLOT_ANALYZER_API_KEY=devkey
uv run plot-analyzer analyze 141201_1.0001.1867/2
uv run plot-analyzer report <analysis_id> --format md

# Docker (combined image; see infra/docker-compose.yml for the full stack):
docker compose -f infra/docker-compose.yml up --build api
```

Auth: `X-API-Key` header; keys via `PLOT_API_KEYS="key:role:tenant,..."`
(role ∈ `read|analyst|admin`; empty config = fail closed). OpenAPI at
`/openapi.json`, liveness at `/healthz`, Prometheus at `/metrics`.

**Deployment-scope security (documented, not faked in-process):** TLS
termination + rate limiting (reverse proxy), encryption at rest, IdP/OIDC RBAC,
per-tenant storage isolation (PostGIS RLS), antivirus engine for uploads (the
sandbox exposes an `AvScanner` hook and honestly reports `not_scanned`),
signed artifacts/SBOM publication (CI runs `pip-audit` + dependency listing;
signing happens in the release pipeline). A TypeScript SDK (F-0465) is NOT
shipped — generate one from `/openapi.json` if needed.
