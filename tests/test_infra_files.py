"""Infra file checks (Phase 14B Task 4) — syntax/structure only, NO docker build.

Dockerfile: multi-stage uv build, non-root runtime, combined-process CMD, no
baked secrets. Compose: the api service exists and wires env-only secrets.
CI: mypy apps + pip-audit/uv-tree supply-chain steps present. Plus: the
combined ASGI app (API mounted at /api, MCP at /mcp) actually builds and
serves /api/healthz in-process.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_dockerfile_is_multistage_uv_build() -> None:
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    froms = re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE)
    assert len(froms) == 2, "expected a multi-stage build (builder + runtime)"
    assert "astral-sh/uv" in froms[0]
    assert "uv sync --frozen --no-dev" in text
    # combined-process entrypoint (Phase 0.1 mount pattern via the factory)
    assert "plot_api.main:build_combined_app" in text
    assert "--factory" in text
    # container hardening: non-root user, hot reload off
    assert "USER plot" in text
    assert "PLOT_DEV_HOT_RELOAD=false" in text
    # no baked secrets (env-only, F-0477)
    assert "PLOT_API_KEYS" not in text


def test_compose_has_api_service_with_env_only_secrets() -> None:
    doc = yaml.safe_load((REPO / "infra" / "docker-compose.yml").read_text(encoding="utf-8"))
    services = doc["services"]
    assert {"api", "postgis", "redis", "minio"} <= set(services)
    api = services["api"]
    assert api["build"]["dockerfile"] == "Dockerfile"
    # keys flow from the host env with an EMPTY (fail-closed) default
    assert api["environment"]["PLOT_API_KEYS"] == "${PLOT_API_KEYS:-}"
    assert api["environment"]["PLOT_DEV_HOT_RELOAD"] == "false"


def test_ci_workflow_has_supply_chain_steps() -> None:
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uv run mypy apps" in text
    assert "pip-audit" in text
    assert "uv tree" in text


def test_combined_app_serves_api_and_mounts_mcp(monkeypatch) -> None:
    monkeypatch.setenv("PLOT_API_KEYS", "")
    monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "false")
    import plot_shared.config as cfg

    cfg.get_settings.cache_clear()
    from plot_api.main import build_combined_app
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    app = build_combined_app()
    mounts = {r.path for r in app.routes if isinstance(r, Mount)}
    assert {"/api", "/mcp"} <= mounts

    # lifespan runs the MCP session manager (Phase 0.1) while /api serves HTTP
    with TestClient(app) as client:
        response = client.get("/api/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    cfg.get_settings.cache_clear()
