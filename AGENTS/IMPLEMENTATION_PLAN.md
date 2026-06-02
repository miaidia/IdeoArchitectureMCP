# IMPLEMENTATION_PLAN.md — Plot Analyzer MCP Server for Claude Code

**Plan version:** 1.0
**Date:** 2026-06-02
**Source spec:** [base_assumptions.md](base_assumptions.md) (v1.0, 561 functional requirements F-0001…F-0561)
**Locked decisions (from user):**

- **Stack:** Python 3.12+ (FastMCP / `mcp` package + GeoPandas / Shapely 2.x / Rasterio / PyProj / PostGIS). One language for the whole server (spec §9.3).
- **Self-improvement:** agent **dev-loop** — Claude Code edits code/rulesets → **hot-reload** → runs golden tests + screenshots → scores → iterates until green. This loop is *infrastructure that builds the rest of the system*.
- **Drawing / verification:** algorithmic GIS render → PNG/SVG returned as MCP **image content** so the model literally sees its output and verifies it; **plus** a generative loop where the model "draws like an architect" (proposes footprints/site layouts), screenshots its own drawing, scores parameters (buildability/capacity/sun), and **learns to draw better** from an exemplar memory.
- **Scope:** full system, all stages (Etap 0–5) detailed, with the four meta-features woven in early.

> This document is the **orchestration contract** for the `do` skill. Each phase is self-contained: open it in a fresh context, read its doc references, copy from them, then run its verification checklist. Phases are ordered so the **hot-reload + screenshot + self-improve loop (Phases 2–4) exists before the heavy domain work**, so the agent can build the rest using its own dev-loop.

---

## How to read this plan

Each implementation phase has four mandatory sections:

1. **What to implement** — framed to *copy from documentation*, not to invent.
2. **Documentation references** — exact APIs (from Phase 0) + spec sections + `base_assumptions.md` line ranges to copy.
3. **Verification checklist** — how to *prove* the phase works (tests, greps, MCP Inspector, screenshots).
4. **Anti-pattern guards** — what NOT to do (invented APIs, undocumented params, spec §21 antipatterns).

Every analytical module must return the canonical shape `{result, evidence, confidence, warnings, unknowns}` (spec §9.4, instruction §20.4). Every MCP write-tool must be annotated and audited (spec §16, NFR-SEC-010).

---

# PHASE 0 — Documentation Discovery & Allowed APIs (FOUNDATION — READ FIRST)

This phase is **research already performed**; treat the "Allowed APIs" below as the authoritative list. Do **not** invent methods outside it — if something is missing, fetch the cited doc and confirm before use.

## 0.1 MCP — FastMCP Python SDK

**Source consulted:** https://github.com/modelcontextprotocol/python-sdk (Python SDK README, fetched 2026-06-02).

**Package:** `mcp` — install `pip install "mcp[cli]"` or `uv add "mcp[cli]"`.

**Allowed imports / APIs (verbatim from docs):**

```python
from mcp.server.fastmcp import FastMCP, Context, Image, Icon
from mcp.server.session import ServerSession
from mcp.types import CallToolResult, TextContent  # ImageContent, ToolAnnotations also in mcp.types

mcp = FastMCP(name="plot-analyzer", lifespan=app_lifespan, json_response=True, stateless_http=True)

@mcp.tool()                                  # structured output auto-derived from return type
def parcel_resolve(...) -> ResolveResult: ...
@mcp.tool(structured_output=False)           # suppress structured output when undesired
@mcp.resource("parcel://PL/{teryt}/{district}/{parcel_id}")   # URI-template resources
def get_parcel(teryt: str, district: str, parcel_id: str) -> str: ...
@mcp.prompt(title="Analyze plot for purchase")
def analyze_plot_for_purchase(...) -> str | list: ...
```

**Context object (inside tools/resources):**

```python
async def tool(ctx: Context[ServerSession, AppContext]) -> ...:
    await ctx.info(msg); await ctx.debug(msg); await ctx.warning(msg); await ctx.error(msg)  # logging
    await ctx.report_progress(progress=0.5, total=1.0, message="step")                       # progress
    content = await ctx.read_resource(uri)                                                    # read resource
    db = ctx.request_context.lifespan_context.db                                              # app context
    await ctx.session.send_resource_updated(AnyUrl(uri))                                      # notify
    # ctx.elicit(...) / ctx.session.create_message(...) available but NOT used for analysis facts
```

**Structured output:** return a Pydantic `BaseModel` / `TypedDict` / `dataclass` / `dict[str, T]`; SDK derives `outputSchema` and emits both `structuredContent` and a JSON `TextContent` (backwards-compat). For full control return:

```python
CallToolResult(content=[TextContent(type="text", text=...)], structuredContent={...}, _meta={...})
```

**Lifespan (DB/cache pools):** `@asynccontextmanager async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]` → `FastMCP(..., lifespan=app_lifespan)`; access via `ctx.request_context.lifespan_context`.

**Transports / run:**
```python
mcp.run()                              # stdio (default — local Claude Code)
mcp.run(transport="streamable-http")   # remote/team deployment
mcp.run(transport="sse")               # legacy compat only
```
**Mount under Starlette/ASGI** (so the HTTP API and MCP share a process):
```python
Mount("/mcp", app=mcp.streamable_http_app())   # within `async with mcp.session_manager.run():`
```

**Image return (screenshots/maps):** `from mcp.server.fastmcp import Image` — a tool may return `Image(...)`, serialized as an MCP image content block.

## 0.2 MCP — Tools result format (protocol)

**Source consulted:** https://modelcontextprotocol.io/specification/2025-06-18/server/tools (fetched 2026-06-02).

**Allowed content blocks in a tool result `content[]`:**
- `{"type":"text","text":...}`
- `{"type":"image","data":"<base64>","mimeType":"image/png"}` ← **how map/screenshot images reach the model**
- `{"type":"audio",...}` (unused)
- `{"type":"resource_link","uri":...,"name":...,"mimeType":...}` ← link big artifacts, don't inline
- `{"type":"resource","resource":{"uri":...,"text"/"blob":...}}` ← embedded resource

**Structured results:** `structuredContent` (JSON object) + optional `outputSchema` on the tool (server MUST conform; client SHOULD validate). Also serialize the JSON into a `TextContent` block for backwards-compat.

