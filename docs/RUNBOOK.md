# Plot Analyzer — operations runbook (Phase 16)

Audience: whoever runs the combined Plot Analyzer process (API `/api` + MCP
`/mcp`). Everything here describes what the code ACTUALLY does today — the
"honest limitations" section is part of the contract, not small print.

## Start / stop

```bash
# dev (in-process, hot-reloadable rulesets):
export PATH="$HOME/.local/bin:$PATH" && uv sync
PLOT_API_KEYS="devkey:admin:local" \
  uv run uvicorn plot_api.main:build_combined_app --factory --port 8000

# container (the repo Dockerfile; non-root, hot reload OFF):
docker compose -f infra/docker-compose.yml up --build api

# kubernetes (minimal manifests; see infra/k8s/README.md):
kubectl create secret generic plot-analyzer-secrets \
  --from-literal=PLOT_API_KEYS="<key>:<role>:<tenant>"
kubectl apply -f infra/k8s/
```

Stop = stop the process / `docker compose down` / `kubectl delete -f infra/k8s/`.
There is no graceful-drain logic beyond uvicorn's default signal handling;
in-flight analyses die with the process (see "state" below).

## Configuration (env only — no config files, no secrets in the repo)

| Variable | Meaning |
|---|---|
| `PLOT_API_KEYS` | `key:role:tenant,...` (roles `read|analyst|admin`). EMPTY = fail closed: every authenticated endpoint 401s. |
| `PLOT_DEV_HOT_RELOAD` | `true` adds the dev-only MCP tools (`dev_reload`, `selfimprove_run`). MUST be `false` in production (22-tool public surface). |
| `PLOT_DATABASE_URL` / `PLOT_REDIS_URL` / S3 settings | Optional backends (PostGIS persistence, Redis cache/queue, S3/MinIO artifacts). Without them the in-process defaults below apply. |

See `plot_shared.config.Settings` for the complete, typed list (every knob has
a docstring and a default).

## Health, metrics, diagnostics

- liveness/readiness: `GET /api/healthz` (also used by the k8s probes);
- Prometheus: `GET /api/metrics` (`plot_api_requests_total`, latency
  histograms; route-template labels only);
- source health: `GET /v1/sources/health` or the MCP `source_healthcheck` tool
  (per-connector circuit state; `diagnostics_run(probe_connectors=true)` does a
  live probe — network!);
- audit: API writes land in the API audit log (key-id, never the raw key);
  every `propose_layout` iteration is audited with inputs-hash + rationale.

## State & persistence — read this before scaling

HONEST LIMITATIONS of the current build:

- **Analysis results, masterplan variants, monitoring profiles, overrides,
  upload sandbox: in-memory, per process.** A restart loses them; two replicas
  do not share them. Keep ONE replica (the k8s manifest pins `replicas: 1`)
  until the PostGIS-backed stores are wired in a later phase.
- **Artifact store: local directory** (`.artifacts/`, `emptyDir` in k8s) unless
  the S3/MinIO backend is configured — renders/exports/evidence packs do not
  survive a pod reschedule on the default backend.
- **Connector cache: in-memory default; Redis optional.** Snapshots of raw
  source responses are content-addressed in the artifact store
  (`snapshots/<source_id>/<sha256>`) and can be re-normalized offline
  (`tests/test_offline_snapshot.py` proves the path).
- **Queue (Dramatiq) is optional**: without a broker, `cache_warm`/portfolio
  run in-process and synchronously.

## Broker / workers

With `PLOT_REDIS_URL` + queue enabled, `cache_warm` and batch tasks are sent to
the Dramatiq worker (`apps/worker`). Run it from the same image:
`uv run plot-worker` (compose service `worker`). Without a broker everything
still works in-process — slower, never wrong.

## Ruleset updates

The no-code-change workflow lives in `docs/RULESET_UPDATE.md`. Production
processes (hot reload OFF) pick rulesets up at startup; dev processes can
`dev_reload` without a restart.

## Incident quick checks

1. `GET /api/healthz` — process up?
2. `GET /v1/sources/health` — which upstream is down? A dead source degrades to
   `source_unavailable` + partial results (never fake "clear" answers): expect
   `status: partial` analyses, not failures.
3. `/api/metrics` latency histograms — quick screening should sit well under
   its budget (`perf_quick_budget_s`, default 5 s).
4. Upload rejections (4xx) are by design: size/type/page caps + filename
   normalization; see `tests/test_upload_fuzzing.py` for the contract.
