# IMPLEMENTATION_PLAN_V2.md — From MVP to architect-grade masterplans (chłonność → koncepcja → PB/PW)

**Plan version:** 2.0
**Date:** 2026-06-10
**Predecessor:** [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) v1.0 — Phases 1–7 **DONE** (MVP gate, commit `f4b4070`). This plan **supersedes the ordering and scope of v1 Phases 8–13** and continues the numbering (Phases 8–16). v1 Phase 0 "Allowed APIs" remains binding; §0v2 below extends it.
**Target (user-locked):**

- The model (Claude Opus/Fable-class) acts as a **real architect making design decisions** through the MCP server: site analysis → design brief → composition (parti) → massing iterations with visual feedback → chłonność (capacity) numbers → final concept deliverable.
- Output quality bar: an **irregular, plot- and MPZP-adapted multi-building masterplan** of the kind produced by development teams (reference exemplar: ROBYG "Elektrownia Garbary" plan zagospodarowania terenu — ~14 buildings incl. heritage, internal roads, courtyards with playgrounds, per-building floors/PUM/PUU, 8 construction stages, totals table: 1257 mieszkań / 63 713 PUM / 15 576 PUU).
- Growth direction: data model and exports must extend cleanly toward **projekt budowlany** (PZT first), later **projekt wykonawczy** — never block that path.

> Orchestration contract for the `do` skill. One phase per fresh context. Every phase: read its doc references, copy from them, run its verification checklist. After every phase run the Phase 4 self-improve loop (edit → hot-reload → golden tests + screenshots → score) as the working method.

---

## Target workflow — "the model as architect" (what all phases serve)

This is the end-to-end MCP session the finished tool must support. Phases below are ordered to light these steps up incrementally:

1. **Site analysis** — `parcel_resolve` → `parcel_analyze(full)` → constraints, envelope, terrain/flood/heritage context, `map_preview`. Model reads the plot like an architect: frontages, orientation, noise edges (railway/road), water edges, heritage objects to keep.
2. **Planning frame** — `planning_fetch` + `planning_parse_document` → MPZP/POG/WZ indicators (intensywność, wysokość, PBC, miejsca postojowe/mieszkanie, linie zabudowy, % zabudowy) with citations.
3. **Numeric chłonność first** — `capacity_generate_scenarios` → max GFA/PUM/PUU/parking from indicators *before any drawing* (conservative/base/optimistic/max + sensitivity).
4. **Design brief** — `analysis://{id}/design-brief` resource: composition axes, buildable zones, recommended typologies, hard rules in force. The model writes its **design rationale** (why this orientation, why this typology) — recorded, auditable.
5. **Massing iterations** — `propose_layout` with the **masterplan DSL v2** (multiple buildings, segments, floors, uses, roads, parking, courtyards, stages) → server renders the plan, validates **inter-building rules** (WT §13/§60/§12/§19/§40, ppoż §271–273 + droga pożarowa), scores, returns image + structured critique citing rule IDs → model iterates.
6. **Deliverable** — `report_generate` → plan zagospodarowania render (legend: istniejące/zrealizowane/w budowie/projektowane) + per-building & per-stage PUM/PUU table + design rationale; `export_layers` → GeoJSON/GPKG/DXF/IFC.
7. **PB direction** — same model deepens into PZT (zestawienie powierzchni, sieci, ppoż section) per the projekt budowlany regulation.

**Tool-surface rule (carries over from v1 Final Phase):** the 22 public tools are **frozen**. Phases 8–16 implement real logic behind existing tools and extend their input/output schemas (versioned), resources and prompts — they do NOT add public tools. Dev-gated tools (`dev_reload`, `selfimprove_run`) stay dev-only.

---

# PHASE 0v2 — Documentation Discovery addendum (verified 2026-06-10)

Research already performed (repo inventory subagent + legal/API research subagent). Treat as authoritative; items flagged ⚠️ must be re-verified against the primary source before encoding into rulesets.

## 0v2.1 Current code anchors (copy-from locations)

| What | Where (file:line) |
|---|---|
| MCP tool registration, all 22+2 tools | `apps/mcp-server/plot_mcp_server/server.py:129-454` |
| `propose_layout` tool (image + structured return) | `apps/mcp-server/plot_mcp_server/server.py:369-401` |
| Drawing DSL `LayoutProposal` (program_type, footprint/rectangles, floors, parking_count, greenery) | `packages/agent/plot_agent/drawing/proposal.py:69-115` |
| `PlacedRectangle` draw-DSL (x,y,w,h,rotation,setback → shapely) | `packages/agent/plot_agent/drawing/proposal.py:35-66` |
| Exemplar memory + `shape_class_for` | `packages/agent/plot_agent/drawing/proposal.py:117-147` |
| Drawing loop iterate/critique/audit | `packages/agent/plot_agent/drawing/loop.py` |
| `ProposalScore` + hard-blocker dominance | `packages/agent/plot_agent/drawing/score.py:42-161` |
| `ScoreEvaluator` + `PHASE10_SCORE_HOOKS` (unknown-score pattern) | `packages/agent/plot_agent/selfimprove/evaluator.py:31-210` |
| `buildable_envelope_v1` (difference trace, LIR, confidence §7.5) | `packages/envelope/plot_envelope/envelope.py:82-163` |
| Renderer `Layer`/`LayerRole` (deterministic styles, area attribution) | `packages/reports/plot_reports/render/layer.py` |
| Ruleset loader (fresh-per-call, SHA-version) | `packages/rules/plot_rules/loader.py` |
| Connector ABC + profiles | `packages/connectors/plot_connectors/base/connector.py`, `profiles/PL.py` |
| Domain models incl. `CapacityScenario` (single-footprint today) | `packages/domain/plot_domain/models.py` |
| Ruleset exemplars (format to copy) | `rulesets/PL/planning/mn-coverage.yaml`, `rulesets/PL/building-technical/setback-granica.yaml` |