**Tool annotations** (use to mark side-effect tools, spec §16/NFR-SEC-010): `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. **Clients treat annotations as untrusted hints.**

**Pagination:** `tools/list` (and resource/prompt lists) accept `cursor`, return `nextCursor`. Apply the same cursor pattern to our own large result tools (NFR-PERF-009).

**Errors:** tool *execution* errors → result with `isError: true` (+ text). Protocol errors (unknown tool, bad args) → JSON-RPC `error`.

**Capability:** declare `tools.listChanged: true`; emit `notifications/tools/list_changed` when the tool set changes (used by hot-reload, Phase 2).

## 0.3 Parcel resolution — ULDK (GUGiK)

**Source consulted:** https://uldk.gugik.gov.pl/?lang=en + reference client https://github.com/envirosolutionspl/uldk_gugik (search 2026-06-02).

**Allowed endpoints:**
- By id: `https://uldk.gugik.gov.pl/?request=GetParcelById&id=141201_1.0001.1867/2`
- By point: `https://uldk.gugik.gov.pl/?request=GetParcelByXY&xy=630889.87,497178.59`  (default CRS **EPSG:2180** = PUWG1992; append `,<srid>` to change)
- Result fields: `&result=geom_wkt,teryt,parcel,region,commune,county,voivodeship`
- Response = plain text: **line 1 = status code, line 2 = data** (geometry default WKB; request `geom_wkt` for WKT).

Also available (verify at impl time): `GetParcelByIdOrNr`, `GetRegionById`, `GetCommuneById`, administrative-unit lookups — for TERYT/obręb normalization (F-0019/F-0020).

## 0.4 Spatial / GIS stack (Allowed libraries — verify exact versions at install)

| Concern | Library | Key API |
|---|---|---|
| CRS transform | `pyproj` | `Transformer.from_crs("EPSG:4326","EPSG:2180", always_xy=True)`; GeoPandas `gdf.to_crs(2180)` |
| Vector geometry | `shapely` 2.x | `intersection/union/difference/symmetric_difference`, `buffer`, `make_valid`, `voronoi_polygons`, `maximum_inscribed_circle` (GEOS) |
| Largest inscribed **rectangle** | **NOT built into shapely** | custom rotating + binary-search, or `largestinteriorrectangle` (raster-mask). See Phase 5 anti-pattern. |
| Medial axis / skeleton (narrow-plot) | `shapely.voronoi_polygons` or `centerline`/`scikit-geometry` | derive main axis (F-0034), narrowest passage (F-0032) |
| Dataframes / IO | `geopandas` + `fiona`/`pyogrio` | read SHP/GPKG/GeoJSON/KML; `to_file` for GPKG export |
| Rasters (NMT/NMPT/LiDAR) | `rasterio` (+ `numpy`) | `rasterio.sample`, windowed reads; slope/aspect via `numpy.gradient` or `richdem` |
| OGC services introspection | `owslib` | `WebMapService`, `WebFeatureService`, `WebCoverageService` → `GetCapabilities` (F-0081/F-0082) |
| Solar / shadow | `pvlib` (sun position) + custom/`pybdshadow` | sun path, shadow casting 2D/3D (§7.15, F-0343/0344) |
| DXF (CAD export) | `ezdxf` | F-0394 |
| Basemap tiles for rendering | `contextily` | static map background |
| HTTP connectors | `httpx` (async) | `AsyncClient`; retries via `tenacity`/`stamina`; circuit breaker via `pybreaker`/`purgatory` |
| DB / migrations | `SQLAlchemy` + `GeoAlchemy2` + `Alembic` | PostGIS geometry columns, GIST indexes (spec §26) |
| Schemas/validation | `pydantic` v2 + `jsonschema` | result schemas + ruleset YAML validation |
| Queue / workers | `Dramatiq` (or Celery/RQ) + Redis | async analyses, batch, cache warming |
| Object storage | `minio`/`boto3` (S3-compatible) | snapshots, artifacts |
| PDF report | `weasyprint` (HTML→PDF) | F-0391 |
| Hot-reload | `watchfiles`; `uvicorn`/`granian --reload` | Phase 2 |
| Screenshots (web map) | `playwright` (python) | `page.screenshot()` Phase 3 |
| Map render | `matplotlib` + `geopandas .plot()` | PNG (`savefig`) + SVG (`savefig(format="svg")`) |
| Observability | `opentelemetry-*`, `prometheus_client` | traces + metrics |

## 0.5 Anti-patterns to avoid system-wide (spec §21 + invented-API risks)

- ❌ Exposing all 561 F-features as separate MCP tools. Public surface ≈ the **21 tools in spec §10.3**; everything else is internal modules + **resources** (spec §8 intro, §10.1, §20.8).
- ❌ Returning huge GeoJSON inline. Use `resource_link`/embedded resources + pagination (NFR-PERF-009, §26.3, §21).
- ❌ Hardcoding legal/technical constants in code. All rules live in versioned `rulesets/PL/**/*.yaml` with `valid_from`/`source_reference` (§9.4, §12, §21).
- ❌ Treating an LLM parser result as ground truth. LLM extracts *candidates*; rules + JSON Schema + evidence validate (§20.11, NFR-AUD-005, §29).
- ❌ Letting one dead source fail the whole analysis. Always `source_unavailable` + partial result (NFR-REL-001/010).
- ❌ Conflating `unknown` vs `not_detected` vs `confirmed_absent` vs `source_unavailable` (§2.1, §21).
- ❌ Inventing FastMCP features (e.g., a built-in `reload=True`). Hot-reload is **external** (Phase 2). Confirm any decorator kwarg against §0.1.
- ❌ Assuming `shapely` has largest-inscribed-**rectangle** (it does not — §0.4).

---

# PHASE 1 — Foundation: monorepo, infra, domain models, schemas, DB

**Maps to:** spec §9.1 layout, Etap 0 (§19), data model §11, §26 (DB), §24 NFR scaffolding. F-0026, F-0446.

## 1.1 What to implement
1. **uv workspace monorepo** matching spec §9.1 (`base_assumptions.md:1208-1247`):
   `apps/{api,mcp-server,worker,web}`, `packages/{domain,geo,connectors,planning,rules,envelope,reports,evidence,agent,security,shared}`, `rulesets/PL/**`, `schemas/`, `tests/`, `infra/`.
