# k8s manifests (Phase 16 — minimal, honest)

Minimal manifests for the combined Plot Analyzer process (FastAPI `/api` + MCP
streamable-http `/mcp` — the repo `Dockerfile` image):

- `deployment.yaml` — 1-replica Deployment; non-root; probes on `/api/healthz`;
  `PLOT_API_KEYS` from the out-of-band `plot-analyzer-secrets` Secret;
  artifacts on an `emptyDir` (NON-persistent — configure the S3/MinIO backend
  for real persistence).
- `service.yaml` — ClusterIP in front of port 8000. TLS/rate limiting are
  ingress concerns.

Honest limitations (also in `docs/RUNBOOK.md`): the analysis/variant stores are
in-memory per process, so keep `replicas: 1` unless an external store is wired;
the `image:` reference is a placeholder to patch with your registry tag. The
PostGIS/Redis/MinIO companions from `infra/docker-compose.yml` are NOT
duplicated here — use managed services or your own charts.

Validated structurally by `tests/test_infra_k8s.py` (YAML parse + invariants).