## 0v2.2 Legal sources (Polish law, state 2026-06-10) — encode as rulesets with these citations

**WT** = Rozporządzenie MI z 12.04.2002 w sprawie warunków technicznych… t.j. **Dz.U. 2022 poz. 1225**, zm. **Dz.U. 2023 poz. 2442** (anti-patodeweloperka, in force 2024-08-01 per Dz.U. 2024 poz. 474).

- **§12 setbacks:** 4 m (ściana z oknami/drzwiami), 3 m (bez), **5 m for budynek wielorodzinny >4 kondygnacje nadziemne — both cases** (boundary with neighboring działka budowlana); §12 ust. 1a relaxation does NOT apply to >4-kond. multifamily; each wall plane after załamanie/uskok = separate wall.
- **§13 przesłanianie:** 60° angle from window axis; no obstruction closer than `wysokość przesłaniania` (obstruction ≤35 m tall) or 35 m (taller); narrow objects ≤3 m wide → ≥10 m; zabudowa śródmiejska → up to ½. ⚠️ verify literal ust. 2–4 wording in ISAP PDF before encoding.
- **§60 nasłonecznienie:** pokoje mieszkalne ≥3 h on równonoc, window 7:00–17:00; multi-room dwelling: ≥1 room compliant; śródmiejska 1.5 h; **single-room dwelling in śródmiejska: no minimum**; żłobek/przedszkole/szkoła rooms: 3 h, 8:00–16:00.
- **§19 parking distances** (from windows of pomieszczenia przeznaczone na stały pobyt ludzi & placów zabaw): cars 7 m (≤10 mp), 10 m (11–60), 20 m (>60); from boundary 3/6/16 m; disabled spaces near windows allowed, max 6% of total. §21: stanowisko 2.5×5 m, disabled 3.6×5 m.
- **§39 PBC:** ≥25% działki for zabudowa wielorodzinna unless MPZP says otherwise; 2024: publicly accessible plac >1000 m² → ≥20% PBC.
- **§40 plac zabaw (2024):** trigger >20 mieszkań (building or zespół); area 1 m²/mieszkanie (21–50), 50 m² (51–100), 0.5 m²/mieszkanie (101–300), 200 m² (>300); splittable into parts ≥50 m²; ≥30% on PBC; sunlight ≥2 h równonoc 10:00–16:00 (śródmiejska 1 h); ≥10 m from linia rozgraniczająca ulicy / windows / śmietnik.
- **§271–273 ppoż:** ZL↔ZL base **8 m** (PM by Q: 8/15/20 m); +50/+100% for fire-spreading walls/roof or low fire-rated wall fraction; −25/−50% for sprinklers; **§272: to unbuilt neighbor boundary ≥½ distance** (4 m for ZL); §273: same-plot buildings may be treated jointly within strefa pożarowa limit.
- **Droga pożarowa** = Rozporządzenie MSWiA 24.07.2009, **Dz.U. 2009 nr 124 poz. 1030**: required for ZL III/IV/V in budynek średniowysoki+ (>12 m; multifamily = ZL IV); runs along longer side (both sides if shorter side >60 m); edge **5–15 m from wall**; width **≥4 m**; axle 100 kN; outer curve radius ≥11 m; dojście ≤50 m / ≥1.5 m; dead-end → plac manewrowy 20×20 m; przejazd ≥4.2 m h / ≥3.6 m w.
- **PUM:** PN-ISO **9836:2022-07** (replaced 2015-12, editorial diff). Sloped ceilings (PB reg §20 ust.1 pkt 4 lit. b): ≥2.20 m → 100%, 1.40–2.20 → 50%, <1.40 → 0%. ⚠️ verify which edition the PB-regulation załącznik names. Industry heuristic (NOT law, config not ruleset): PUM ≈ 0.70 × powierzchnia całkowita nadziemna (±5 p.p. for optimized plans).
- **Parking counts:** no national norm — **always from MPZP/WZ**; typical 1–2 mp/mieszkanie or per 60 m² PUM; usługi per 100/1000 m². Ruleset must mark the value `source: planning_act` with `unknown` default, never a hardcoded national number.
- **Projekt budowlany** = Rozporządzenie MR 11.09.2020, **Dz.U. 2020 poz. 1609, t.j. 2022 poz. 1679**: parts **PZT (§13–18) / PAB (§19–21) / PT (§22–24)**; PZT opisowa must contain m.in. projektowane zagospodarowanie, **zestawienie powierzchni** (zabudowa, drogi, PBC), ochrona ppoż incl. drogi pożarowe, obszar oddziaływania; electronic form: PDF, vector drawings, ≤150 MB/file, file-naming codes (PZT/PAB/PT + rrrr.mm.dd), qualified/trusted signature.
- **Planning reform status:** plan ogólny deadline formally **2026-06-30**, extension to 31.08.2026 passed by Sejm 2026-04-30, ⚠️ unsigned as of 2026-06-10 — connector/ruleset must treat the deadline as **data, not constant**, re-check at execution. POG/MPZP carriers: APP **GML, XSD v2.0** (Dz.U. 2023 poz. 2409), schemas at gov.pl/web/zagospodarowanieprzestrzenne/schematy-aplikacyjne. WZ: art. 64c 5-year expiry for decisions final after 2026-01-01.