2. **`packages/shared`**: config (pydantic-settings, env-only secrets — F-0477/0478), structured logging, OpenTelemetry + Prometheus init, error taxonomy enum (`input|source|ruleset|geometry|parser|system` — NFR-REL-009).
3. **`packages/domain`**: Pydantic v2 models for all §11.1 entities (`Parcel`, `InvestmentArea`, `AnalysisRun`, `SourceRecord`, `EvidenceItem`, `PlanningAct`, `PlanningZone`, `PlanningIndicator`, `Constraint`, `NoBuildZone`, `BuildableEnvelope`, `CapacityScenario`, `UtilityNetwork`, `RoadAccess`, `TerrainModel`, `RiskItem`, `UnknownItem`, `Recommendation`, `ReportArtifact`, `Ruleset`, `Override`). Enums for risk type/severity/confidence/status (§11.2). The canonical module return type `ModuleResult{result, evidence, confidence, warnings, unknowns}`.
4. **`schemas/`**: copy the JSON shapes verbatim into JSON Schema files — `analysis-result.schema.json` (§10.7), `analysis-input.schema.json` (§10.6), `risk-register.schema.json` (§11.2), `constraint.schema.json` (§11.3), `mcp-tools.schema.json`, `source-record.schema.json` (§5). Generate from Pydantic where possible; keep them as the contract.
5. **DB**: SQLAlchemy + GeoAlchemy2 models + Alembic migration creating the §26.1 tables and §26.2 GIST indexes. Geometry analytical CRS = **EPSG:2180** (§26.3).
6. **Infra**: `infra/docker-compose.yml` with PostGIS, Redis, MinIO (S3), and the app/worker. CI (lint+type+test) in `.github/`.

## 1.2 Documentation references
- Monorepo tree: `base_assumptions.md:1208-1247`. Data model: `:1450-1529`. DB DDL + indexes + geometry rules: `:2058-2094`. Result/input schemas: `:1389-1444`. NFR ids: `:1934-2004`.
- Pydantic v2 `BaseModel`/`Field(description=...)` for outputSchema quality (Phase 0.1). GeoAlchemy2 `Geometry("POLYGON", srid=2180)`.

## 1.3 Verification checklist
- `uv sync` resolves; `ruff`/`mypy` clean; `pytest -q` runs (even if only smoke tests).
- `alembic upgrade head` creates all §26.1 tables; `psql` shows the 8 GIST/btree indexes from §26.2 (grep migration for `USING GIST`).
- `docker compose up` brings PostGIS+Redis+MinIO healthy.
- Round-trip test: every `schemas/*.json` validates a hand-written example; Pydantic model ↔ JSON Schema agree.
- Grep guard: no secrets in repo — `git grep -nE "(API_KEY|SECRET|PASSWORD)\s*=" -- . ':!*.example'` returns nothing (F-0477).

## 1.4 Anti-pattern guards
- ❌ No `analyzeEverything()` god function (§20.7). Keep packages decoupled: connectors never import rules; rules never import connectors (§9.4).
- ❌ No geometry stored only in input CRS — analytical geom in EPSG:2180, input CRS kept as metadata (§26.3).
- ❌ No raster blobs in DB — object storage + tile index (§26.3).

---

# PHASE 2 — MCP server skeleton + HOT-RELOAD backbone

**Maps to:** §10 (MCP), F-0447–0462, F-0133/F-0440 (ruleset reload), meta-feature **hot-reload**. Etap 0 "MCP skeleton".

## 2.1 What to implement
1. **Thin FastMCP server** in `apps/mcp-server` exposing the **21 public tools** (§10.3) as typed stubs returning structured stubs, the **resource templates** (§10.4), and the **10 prompts** (§10.5). Copy decorator usage from **Phase 0.1**. Both transports: `mcp.run()` (stdio) and a Starlette mount for `streamable-http` (Phase 0.1 mount snippet). Declare `tools.listChanged: true`.
2. **Thin-proxy architecture for hot-reload** (this is the key design): the MCP process stays connected to Claude Code and **delegates domain work to the worker/API process** that runs under `uvicorn`/`granian --reload` (watchfiles). Editing domain code reloads the worker **without dropping the MCP session**. MCP ↔ worker over the shared use-cases (§27: API and MCP share use-cases) via in-proc import (dev) or HTTP (remote).
3. **Reloadable surfaces inside the MCP process** (things that must change live):
   - **Rulesets**: load `rulesets/PL/**/*.yaml` *fresh per analysis* (never cache across runs in dev); a `watchfiles` watcher bumps a `ruleset_version`/in-memory registry (F-0133, F-0440).
   - **Analysis modules / tool registry**: a `dev_reload` dev-tool (annotated `destructiveHint:false, readOnlyHint:false`) that `importlib.reload`s the domain registry, re-registers tools, then emits `notifications/tools/list_changed` (Phase 0.2) so Claude Code re-lists.
4. **`diagnostics_run`** (§10.3) tool: reports loaded ruleset versions, connector health stubs, reload status, last-reload timestamp (F-0442 self-diagnostics).
5. **Config flag** `DEV_HOT_RELOAD=true` gates file-watching + the `dev_reload` tool so production never exposes it.

## 2.2 Documentation references
- Public tool list: `base_assumptions.md:1304-1346`. Resources: `:1352-1370`. Prompts: `:1376-1385`. Transports: `:1296-1298`. MCP perf rules (no giant payloads, on-demand resources): `:1641`, NFR-PERF-009.
- FastMCP decorators/Context/run/mount/annotations: **Phase 0.1**. `listChanged`/`list_changed` notification: **Phase 0.2**. `watchfiles`/`--reload`: Phase 0.4.

## 2.3 Verification checklist
- `uv run mcp dev apps/mcp-server/server.py` opens MCP Inspector; `tools/list` shows exactly the 21 tools (+ `dev_reload` only when `DEV_HOT_RELOAD=true`); each tool has an `outputSchema`.
- Register server in Claude Code (`stdio`); `parcel_analyze` stub returns valid `analysis-result.schema.json`-shaped `structuredContent`.
- **Hot-reload proof:** edit a ruleset YAML value → next `ruleset_explain` call reflects it with **no MCP restart**. Edit a domain function → worker reloads (watch log) → result changes without restarting the MCP session.
- `dev_reload` triggers a `notifications/tools/list_changed` (observe in Inspector).
- Streamable-HTTP: `curl` the mounted `/mcp` endpoint negotiates a session.

## 2.4 Anti-pattern guards
- ❌ Don't expose internal F-features as tools (§21, §20.8). 21 public tools only; detail via resources.
- ❌ Don't try to reload the stdio MCP process *from within itself* — that kills the Claude Code pipe. Reload the **worker/rulesets/registry**, not the transport (Phase 0.5).
- ❌ Don't return whole maps/evidence inline — resources fetched on demand (§15.1, NFR-PERF-009).
- ❌ Don't ship `dev_reload`/file-watch in production builds (security; §16).

---

# PHASE 3 — Drawing & screenshot-verification engine (visual verification capability)

**Maps to:** meta-features **intelligent drawing (algorithmic)** + **screenshots for verification**. F-0038, F-0395, F-0396, F-0397, F-0416; NFR-AUD-009; §17 maps.

