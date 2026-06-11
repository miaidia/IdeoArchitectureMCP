# syntax=docker/dockerfile:1
# Plot Analyzer — combined API + MCP image (Phase 14B; multi-stage uv build).
#
# ONE process serves both surfaces (the v1 Phase 0.1 Starlette mount pattern):
#   /api → FastAPI (§27 endpoints)        /mcp → MCP streamable-http transport
# so the in-memory domain stores are SHARED between HTTP and MCP clients.
#
# Secrets (API keys, DB/S3 credentials) are injected via the environment ONLY
# (PLOT_* vars — F-0477/0478); nothing sensitive is baked into the image.
# PDF rendering note: weasyprint needs system pango/cairo, which this slim image
# does not install — report_generate(format="pdf") honestly returns
# pdf_unavailable in-container (HTML/MD/JSON carry the same numbers, §31).

# --------------------------------------------------------------------------- #
# build stage: resolve + install the locked workspace into /app/.venv
# --------------------------------------------------------------------------- #
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Layer-cache the dependency resolution: manifests first, sources second.
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps apps
RUN uv sync --frozen --no-dev --no-editable

# --------------------------------------------------------------------------- #
# runtime stage: slim Python + the built venv + runtime data (rulesets/schemas)
# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm AS runtime

# Non-root runtime user (container hardening; §24.4).
RUN useradd --create-home --uid 10001 plot
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY rulesets rulesets
COPY schemas schemas

ENV PATH="/app/.venv/bin:$PATH" \
    PLOT_DEV_HOT_RELOAD=false \
    PYTHONUNBUFFERED=1

# Artifact store (local backend) lives under /app/.artifacts unless S3 is configured.
RUN mkdir -p /app/.artifacts && chown -R plot:plot /app
USER plot

EXPOSE 8000

# Combined process: /api (FastAPI) + /mcp (streamable-http) — Phase 0.1 mount.
CMD ["uvicorn", "plot_api.main:build_combined_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