## 0v2.3 Allowed libraries addendum (verified on PyPI 2026-06)

| Need | Package | Status |
|---|---|---|
| Largest interior rectangle | `largestinteriorrectangle` 0.2.1 | raster-mask based — already the v1 Phase 5 approach |
| Max inscribed circle | `shapely` ≥2.1 `shapely.maximum_inscribed_circle` | returns 2-point LineString center→boundary |
| Straight skeleton (composition axes, courtyard offsets) | `ladybug-geometry-polyskel` (pure-Python, **AGPL-3.0** ⚠️ license review) or `py-straight-skeleton` 0.1.0 (minimal deps) | `polyskel` and `scikit-geometry` are NOT on PyPI — do not `uv add` them |
| Sun position | `pvlib` 0.15.1 | active |
| IFC export | `ifcopenshell` 0.8.5 | Python 3.10–3.14 |
| DXF | `ezdxf` 1.4.4 | already in v1 Phase 0.4 |
| Procedural urban-layout generation | **no maintained PyPI library exists** | build in-repo (Phases 9/11); do not invent a dependency |

## 0v2.4 Anti-patterns (v2 additions to v1 §0.5)

- ❌ Hardcoding parking norms nationally — they come from MPZP only.
- ❌ Treating PUM=0.7×PC as law — it is a configurable estimation factor with `basis: industry_heuristic` in output metadata.
- ❌ Encoding §13/§60 from secondary sources — pull ISAP consolidated text first (Phase 8 task).
- ❌ Adding public MCP tools (surface frozen at 22).
- ❌ Free-form "drawings": every proposal element stays typed DSL → validated geometry (v1 Phase 4 guard).
- ❌ AGPL dependency shipped without license decision (ladybug-polyskel) — prefer `py-straight-skeleton` or in-repo Felkel implementation if AGPL is unacceptable.

---

# PHASE 8 — Planning intelligence + regulatory rulesets v1 (MPZP/POG/WZ → machine-readable indicators)

**Unchanged scope from v1 Phase 8** (APP/GML pipeline, document parsers with LLM-candidates → schema validation → evidence, use matrix, conflicts, ruleset engine, `planning_fetch`/`planning_parse_document`/`ruleset_explain`/`manual_override`) **plus** the regulatory ruleset corpus the masterplan phases need.

## 8.1 What to implement (delta over v1 Phase 8)

1. Everything in v1 Phase 8 §8.1 (read it; doc refs there remain valid).
2. **Ruleset corpus `rulesets/PL/building-technical/`** — encode §0v2.2 as YAML rules in the exact format of `rulesets/PL/building-technical/setback-granica.yaml` (copy its frontmatter; `valid_from: 2024-08-01` for the 2023/2442 amendment values; `source_reference` = Dz.U. citation per rule):
   `wt-12-setbacks.yaml` (incl. 5 m >4-kond. multifamily), `wt-13-przeslanianie.yaml`, `wt-60-naslonecznienie.yaml`, `wt-19-parking-distances.yaml`, `wt-21-parking-dimensions.yaml`, `wt-39-pbc.yaml`, `wt-40-plac-zabaw.yaml`, `ppoz-271-273-fire-separation.yaml`, `ppoz-droga-pozarowa.yaml` (Dz.U. 2009/124/1030), `pum-pn-iso-9836.yaml` (measurement rules + sloped-ceiling brackets).
   **Pre-task:** fetch ISAP consolidated PDFs (Dz.U. 2022/1225 + 2023/2442) and confirm literal §12 ust. 1a / §13 ust. 2–4 / §60 wording; record the fragment in each rule's `source_reference` notes (NFR-AUD-005).
3. **Planning-indicator extraction targets** (parser output schema): `max_intensity`, `min_intensity`, `max_height_m`, `max_kondygnacje`, `max_coverage_ratio`, `min_pbc_ratio`, `parking_per_mieszkanie`, `parking_per_100m2_uslug`, `linia_zabudowy` (obowiązująca/nieprzekraczalna, geometry where derivable), `dach/materiał` constraints, `zabudowa_śródmiejska` flag (drives §13/§60/§40 reductions). Each with citation fragment + confidence (v1 §29 parser DoD).
4. **Rule evaluation engine** (v1 Phase 8 item 4): declarative pass/fail/warning/unknown with trace — the Phase 10 validators call it.

## 8.2 Documentation references

- v1 Phase 8 §8.2 (all). Ruleset YAML format to copy: `rulesets/PL/building-technical/setback-granica.yaml`. Loader contract: `packages/rules/plot_rules/loader.py`. Legal values + citations: **§0v2.2**. APP/GML XSD v2.0: Dz.U. 2023 poz. 2409 + gov.pl schematy aplikacyjne (§0v2.2).

## 8.3 Verification checklist