## 3.1 What to implement
1. **`packages/reports/render`** — deterministic map renderer: given parcel + layers (constraints, no-build, buildable envelope, networks, sun/shadow), render to **PNG and SVG** via `matplotlib` + `geopandas .plot()` (+ `contextily` basemap optional). Persist style metadata (CRS, layers, style params) alongside every image (NFR-AUD-009). Output to object storage; expose as `analysis://{id}/map-preview.png` (§10.4).
2. **MCP image return path**: a `report_generate`/preview tool returns the PNG as an MCP **image content block** (Phase 0.2) so **Claude Code sees the rendered map directly** — this is the "screenshot for verification" channel for static renders.
3. **Web-map screenshot path** (for `apps/web` interactive map, §10.4 map-preview / F-0397): `playwright` headless `page.screenshot()` of a Leaflet/MapLibre page → PNG → image content. Used when an interactive/zoomable check is needed.
4. **Golden-image snapshot testing**: a `tests/` harness that renders known parcels and compares against committed reference PNGs with a perceptual/pixel-diff tolerance (F-0546 snapshot tests, extended to maps).
5. **Render contract**: every drawing carries a legend that distinguishes hard vs soft constraints and shows *which constraint removed which area* (DoD envelope §30, "pokazuje, które ograniczenie odjęło jaką powierzchnię").

## 3.2 Documentation references
- Map/report requirements: `base_assumptions.md:1684-1721` (§17), `:2162-2175` (envelope DoD), map resource URIs `:1352-1370`.
- MCP image content block shape + base64/mimeType: **Phase 0.2**. `Image` helper: **Phase 0.1**. matplotlib/geopandas/contextily/playwright: **Phase 0.4**.