- v1 Phase 8 §8.3 (all), plus:
- Every new YAML has `valid_from`, Dz.U. `source_reference`; `git grep -L "valid_from" rulesets/PL/**/*.yaml` → empty.
- Golden MPZP fixture (uchwała PDF excerpt) → parser extracts the §8.1.3 indicator set with citations; missing indicator → `unknown`, not a default.
- `ruleset_explain` returns the WT rules with their Dz.U. references; edit `wt-12-setbacks.yaml` value → reflected without restart (hot-reload, v1 Phase 2).
- `zabudowa_śródmiejska: true` flips §13/§60/§40 thresholds in rule evaluation (unit test per rule).

## 8.4 Anti-pattern guards

- v1 Phase 8 §8.4 (all). Plus: ❌ no WT value enters Python code — values live in YAML only (the validators in Phase 10 *read* the ruleset registry); ❌ parking norm never defaulted nationally (§0v2.4).

---

# PHASE 9 — Masterplan DSL v2 + capacity engine (chłonność liczbowa i per-budynek)

**Goal:** the proposal language and the metrics engine that can represent and measure a ROBYG-class layout. Maps to spec §8.4 F-0161–0179, §8.5 F-0180–0215 (v1 Phase 9 items 2–4 absorbed here and in Phase 11).

## 9.1 What to implement

1. **DSL v2** in `packages/agent/plot_agent/drawing/proposal.py` — extend, don't replace (`LayoutProposal` stays valid for single-building; add a discriminated union or `schema_version` field):
   ```python
   class BuildingSegment(BaseModel):      # one wing of an L/C/courtyard building
       rectangles: list[PlacedRectangle]  # reuse proposal.py:35-66 DSL
       polygon: dict | None               # or explicit GeoJSON
       floors: int                        # kondygnacje nadziemne of this wing
       use: Literal["mieszkalny","uslugowy","mieszkalno-uslugowy","hotelowy","garazowy","techniczny"]
       ground_floor_use: Literal["mieszkalny","uslugowy","garaz","techniczny"] | None
   class BuildingSpec(BaseModel):
       name: str                          # "Budynek 1"
       segments: list[BuildingSegment]
       stage: int | None                  # etap realizacji
       status: Literal["projektowany","istniejacy","w_budowie","zrealizowany","zabytek_do_remontu"]
       underground_floors: int = 0        # hala garażowa levels
   class RoadElement(BaseModel):          # internal KDW + fire road
       centerline: dict                   # GeoJSON LineString
       width_m: float
       function: Literal["kdw","pozarowa","pieszojezdnia","dojscie"]
   class ParkingElement(BaseModel):
       kind: Literal["naziemny","hala_podziemna","wbudowany"]
       polygon: dict
       spaces: int
       serves_buildings: list[str]
   class MasterplanProposal(BaseModel):
       buildings: list[BuildingSpec]
       roads: list[RoadElement]
       parking: list[ParkingElement]
       greenery_polygons: list[dict]      # PBC
       playgrounds: list[dict]            # place zabaw polygons
       retention: list[dict]
       zabudowa_srodmiejska: bool = False
   ```
   `propose_layout` accepts both shapes (schema bump documented in tool description; output schema versioned).
2. **Capacity/metrics engine** `packages/planning/plot_planning/capacity.py` — pure functions, per building / per stage / total:
   - powierzchnia zabudowy (footprint union), **PC nadziemna** = Σ(segment_area × floors), **GFA**, **PUM** = PC_mieszkalna × `pum_efficiency` (config default 0.70, `basis: industry_heuristic` in metadata; PN-ISO 9836:2022 cited as the measurement standard the estimate approximates), **PUU** analogously for usługi, **mieszkania estimate** = PUM / `avg_mieszkanie_m2` (config, default ~52 m²), intensywność = PC/parcel, coverage ratio, PBC balance (greenery ∪ playground·30% rule), parking demand (from Phase 8 MPZP indicator × mieszkania + usługi) vs supply (Σ ParkingElement.spaces), plac zabaw demand per §40 brackets.
   - **Wire `capacity_generate_scenarios`** (stub at `server.py:210-216`): no-drawing mode — from envelope + indicators compute conservative/base/optimistic/max scenarios (v1 F-0202–0205) + sensitivity (F-0206), filling `CapacityScenario.metrics`.
3. **Domain extension** `packages/domain/plot_domain/models.py`: `BuildingRecord` (name, geometry, floors per segment, uses, stage, status, metrics dict) and `MasterplanVariant` (buildings list, roads, parking, totals, per-stage table). `CapacityScenario` gains optional `masterplan_variant_id`. **PB-forward rule:** `BuildingRecord` must carry an optional `storeys: list[StoreyRecord]` placeholder (empty until Phase 15) so the PB data path needs no schema break.
4. **Renderer v2** (`packages/reports/plot_reports/render/`): building footprints with **floor-count labels** ("4", "7" — like the exemplar), status hatching (legend: istniejące/zrealizowane/w budowie/projektowane — copy the exemplar legend semantics), roads, courtyards, playgrounds, parking, north arrow + scale bar, side table panel (per-stage PUM/PUU/mieszkania + SUMA row). Deterministic styles via `LayerRole` extension (golden-image testable).
5. **Validation at ingest:** every polygon `make_valid`-checked, inside parcel ∪ tolerance, EPSG:2180 — reuse `proposal.py` bounds-check idiom.

## 9.2 Documentation references