## 3.3 Verification checklist
- Render a golden parcel → PNG+SVG produced; opening the PNG via the MCP tool returns an `image` content block that renders in Claude Code.
- Snapshot test passes against committed reference; intentionally perturb geometry → snapshot test **fails** (proves it's real).
- Style metadata JSON saved next to each image includes CRS=EPSG:2180 + layer list (NFR-AUD-009).
- Playwright screenshot of the web map produces a non-blank PNG in CI (headless).

## 3.4 Anti-pattern guards
- ❌ Don't embed multi-MB PNGs as base64 in *every* result — return a `resource_link` by default and inline the image only on explicit preview/verify calls (NFR-PERF-009).
- ❌ Don't render from un-simplified analytical geometry for previews — use the simplified preview geometry (F-0514, §26.3) while keeping full precision for analysis.
- ❌ No nondeterministic styling (random colors/order) — golden-image tests require deterministic render.

---

# PHASE 4 — Self-improvement dev-loop harness (draw → screenshot → score → learn)

**Maps to:** meta-feature **self-improvement (agent dev-loop)** + the generative "draw like an architect & learn to draw" loop. F-0421–0446 (agent), F-0438 (agent memory), F-0206/F-0211 (sensitivity/efficiency), §14 scoring hooks.

## 4.1 What to implement
1. **Golden-case runner** (`packages/agent/selfimprove`): runs a named scenario (parcel + mode + investment goal) end-to-end, captures: structured result, scores (§14), rendered map (Phase 3), and a machine-readable diff vs the golden expectation.
2. **Score evaluator**: computes the §14 scores and a single "improved/regressed" verdict per change, so the agent loop has an objective signal.
3. **Edit → hot-reload → re-run automation**: a thin driver (a CLI + an MCP `diagnostics_run`/dev tool) that, after the agent edits code/rulesets, triggers Phase 2 reload, re-runs the affected golden cases, and returns `{before_scores, after_scores, screenshots, failures}` as structured content + image blocks. This is the loop Claude Code uses to build later phases.
4. **Generative architect-drawing loop** (the user's "model draws and learns to draw"):
   - **Propose**: the model emits a candidate building footprint / site layout as **GeoJSON or a constrained draw-DSL** (rectangles, setback offsets, parking bays, greenery) — *data, not free pixels* — into a `manual_override`-style typed input.
   - **Render & shoot**: server overlays the proposal on parcel + buildable envelope + constraints, renders PNG (Phase 3), returns it as image content so the model *sees its own drawing*.
   - **Score**: server computes buildability/capacity/PBC/parking/sun scores + constraint violations (§14, §8.4/§8.5) for the proposal.
   - **Critique & iterate**: structured critique (which constraint violated, which score is low) feeds the next proposal; loop until scores plateau or a budget is hit.
   - **Learn to draw**: accepted high-scoring proposals are stored in a **drawing-exemplar memory** (`packages/agent` store, keyed by parcel shape class / program type) and surfaced as few-shot exemplars in the drawing prompt for similar future parcels. (Optionally exported as a fine-tuning dataset — out of scope to train, in scope to *collect*.)
5. **Loop guardrails**: every proposal validated against hard constraints *before* scoring; the loop **cannot** mark a layout valid if it violates a hard blocker (§14.2 "hard blocker must dominate"); all iterations logged to the audit trail (F-0446).

## 4.2 Documentation references
- Agent/autonomy requirements: `base_assumptions.md:1046-1074` (§8.13), `:1645-1657` (§15.2). Scoring: `:1604-1626` (§14). Envelope/footprint variants the loop optimizes: `:1738-1779` (§8.4), capacity `:1781-1818` (§8.5). Sensitivity: F-0206/F-0211.
- MCP image content for the "see your drawing" step: **Phase 0.2**. Drawing renderer: **Phase 3**. Hot-reload trigger: **Phase 2**.

## 4.3 Verification checklist
- Run the harness on a golden parcel: it produces `{before,after}` scores + screenshots; intentionally worsen a ruleset → harness reports **regression**.
- Drawing loop demo: model proposes a footprint that violates a setback → server **rejects** it (hard constraint) and the score reflects the violation; a compliant proposal scores higher and is stored as an exemplar.
- Exemplar recall: a second similar parcel surfaces the stored exemplar in the drawing prompt context (grep the prompt assembly / log).
- Audit: every loop iteration appears in the audit log with inputs, scores, and the rendered artifact URI (F-0446).

## 4.4 Anti-pattern guards
- ❌ The drawing loop must **not** let the model "draw away" a hard blocker to raise a score (§14.2, §21 "twierdzenie że działka jest budowlana bo wygląda na budowlaną").
- ❌ Drawings are typed geometry/DSL validated by rules — **not** trusted free-form output (§20.11, NFR-SEC-003: document/model text must not steer the agent).
- ❌ Don't optimize scores by hiding unknowns — `unknowns` must persist regardless of score (§20.10).
- ❌ Self-improvement edits to **rulesets** are proposals with audit + valid_from; never silently mutate legal rules (§12, NFR-AUD-003).

---

# PHASE 5 — Geometry & spatial core

**Maps to:** §13 algorithms, F-0014–0040, §8.6 geometry parts. Etap 1 "geometry metrics".

## 5.1 What to implement
`packages/geo`:
1. CRS detection + transform to EPSG:2180; topology validation + `make_valid` repair; flag uncertain geometry (F-0014–0018).
2. Multipart detection, merge parcels → `InvestmentArea`, split back to components (F-0021–0023).
3. Metrics: area/perimeter/compactness/convexity/irregularity; frontage & corner detection; width profile + narrowest passage via medial axis; main axis & frontage azimuth; boundary-edge classification; usable area before/after constraints (F-0027–0036, §8.6).
4. **Largest inscribed rectangle / orthogonal polygon** (F-0159/0160) — custom algorithm (Phase 0.4 note). Input hash + snapshot (F-0026).
5. Overlay/buffer/clip engine wrappers (intersection/union/difference/sym-diff, metric buffers, dissolve, simplification with topology preservation, nearest/distance-to-layer, clip to parcel+analysis buffer) — §13.

## 5.2 Documentation references
- Algorithms list: `base_assumptions.md:1578-1600` (§13). Geometry F-features: `:592-631`. shapely/pyproj/geopandas APIs: **Phase 0.4**.

## 5.3 Verification checklist
- Property-based tests (F-0547): area/perimeter invariant under CRS round-trip; `make_valid` idempotent; buffer area monotonic in distance.
- Golden parcels (simple/narrow/corner/irregular/multipart) produce expected metric ranges (F-0541).
- CRS transform tests EPSG:4326↔2180 within tolerance (F-0548).
- Largest-rectangle returns a rectangle fully inside the polygon (containment assertion) for all golden shapes.

## 5.4 Anti-pattern guards
- ❌ Don't buffer in degrees — always metric CRS (§9.4). ❌ Don't assume shapely largest-rectangle exists (Phase 0.5).

---

# PHASE 6 — Connectors framework + core connectors

**Maps to:** §8.2 (F-0041–0096), §5 source ranking, §28 connector DoD, §6 source list. Etap 1 connectors.

## 6.1 What to implement
1. **Adapter interface** every connector implements: `fetch / normalize / snapshot / healthcheck` (§20.3). Shared base: httpx async client, `tenacity` retry+backoff, `pybreaker` circuit breaker, per-connector timeout budget, **egress allowlist + SSRF guard** (F-0488/0489), `owslib` GetCapabilities introspection + layer auto-discovery (F-0081/0082), bbox minimization (F-0083), cache (Redis + object-storage snapshots, F-0085–0089), license validation (F-0092), `SourceRecord`+`EvidenceItem` emission (§5 schema, §11), and `not_detected` vs `source_unavailable` distinction (F-0093/0094, NFR-REL-001).
2. **Core connectors (MVP set, spec §32 must-have):** ULDK (Phase 0.3), Geoportal WMS/WMTS/WFS/WCS + ortofoto + NMT/NMPT (F-0045–0052), BDOT10k (F-0054), GESUT/KIUT (F-0056), APP/GML + Planning-data viewer + local BIP/SIP (F-0057–0061), ISOK MZP/MRP/WORP (F-0064), GDOŚ/CRFOP (F-0066), PIG SOPO (F-0068), NID heritage (F-0071). PRG/TERYT/GUS (F-0043/0044).
3. **CI mocks + contract tests** for each (F-0095/0096), recorded fixtures.

## 6.2 Documentation references
- Connector F-features: `base_assumptions.md:633-690`. Source ranking + SourceRecord schema: `:148-176`. Start sources + URLs: `:180-221` (§6). Connector DoD: `:2126-2139` (§28). Adapter contract: `:1839` (§20.3). ULDK: **Phase 0.3**. owslib/httpx/tenacity/pybreaker: **Phase 0.4**.

## 6.3 Verification checklist
- Each connector: healthcheck test green against live or recorded capabilities; contract test on fixtures (F-0539); offline mode works from snapshots (§20.14).
- SSRF test: connector refuses a non-allowlisted host (F-0489).
- Kill a source (simulate timeout) → returns `source_unavailable`, circuit opens, analysis still produces partial result (NFR-REL-001/010).
- Every fetch writes a `SourceRecord` with `retrieved_at`, `legal_status`, `confidence` (§5).

## 6.4 Anti-pattern guards
- ❌ WMS pixels used as analytical truth (§21) — WMS is preview; analysis uses WFS/GML vectors or WCS rasters with precision flags.
- ❌ Missing data interpreted as "no constraint" (§2.1, §21). ❌ No snapshot/metadata on fetch (§20.13).

---

# PHASE 7 — Overlay/constraints engine + quick_screening + red flags + buildable envelope v1 → **MVP GATE**

**Maps to:** Etap 1 + Etap 3 v1; §4.1 quick_screening; §7.5 envelope; §7.19 risks; §18.2 MVP acceptance; §32 must-have.

## 7.1 What to implement
1. **Overlay engine**: intersect parcel with all fetched risk layers (flood/protected/landslide/heritage/utilities/roads/watercourses/forest), compute buffers per layer type, derive `NoBuildZone`s and `Constraint` records with area attribution (which constraint removed which area).
2. **Buildable envelope v1**: parcel − setbacks − no-build zones → buildable polygon + largest inscribed rectangle (Phase 5), with confidence ranked by source type (§7.5).
3. **Red-flag classifier + decision**: `OK | OK_WITH_RISKS | NEEDS_MANUAL_REVIEW | LIKELY_BLOCKED` (§4.1, §10.7). Hard-blocker dominance (§14.2).
4. **Wire real logic** into MCP `parcel_resolve`, `parcel_analyze` (quick_screening), `analysis_get_status`, `analysis_get_result`, `report_generate`, `risks_list`, `sources_collect` — using the §27 shared use-cases.
5. **Markdown + JSON report** (§22 template) + map preview (Phase 3). Cache + partial results + `manual_review_required` as a normal status (§20.12).

## 7.2 Documentation references
- MVP acceptance criteria: `base_assumptions.md:1746-1760` (§18.2); must-have order `:2200-2212` (§32). quick_screening: `:78-90`. Envelope §7.5: `:307-325`. Risk/decision: `:568-584`, result schema `:1417-1444`. Report template: `:1874-1924` (§22).

## 7.3 Verification checklist (this is the MVP gate)
- From a parcel id **or** point → geometry + admin context fetched; geometry metrics computed; planning coverage reported; flood/protected/landslide/heritage/utilities/roads checked; envelope v1 produced; result returns red flags, unknowns, next actions, evidence (§18.2).
- MCP `parcel_analyze` + `analysis_get_result` + `report_generate` work end-to-end in Claude Code; Markdown & JSON reports stable (snapshot test F-0546).
- Every claim has a source or an explicit `no_source` mark (NFR-AUD-001).
- Kill one connector → partial result, not total failure (§18.2 last bullet).
- Run via the **Phase 4 self-improve loop** with a screenshot of the envelope map verified by the model.

## 7.4 Anti-pattern guards
- ❌ Summing scores past a hard blocker (§14.2). ❌ Hiding `unknown`/no-data (§21). ❌ Inline giant envelope GeoJSON — expose as `analysis://{id}/buildable-envelope.geojson` resource (§10.4, NFR-PERF-009).

---

# PHASE 8 — Planning intelligence (MPZP/POG/WZ parsing + ruleset engine)

**Maps to:** Etap 2; §8.3 (F-0097–0139); §7.3/7.4; §12 rules; §29 parser DoD; §25 conflicts.

## 8.1 What to implement
1. **APP/GML pipeline**: fetch + validate APP/GML, intersect planning zones with parcel, recognize terrain symbols (F-0108–0111).
2. **Document parsers** (PDF/HTML/DOCX/text) for MPZP resolution, general & detailed provisions, local definitions, parking indicators, building lines, prohibitions, roof/material rules (F-0112–0120). **LLM extracts candidates → JSON Schema validation → evidence trace (source fragment per provision) → rules validate.** Runs in **untrusted-content mode** (prompt-injection defense, NFR-SEC-002/003).
3. **Use matrix** (allowed/conditional/forbidden), conflict detection drawing-vs-text and GML-vs-PDF (F-0121–0125), planning stability score, citation trace (F-0126/0127).
4. **Ruleset engine** (`packages/rules`): declarative, versioned YAML rules (§12.2 format) with `pass/fail/warning/unknown/not_applicable` + trace; categories §12.3 (building-technical/planning/road-access/utilities/environmental/water/geology/heritage/reporting). JSON-Schema-validated. Strict/conservative/optimistic modes + expert override with audit (F-0134–0138).
5. **MCP**: `planning_fetch`, `planning_parse_document`, `ruleset_explain`, `manual_override`.

## 8.2 Documentation references
- Planning F-features: `base_assumptions.md:692-739` (§8.3). Parser DoD: `:2143-2158` (§29). Rule format/categories: `:1533-1574` (§12). Conflicts model: `:2033-2050` (§25). Confidence components: `:2008-2031` (§25.1). Untrusted content: NFR-SEC §`:1980-1991`.

## 8.3 Verification checklist
- Parser evaluation set with precision/recall per parameter (F-0549); LLM-hallucination tests (F-0550); every provision cites a source fragment (NFR-AUD-002/005).
- Ruleset regression suite (F-0559); rule edit reflected live via hot-reload (Phase 2) without code change (§18.3).
- Conflict case (GML vs PDF) → reported as conflict + `manual_review_required`, not silently resolved (§25.2).

## 8.4 Anti-pattern guards
- ❌ LLM output as binding law (§20.11). ❌ Rules hardcoded in code or missing `valid_from`/source (§21, §12.1). ❌ Document text steering the agent (NFR-SEC-003).

---

# PHASE 9 — Buildable envelope advanced + capacity + generative drawing variants

**Maps to:** Etap 3; §8.4 (F-0140–0179); §8.5 (F-0180–0215); §30 envelope DoD; ties to Phase 4 drawing loop.

## 9.1 What to implement
1. **Full setback/buffer composition** combining statutory + planning + technical buffers (roads/rail/forest/watercourses/networks/power lines/protection zones/heritage/scarps/levees) with hard vs soft distinction + area attribution + spatial-op trace (F-0140–0157, §30).
2. **Footprint & layout variants** (F-0158–0179): single/semi-detached/terraced/multifamily/services/warehouse/garage; parking, walkways, fire access, waste, retention, greenery, playground layouts; privacy/views analysis; **ranking** — driven by the **Phase 4 generative drawing loop** (model proposes, renders, scores, iterates, learns).
3. **Capacity** (§8.5): max footprint/GFA, PUM/PUU, min PBC, parking demand, intensity limits, area balances; program-fit per building type; conservative/base/optimistic/max variants + sensitivity analysis (F-0206); subdivision/assembly/adjacent-buy potential (F-0207–0209); envelope-utilization & efficiency comparison.
4. **Exports**: GeoJSON/GPKG/DXF of envelopes & variants (`export_layers`, F-0179, ezdxf/geopandas).

## 9.2 Documentation references
- Envelope/variants F: `base_assumptions.md:738-779` (§8.4). Capacity F: `:781-818` (§8.5). Envelope DoD: `:2162-2175` (§30). Drawing loop: **Phase 4**. ezdxf/geopandas: Phase 0.4.

## 9.3 Verification checklist
- Envelope DoD §30: works on single/multi parcels; shows which constraint removed which area; hard/soft distinction; geometric + textual output; largest rectangles/variants; spatial-op trace; tests on simple/narrow/corner/irregular/multipart; GeoJSON/GPKG/DXF export.
- Capacity scenarios reproduce expected ranges on golden MPZP cases (F-0542); sensitivity analysis present for ambiguous indicators.
- Drawing loop raises capacity/buildability scores across iterations on a demo parcel; exemplars stored (Phase 4 verification reused).

## 9.4 Anti-pattern guards
- ❌ One source of building lines treated as certain — multiple sources w/ confidence (§7.5, §30). ❌ Vectorized PDF lines treated as binding — low-confidence flag (F-0145).

---

# PHASE 10 — Terrain/water/geology + environment/heritage + roads/utilities + neighborhood/sun + scoring

**Maps to:** §7.7–7.16; §8.6–8.10 (F-0216–0362); §14 scoring; §13 raster/sun algorithms.

## 10.1 What to implement
1. **Terrain** (§8.8): DEM/NMT/NMPT/LiDAR sampling; slope/aspect/contours/elevation profiles; depressions & flow direction; earthworks & retaining-wall risk; basement/underground-parking precheck; terrain diagnostic maps (F-0271–0303).
2. **Water/flood**: ISOK MZP/MRP/WORP overlay; watercourse/ditch/reservoir distances; levees; water-law precheck; retention/infiltration class; GZWP & intake zones (F-0288–0302).
3. **Geology**: SOPO landslides, mining areas/deposits (MIDAS/ROG), CBDG context, geotechnical risk score + investigation brief (F-0280–0286, F-0303).
4. **Environment/heritage** (§8.9): Natura2000/parks/reserves/OChK/monuments/habitats/corridors; EIA screening by investment type; tree detection (orthophoto/LiDAR) precheck; tree-removal precheck; NID register/GEZ/conservation & archaeology zones; demolition/material/roof/height constraints; airfield/HV/wind/cemetery/industrial nuisance (F-0304–0334).
5. **Roads/utilities** (§8.6/§8.7): public/internal road adjacency + access chain + servitude; frontage length; road class/authority; driveway feasibility & refusal risk; fire/turning/garbage/delivery access; road reserves/widening collisions; network detection & crossings; technical zones; collisions with foundations/access/trees; nearest-network distances; connection-risk scoring; gestor/road-authority questionnaires (F-0216–0270).
6. **Neighborhood/sun** (§8.10): nearby buildings + heights from LiDAR/NMPT; neighbor building lines; good-neighborhood WZ precheck; windows-at-boundary; shading to/from neighbors; sun path & shadow scenarios (`pvlib` + shadow casting); PV/garden orientation; privacy/views; noise/odor/dust; heat-island/ventilation/prevailing winds; existing greenery; historical terrain change (F-0335–0362).
7. **Scoring engine** (§14): all §14.1 scores, explainable (positive/negative factors), confidence-weighted, hard-blocker dominance (§14.2).

## 10.2 Documentation references
- §7.7–7.16: `base_assumptions.md:345-531`. §8.6–8.10 F-features: `:820-981`. Raster/sun algorithms: `:1578-1600` (§13). Scoring: `:1604-1626` (§14). rasterio/pvlib/owslib: Phase 0.4.

## 10.3 Verification checklist
- Golden flood/protected/heritage cases pass (F-0543/0544/0545); slope/aspect validated against a known DEM tile; sun path matches `pvlib` reference for a date/lat/lon.
- Each score lists its positive/negative factors + confidence (NFR-AUD-006); hard blocker forces decision regardless of other scores (§14.2).
- Road access chain resolves servitude/internal-road paths on a golden case.

## 10.4 Anti-pattern guards
- ❌ Sample full county rasters — bbox+buffer only (NFR-PERF-007). ❌ Auxiliary data (OSM) treated as binding (§21). ❌ 3D shadow run unconditionally — gate behind detected need (NFR-PERF-013).

---

# PHASE 11 — Autonomy, orchestration, batch, monitoring

**Maps to:** Etap 4; §8.13 (F-0421–0446); §4.4 portfolio; §4.5 monitoring; §15.2 autonomy; workers.

## 11.1 What to implement
1. **Task graph + dependency graph** orchestrator (`packages/agent`): autonomous analysis plan, source selection by location, fallback between sources, result degradation on failure, partial results, manual-review gates, agent memory per analysis, idempotent tools, cache-aware planning, chunking, streaming status (F-0421–0438).
2. **Freshness monitors**: source + ruleset freshness; connector autotests; self-diagnostics; safe-failure & conservative defaults; audit trail (F-0439–0446).
3. **Workers + queue** (`apps/worker`, Dramatiq+Redis): async/resumable `full_due_diligence`; `portfolio_batch` (§4.4) with dedup + cache warming + ranking + portfolio map/table; `monitoring_changes` (§4.5) with change alerts + snapshot archive + webhook (F-0418).
4. **MCP**: `portfolio_analyze`, `monitoring_create`, `cache_warm`, `analysis_get_status` streaming progress (`ctx.report_progress`, Phase 0.1).

## 11.2 Documentation references
- §8.13: `base_assumptions.md:1046-1074`. §15.2 autonomy: `:1645-1657`. Portfolio §4.4 `:121-133`; monitoring §4.5 `:135-144`. Perf/queue/backpressure: `:1936-1952` (§24.1). Dramatiq/Redis: Phase 0.4.

## 11.3 Verification checklist
- Portfolio batch: one bad parcel does not stop the portfolio (NFR-REL-008); ranking + CSV/XLSX/GPKG/JSON export produced (F-0419).
- Resumable full analysis: kill mid-run → resume from snapshot; idempotent re-run gives identical result (NFR-REL-002/003).
- Progress notifications visible in Claude Code during a long analysis.
- Monitoring detects a simulated MPZP change and emits an alert + diff.

## 11.4 Anti-pattern guards
- ❌ Blocking full analysis on one unavailable service (§21). ❌ Random pick on source conflict — report it (NFR-REL-007). ❌ No backpressure → hammering public services (NFR-PERF-014).

---

# PHASE 12 — Reports/exports (full), HTTP API, SDKs, security & perf hardening

**Maps to:** Etap 5; §8.12 (F-0389–0420); §8.14 (F-0447–0476); §8.15 security (F-0477–0503); §8.16 perf (F-0504–0536); §16, §17, §24, §27.

## 12.1 What to implement
1. **Reports**: all formats MD/HTML/PDF/JSON/GPKG/DXF/SVG/PNG from one report model (PDF/HTML rendered from the same model — §31 DoD); audience variants (architect/investor/lawyer/bank); executive summary; evidence pack downloadable independently; report versioning + comparison; share link + map annotations (F-0389–0420, §17, §31).
2. **HTTP API** (`apps/api`, FastAPI + OpenAPI) implementing §27 endpoints, **sharing the same domain use-cases as MCP** (§27 last line). CLI + Python/TS SDK clients (F-0463–0466). Docker image, compose, optional k8s/terraform, PostGIS migrations, background workers, object storage, MQ, health/metrics endpoints (F-0467–0476).
3. **Security** (§16, §24.4): SSRF + egress allowlist, upload sandbox + AV + size/type/page limits, path-traversal guard, prompt-injection defense (untrusted HTML/PDF/BIP), tool/command allowlist, RBAC + tenant isolation, audit log, encryption at rest/in transit, PII redaction in shared reports, secret management, signed artifacts, SBOM, license & supply-chain scanning (F-0477–0503).
4. **Performance** (§15.1, §24.1): PostGIS spatial indexes, materialized overlays, precomputed municipal caches, vector/raster tile caches, in-memory LRU, parallel/async IO, batching, bbox minimization, geometry simplification, multi-resolution geometry, timeout/error budgets, circuit breakers, partial results, observability traces (F-0504–0536).

## 12.2 Documentation references
- Reports F: `base_assumptions.md:1011-1044` (§8.12); report DoD `:2179-2194` (§31); §17 `:1684-1721`. Dev/API F: `:1075-1106` (§8.14); HTTP API `:2100-2120` (§27). Security: `:1108-1136` (§8.15), `:1660-1681` (§16), `:1980-1991` (§24.4). Perf: `:1138-1172` (§8.16), `:1936-1952` (§24.1).

## 12.3 Verification checklist
- Report DoD §31: PDF/HTML/MD/JSON consistent numbers; reproducible from snapshot; evidence pack downloadable; explicit disclaimers present.
- API ⇄ MCP parity test: same input → same domain result via both surfaces (§27).
- Security tests: SSRF blocked, path traversal blocked, oversized/forbidden upload rejected, prompt-injection corpus does not alter agent behavior (F-0555/0556, NFR-SEC).
- Perf benchmarks: quick_screening under budget on golden parcels; spatial queries use GIST (EXPLAIN ANALYZE shows index scan) (NFR-PERF-004/015).

## 12.4 Anti-pattern guards
- ❌ Logic drift between API and MCP — both call shared use-cases (§27). ❌ Secrets in logs/reports/artifacts (NFR-SEC-006). ❌ Write-tools without side-effect annotation + audit (NFR-SEC-010).

---

# PHASE 13 — Test corpora, calibration, acceptance & deployment

**Maps to:** Etap 5; §18 tests/acceptance; §8.17 (F-0537–0561); §24 NFRs; §25 confidence calibration; §18.3 full-version criteria.

## 13.1 What to implement
1. **Full test matrix** (F-0537–0561): unit (geometry/CRS/rules/scoring), connector contract, PostGIS integration, golden parcels/MPZP/flood/protected/heritage, report snapshots, property-based geometry, parser eval set + hallucination tests, evidence-completeness, **confidence calibration tests** (§25.1), perf benchmarks, load tests, security tests, upload fuzzing, MCP protocol tests, API schema tests, ruleset regression suite, manual-expert-review set, acceptance-by-use-case.
2. **Confidence calibration** (§25): implement `confidence_components` (source_authority/freshness/geometry_precision/semantic_precision/parser_confidence/cross_source_agreement/ruleset_certainty/manual_verification) + the policy thresholds; calibrate against the manual-review set.
3. **Deployment**: k8s/terraform, runbooks, ruleset update workflow (no code change — §18.3), offline-snapshot mode (§20.14), docs.

## 13.2 Documentation references
- Tests F: `base_assumptions.md:1174-1200` (§8.17); §18 acceptance `:1725-1775`; full-version criteria `:1762-1775` (§18.3); confidence model `:2008-2031` (§25); DoD connector/parser/envelope/report `:2126-2194`.

## 13.3 Verification checklist (full-version acceptance, §18.3)
- Full MPZP/POG/WZ analysis with parsers + evidence works; advanced envelope works; investment scoring works; batch portfolio works; change monitoring works; GIS/CAD export works.
- Rulesets versioned, tested, updatable without code change; analysis reproducible & auditable; sources cached/healthchecked/versioned.
- Confidence calibration within target on the manual-review set; thresholds (§25.1) applied in reports.
- System usable by Claude Code in a real design workflow (end-to-end MCP session demo).

## 13.4 Anti-pattern guards
- ❌ Shipping without golden corpora / calibration (§18). ❌ Ruleset changes requiring code edits (§18.3). ❌ Removing uncertainty to please a simple-answer expectation (§20.10).

---

# FINAL PHASE — Cross-cutting verification (run after each milestone and at the end)

1. **Doc conformance:** every MCP usage matches Phase 0.1/0.2 (no invented decorators/kwargs); ULDK calls match Phase 0.3; spatial calls match Phase 0.4.
2. **Anti-pattern greps:**
   - `git grep -nE "(GetParcelBy|uldk\.gugik)"` → only in the ULDK connector.
   - Tool count: `tools/list` returns **22 public tools** + dev-gated tools. Baseline = the 20 of §10.3; the meta-features added two stable, well-described public tools — `map_preview` (Phase 3, inline-image verification channel) and `propose_layout` (Phase 4, generative design-feasibility channel). Dev-only (gated by `dev_hot_reload`): `dev_reload`, `selfimprove_run`. So `len(tools)` = 22 (prod) / 24 (dev). This is still a "minimal, stable, well-described" surface (§10.1) — guards §21 "hundreds of tools". Phases 5–13 must NOT add public tools; they implement the real logic behind the existing 22.
   - `git grep -nE "valid_from"` present in every `rulesets/PL/**/*.yaml` (no hardcoded constants without dates).
   - `git grep -niE "TODO|FIXME|HACK"` triaged.
   - No `degrees`/EPSG:4326 buffering in `packages/geo` (metric-CRS guard).
3. **Run full suite** (Phase 13) + perf benchmarks + MCP protocol tests; all green.
4. **Self-improve loop end-to-end** (Phase 4): pick a real golden parcel, let the draw→screenshot→score→learn loop run, confirm scores improve and an exemplar is stored, with full audit trail.
5. **MVP gate (§18.2)** and **full gate (§18.3)** checklists both pass.

---

## Phase ↔ spec roadmap mapping (quick index)

| Plan phase | Spec roadmap (§19) | Spec sections / F-ranges | Meta-feature |
|---|---|---|---|
| 1 Foundation | Etap 0 | §9, §11, §26 / F-0026,0446 | — |
| 2 MCP + hot-reload | Etap 0 | §10 / F-0447–0462,0133,0440 | **hot-reload** |
| 3 Drawing/screenshot | (cross-cut) | §17 / F-0395–0397,0416 | **screenshots, algorithmic drawing** |
| 4 Self-improve loop | (cross-cut) | §8.13,§14 / F-0421–0446 | **self-improvement + generative drawing** |
| 5 Geometry core | Etap 1 | §13 / F-0014–0040 | — |
| 6 Connectors | Etap 1 | §5,§6,§28 / F-0041–0096 | — |
| 7 Overlay+envelope v1 | Etap 1/3 | §4.1,§7.5,§7.19,§18.2 / §32 must | **MVP gate** |
| 8 Planning intelligence | Etap 2 | §8.3,§12,§25,§29 / F-0097–0139 | — |
| 9 Envelope+capacity | Etap 3 | §8.4,§8.5,§30 / F-0140–0215 | uses drawing loop |
| 10 Terrain/env/roads/sun | Etap 3 | §7.7–7.16,§8.6–8.10,§14 / F-0216–0362 | — |
| 11 Autonomy/batch/monitor | Etap 4 | §8.13,§4.4,§4.5,§15.2 / F-0421–0446 | — |
| 12 Reports/API/SDK/security/perf | Etap 5 | §8.12,§8.14–8.16,§16,§17,§24,§27 / F-0389–0536 | — |
| 13 Tests/calibration/deploy | Etap 5 | §8.17,§18,§24,§25 / F-0537–0561 | — |

## Suggested execution

Run with the `do` skill, one phase per fresh context, in order. **Phases 1→4 first** (they build the dev-loop the rest is built with), then 5→7 to hit the **MVP gate (§18.2)**, then 8→13 for the **full version (§18.3)**. After Phase 4, use the self-improve loop (edit → hot-reload → golden tests + screenshots → score) as the default workflow for every later phase.