- DSL to extend: `proposal.py:35-115`. Score contract: `score.py:42-161`. Renderer layers: `render/layer.py`. Domain: `models.py` (`CapacityScenario`). Spec: `base_assumptions.md:738-818` (§8.4–8.5). PUM/PN-ISO + sloped brackets: **§0v2.2**. Exemplar deliverable semantics (legend, table columns Liczba mieszkań/PUM/PUU/PU, stage rows): the ROBYG reference described in the header.

## 9.3 Verification checklist

- Pydantic round-trip: a hand-written `MasterplanProposal` with ≥10 buildings (incl. one L-shaped 2-segment, one zabytek `status="zabytek_do_remontu"`, hala podziemna, 2 stages) validates and renders.
- Metrics golden test: fixture masterplan with known arithmetic → PUM/PUU/mieszkania/parking-balance match hand-computed values exactly; per-stage table sums to totals (SUMA row consistency, like the exemplar's 1257/63713/15576).
- `capacity_generate_scenarios` on a golden parcel + indicators returns 4 scenarios + sensitivity; `unknown` indicators propagate to `unknowns`, not defaults.
- Renderer golden-image test: masterplan render with floor labels + legend + table; perturb a floor count → snapshot fails.
- Old `LayoutProposal` payloads still accepted (backwards-compat test).

## 9.4 Anti-pattern guards

- ❌ PUM presented without `basis` metadata (heuristic vs measured). ❌ Parking demand computed without an MPZP source → must be `unknown` + question for gmina. ❌ Mixing analytic and render geometry (v1 Phase 3 guard). ❌ New public tool for masterplans — `propose_layout` carries it.

---

# PHASE 10 — Inter-building rule validators (WT §12/§13/§19/§39/§40/§60 + ppoż) — the "real architecture" gate

**Goal:** a layout that passes here is defensible against warunki techniczne. These validators are what makes the model's drawing *architecture* instead of rectangles. New package module `packages/planning/plot_planning/wt_validators/`; all thresholds read from Phase 8 rulesets.

## 10.1 What to implement

1. **§12 boundary validator:** per-building wall-plane decomposition (each załamanie = separate wall, §0v2.2), windowed/windowless attribute on `BuildingSegment` walls (DSL v2 addendum: `windowed_walls: list[int]` edge indices, default all-windowed = conservative), distance to parcel boundary ≥4/3/5 m by case.
2. **§13 przesłanianie checker:** for each facade window axis (sampled along windowed walls at configurable spacing), construct the 60° horizontal angle, find obstructing buildings within `wysokość przesłaniania`/35 m; pairwise over all buildings incl. existing/neighbor buildings (from BDOT10k connector); śródmiejska → ½ reductions.
3. **§60 nasłonecznienie checker:** `pvlib` sun positions for równonoc (Mar 20/Sep 22) over the rule's window (7:00–17:00 mieszkania; 10:00–16:00 plac zabaw §40); shadow casting = 2.5D extrusion of building footprints by floors × `floor_height` config (default 3.3 m wielorodzinny — config, not ruleset); per-facade-point continuous-insolation ≥3 h (1.5 h śródmiejska); flag worst-case apartments; plac zabaw ≥2 h (1 h śródmiejska).
4. **§19/§21 parking validator:** distance of surface parking polygons to windows/playgrounds/boundary by size bracket; stall-count vs polygon area plausibility (2.5×5 m + maneuvering ≈ 25 m²/stall heuristic, config).
5. **§39 PBC + §40 plac zabaw validators:** PBC ratio vs MPZP-or-25%; playground area brackets, part size ≥50 m², ≥10 m distances, ≥30% on PBC.
6. **Ppoż validators:** §271–273 pairwise separations (ZL IV default for mieszkalne; PM for garaż/techniczny by Q config); §272 half-distance to unbuilt neighbor boundary; **droga pożarowa reachability:** for each building requiring one (średniowysoki+ = >12 m ≈ floors × floor_height), verify a `RoadElement(function="pozarowa")` (or kdw of width ≥4 m) runs along the longer side with edge 5–15 m from wall, curve radii ≥11 m (polyline check), dead-end → 20×20 plac, dojście ≤50 m.
7. **Integration:** all validators return `RuleCheck{rule_id, status: pass|fail|warning|unknown, geometry_evidence, message}`; wired into `score_proposal` as **hard violations** (fail) / negative factors (warning) — reusing `validate_hard` + `ProposalScore.violations` (`score.py`); `constraints_compute` exposes them per analysis; renderer can draw violation geometries (red overlay layer).

## 10.2 Documentation references

- Threshold values + citations: **§0v2.2** (the rulesets written in Phase 8 — validators read `RulesetRegistry`, never literals). Hard-blocker dominance: `score.py:140-151` + spec §14.2 (`base_assumptions.md:1620-1626`). pvlib sun API: v1 Phase 0.4. Shadow approach: v1 Phase 10 item 6 (§7.15, F-0343/0344). Neighbor buildings source: BDOT10k WFS connector (`packages/connectors/plot_connectors/wfs.py`).

## 10.3 Verification checklist

- Unit goldens per validator with hand-constructed geometry: (a) two 5-kond. blocks at 7 m → §13 fail, at sufficient spacing → pass; (b) south-facing flat → §60 pass, north courtyard bottom flat → fail flagged; (c) parking 25 stalls 8 m from window → fail (needs 10 m); (d) 120-mieszkanie zespół without playground → §40 fail with required-area message; (e) ZL blocks 6 m apart → §271 fail, 8 m → pass; (f) building 18 m tall with no fire road along longer side → fail.
- Śródmiejska flag halves §13/§60/§40 thresholds (parameterized tests).
- Property test: validators are pure — same proposal twice → identical RuleChecks (determinism for the loop).
- `propose_layout` on a violating masterplan returns `valid=false`, violations list with rule_ids + an image where violation geometry is visibly overlaid.
- All thresholds traced to ruleset: `git grep -nE "(4\.0|3\.0|5\.0|8\.0|35\.0)" packages/planning/plot_planning/wt_validators` shows no bare legal constants (values come from registry).

## 10.4 Anti-pattern guards

- ❌ Full 3D shadow mesh when 2.5D extrusion suffices (NFR-PERF-013). ❌ Validator silently passing when an input is missing (no MPZP parking norm → `unknown`, not pass). ❌ Score "averaging away" a fail (§14.2). ❌ Trusting model-declared `windowed_walls` blindly for *compliance claims* — mark geometry-derived assumptions in evidence.

---

# PHASE 11 — Architect-agent workflow: composition, generative masterplan loop, etapowanie, deliverable

**Goal:** the end-to-end "model as architect" session (Target workflow steps 4–6). Builds on Phases 8–10; this is the user's core demo: an irregular, MPZP-adapted multi-building concept with PUM table, produced by the model through decisions, not by a black-box optimizer.

## 11.1 What to implement

1. **Design-brief generator** → resource `analysis://{id}/design-brief` (+ assembled into the drawing prompt): plot axes (medial axis / straight skeleton via `py-straight-skeleton`; fall back to in-repo Felkel port if quality insufficient — §0v2.3 license note), frontage/orientation analysis (south exposure, water/green edges, noise edges from road class + railway), heritage objects to retain (status `zabytek_do_remontu`), buildable envelope, MPZP indicator summary, hard rules in force (Phase 8/10), recommended typologies per zone+plot (see 2).
2. **Typology knowledge** `packages/planning/plot_planning/typologies.py` + `rulesets/PL/typologies/*.yaml` (heuristics, `basis: design_practice`): wielorodzinny trakt 12–18 m, klatka serves ~2–6 mieszkań/kondygnacja, sekcja length 20–35 m, punktowiec/klatkowiec/galeriowiec/kwartał obrzeżny templates, hala garażowa pod dziedzińcem pattern, usługi w parterze along public frontages. These are *suggestions in the brief*, never validators.
3. **Generative masterplan loop v2** (extend `drawing/loop.py`): iterate `MasterplanProposal`s; critique cites failing `rule_id`s + which building pair/facade (from Phase 10 RuleChecks) + capacity gap vs `capacity_generate_scenarios` target ("PUM 41 200 of ~63 000 achievable; zwiększ kondygnacje wzdłuż północnej pierzei"); budget + plateau stop (v1 Phase 4 contract); **every iteration audit-logged with rendered artifact URI** (F-0446).
4. **Exemplar memory v2:** persist to `.artifacts/drawing-exemplars/` (dir exists; currently in-memory only — inventory §5): key `(shape_class, program_type, density_class)`, store accepted `MasterplanProposal` + scores + thumbnail; surfaced as few-shot in the drawing prompt (recall test exists in Phase 4 — extend it).
5. **Etapowanie:** stage assignment in DSL (Phase 9) + stage-consistency checks (each stage independently serviceable: road access + parking balance per stage; placówka zabaw available by the stage that crosses 20 mieszkań) + per-stage capacity table.
6. **Design rationale record:** loop accepts model-provided `rationale: str` per iteration; persisted in audit + final report ("dlaczego tak" — the user's "pełne zrozumienie projektowania"). Rationale is documentation, NEVER input to validation (NFR-SEC-003).
7. **Deliverable assembly** — wire `report_generate` variant `koncepcja`: plan zagospodarowania render (Phase 9 renderer v2) + per-building/per-stage table + rationale + unknowns/questions for gmina; **2 new prompts** (allowed; tools frozen): "Analiza chłonności działki (chłonność + koncepcja)" and "Iterate masterplan like an architect" encoding the Target-workflow step order.

## 11.2 Documentation references

- Loop/exemplar code to extend: `drawing/loop.py`, `proposal.py:117-147`. Audit + guardrails: v1 Phase 4 §4.1.5/§4.4. Brief inputs: envelope (`envelope.py:82-163`), indicators (Phase 8), validators (Phase 10), capacity targets (Phase 9). Skeleton libs: **§0v2.3**. Spec: §8.13 autonomy `base_assumptions.md:1046-1074`, scoring §14 `:1604-1626`, F-0161–0179 variants.

## 11.3 Verification checklist

- **End-to-end demo (acceptance for this phase):** golden fixture `elektrownia-like` (irregular ~5 ha riverside polygon + synthetic MPZP indicators + 2 heritage footprints + railway edge) → a scripted model session (recorded tool-call sequence) produces a masterplan with ≥8 buildings incl. retained zabytki, internal road loop, ≥2 stages, **zero hard-rule violations**, PUM within 10% of `capacity_generate_scenarios` base target, table totals consistent; final render visually shows courtyards/roads/labels (manual review + golden image).
- Critique quality test: inject a §13 violation → next-iteration prompt contains the rule_id and the offending building pair.
- Stage-consistency test: stage 1 without parking → fail with message.
- Exemplar persistence: process restart → exemplars reload from `.artifacts/drawing-exemplars/`; similar parcel recalls them.
- Rationale appears in report + audit; grep confirms rationale is not parsed by any validator.
- Run via the self-improve loop: worsen a typology YAML → scores drop → DevLoop reports regression.

## 11.4 Anti-pattern guards

- ❌ Optimizer replacing the model's decisions — the server validates/scores/criticizes; **the model proposes** (user's locked intent). ❌ Typology heuristics enforced as rules. ❌ Loop accepting a layout with hard violation regardless of capacity score (§14.2). ❌ Rationale text steering validators (NFR-SEC-003). ❌ Hiding capacity shortfall — report gap vs scenarios explicitly.

---

# PHASE 12 — Site context depth: terrain/water/geology/environment/heritage/roads/sun (v1 Phase 10)

**Scope = v1 Phase 10 minus what Phase 10v2 already built (sun/shadow engine).** Read v1 Phase 10 §10.1–10.4 verbatim; deltas:

1. Sun-path/shadow module from Phase 10v2 is reused for neighbor-shading analysis (F-0343/0344) — do not re-implement.
2. Masterplan integration: terrain slope feeds earthworks risk per building; flood zones clip buildable envelope per stage; heritage zones constrain `zabytek_do_remontu` interventions; road-access chain validates the KDW connection point (zjazd) of the masterplan.
3. All §14 scores wired into `PHASE10_SCORE_HOOKS` (`evaluator.py:31-41`) replacing `unknown` placeholders.

**Verification:** v1 Phase 10 §10.3 + masterplan-context test: flood-zone overlap with a stage → that stage flagged, envelope clipped, render shows it. **Anti-patterns:** v1 Phase 10 §10.4.

---

# PHASE 13 — Autonomy, orchestration, batch, monitoring (v1 Phase 11, unchanged)

Execute v1 Phase 11 as written (task graph, freshness monitors, workers/queue, `portfolio_analyze`/`monitoring_create`/`cache_warm`, streaming progress). Addendum: the orchestrator's task graph includes the chłonność pipeline (steps 1–6 of the Target workflow) as a plannable composite with manual-review gates after the design-brief and after final concept.

---

# PHASE 14 — Reports/exports (koncepcja-grade), HTTP API, security & perf (v1 Phase 12 + deliverable polish)

**Scope = v1 Phase 12** (all formats from one report model, audience variants, FastAPI sharing use-cases, security hardening F-0477–0503, perf F-0504–0536) **plus:**

1. **Chłonność report template** mirroring the professional deliverable: title block (investor/date), plan render with legend (4 status hatches), stage table (Liczba mieszkań / PUM / PUU / PU + SUMA), per-building callouts, assumptions & disclaimers (PUM heuristic basis, data confidence), questions-for-gmina annex.
2. **DXF export** of masterplan layers (`ezdxf`; layer naming convention documented) and **IFC export** (`ifcopenshell` 0.8.5): `IfcProject/IfcSite/IfcBuilding/IfcBuildingStorey` massing per `BuildingRecord` (storey count × floor height), georeferenced (EPSG:2180 → IfcMapConversion). This is the BIM bridge for PB/PW.
3. PDF via weasyprint from the same report model (§31 DoD).

**Verification:** v1 Phase 12 §12.3 + IFC opens in a viewer (e.g. ifcopenshell validate + open-source viewer smoke), DXF opens in CAD with correct layers; PDF/HTML/MD/JSON numbers identical. **Anti-patterns:** v1 Phase 12 §12.4 + ❌ IFC with fake detail (massing only — no invented walls/slabs at this stage).

---

# PHASE 15 — Projekt budowlany direction: PZT generator v1 (and the PW horizon)

**Goal:** not a signed projekt budowlany (that requires uprawnienia + survey-grade data) — but the tool produces a **PZT-structured draft package** from an accepted masterplan, and the data model carries everything PB needs. Maps to Dz.U. 2020 poz. 1609 (t.j. 2022/1679) §13–18.

## 15.1 What to implement

1. **Domain deepening:** fill `BuildingRecord.storeys` (`StoreyRecord{level, height, use, area}`) from DSL floors; `LokalRecord` placeholder (PW horizon). Mark every PB-relevant quantity with measurement basis (heuristic vs computed vs surveyed).
2. **PZT część opisowa generator** (per §14): przedmiot zamierzenia, existing state (from connectors: BDOT10k buildings, uzbrojenie GESUT), projektowane zagospodarowanie (buildings/roads/parking/zieleń from masterplan), **zestawienie powierzchni** (auto: zabudowa / drogi+utwardzenia / PBC — already computed in Phase 9), constraints (zabytki, górnicze, zagrożenia from Phase 12), **ochrona ppoż section incl. drogi pożarowe** (from Phase 10 fire validators — checks become statements with evidence), informacja o obszarze oddziaływania (rule-based draft, flagged `requires_projektant`).
3. **PZT część rysunkowa export** (per §15): scaled vector drawing (SVG→PDF, vector per e-form rules): granice, linie zabudowy, obiekty z wymiarami zewnętrznymi i liczbą kondygnacji, sieci/przyłącza (where known), układ komunikacyjny incl. drogi pożarowe, zieleń, rzędne (from NMT, Phase 12). File naming per załącznik convention (PZT + rrrr.mm.dd), PDF ≤150 MB.
4. **Compliance checklist tool output:** `report_generate(format="pzt-draft")` returns the package + a §13–18 checklist (done / missing / requires-uprawnienia) — honest about what a human projektant must complete.
5. **PW horizon (documentation only, no code):** `AGENTS/PW_DIRECTION.md` — what projekt techniczny/wykonawczy will require (branżowe projekty, detale, IFC deepening to elements), confirming current schemas don't block it.

## 15.2 Documentation references

- PZT content: **§0v2.2** "Projekt budowlany" (Dz.U. citations; §13–18 itemization). E-form rules (PDF/vector/150 MB/naming): §0v2.2. Zestawienie powierzchni source data: Phase 9 capacity engine. Ppoż statements: Phase 10 RuleChecks. IFC: Phase 14 exporter.

## 15.3 Verification checklist

- PZT draft generated for the Phase 11 golden masterplan: opisowa contains all §14 sections (checklist all-green or explicitly `requires_projektant`); zestawienie powierzchni numbers equal Phase 9 metrics (single source of truth test).
- Rysunkowa PDF is vector (no raster embed), correctly scaled (measure a known dimension), named per convention.
- Checklist honesty test: remove NMT data → rzędne item flips to `missing`, not silently dropped.

## 15.4 Anti-pattern guards

- ❌ Claiming the output is a projekt budowlany (it is a draft package for a projektant; disclaimers mandatory). ❌ Inventing rzędne/sieci where data is absent. ❌ Diverging zestawienie powierzchni from capacity engine (one computation, two renderings).

---

# PHASE 16 — Test corpora, calibration, acceptance & deployment (v1 Phase 13 + masterplan acceptance)

**Scope = v1 Phase 13** (full test matrix F-0537–0561, confidence calibration §25, deployment/runbooks/offline mode) **plus the v2 acceptance gate:**

1. **Masterplan golden corpus:** ≥5 fixtures spanning plot classes (irregular riverside ~5 ha like the exemplar; narrow infill śródmiejska; suburban 1 ha MN; corner mixed-use; multi-parcel assembly) each with synthetic-but-realistic MPZP indicators and expected capacity ranges.
2. **V2 acceptance demo (the user's bar):** live MCP session in Claude Code on the `elektrownia-like` fixture: model runs Target-workflow steps 1–6 unaided by hardcoded scripts → multi-building, multi-stage concept, zero hard violations, PUM table + render + rationale + PZT-draft generated. Recorded as the canonical demo.
3. Calibration extended to validator confidence (geometry-derived vs declared inputs).

**Verification:** v1 Phase 13 §13.3 + the demo above reviewed by the user. **Anti-patterns:** v1 Phase 13 §13.4.

---

# FINAL PHASE — Cross-cutting verification (v2)

Run v1 Final Phase checks (doc conformance, anti-pattern greps, tool count == 22 prod / 24 dev, `valid_from` in every ruleset, full suite) plus:

1. `git grep -nE "0\.7[0-9]?\s*#?.*PUM|pum_efficiency\s*=\s*0\.7" packages/` → factor only in config with `industry_heuristic` basis.
2. No legal constants in validator code (Phase 10.3 grep) — all from `RulesetRegistry`.
3. Schema-version test: v1 `LayoutProposal` payload and v2 `MasterplanProposal` both accepted by `propose_layout`.
4. License check: no AGPL package in `uv.lock` unless explicitly approved (§0v2.3).
5. Re-verify time-sensitive legal facts (plan ogólny deadline; PN-ISO edition in PB-reg załącznik) — they were in flux on 2026-06-10.
6. End-to-end self-improve run on the masterplan loop: scores improve across iterations, exemplar stored, audit complete.

---

## Phase map (v2 ↔ v1 ↔ spec)

| v2 Phase | v1 equivalent | New content | Spec anchors |
|---|---|---|---|
| 8 Planning intelligence + WT rulesets | Phase 8 | WT/ppoż/PUM ruleset corpus with Dz.U. citations | §8.3, §12, §29 |
| 9 Masterplan DSL + capacity | Phase 9 (part) | multi-building DSL, PUM/PUU engine, renderer v2, stage tables | §8.4–8.5 |
| 10 Inter-building validators | — (new) | WT §12/13/19/39/40/60 + ppoż + droga pożarowa | §7.15, §14.2 |
| 11 Architect workflow | Phase 9 (part) + Phase 4 loop | design brief, typologies, masterplan loop, etapowanie, rationale, deliverable | §8.13, §14, F-0161–0179 |
| 12 Site context depth | Phase 10 | masterplan integration of terrain/flood/heritage/roads | §7.7–7.16, §8.6–8.10 |
| 13 Autonomy/batch/monitoring | Phase 11 | chłonność pipeline as task graph | §8.13, §4.4–4.5 |
| 14 Reports/API/security/perf | Phase 12 | chłonność report template, DXF/IFC massing export | §8.12, §8.14–8.16, §27 |
| 15 PZT generator v1 | — (new) | PZT opisowa+rysunkowa draft, checklist, PW horizon doc | Dz.U. 2020/1609 §13–18 |
| 16 Tests/calibration/acceptance | Phase 13 | masterplan golden corpus + live acceptance demo | §18, §25 |

## Suggested execution

`do` skill, one phase per fresh context: **8 → 9 → 10 → 11** (this is the chłonność/koncepcja critical path — after 11 the user's demo works on synthetic data), then **12** (real-context depth), **13 → 14 → 15 → 16**. The Phase 4 self-improve loop remains the default workflow throughout.
