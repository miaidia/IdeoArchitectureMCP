# PW direction — projekt techniczny / wykonawczy horizon (Phase 15 Task 5)

**Status: documentation only. Everything below is explicitly OUT OF CODE SCOPE
for v2.** This note records what the projekt techniczny (PT, §22–24 of Dz.U.
2020 poz. 1609, t.j. 2022 poz. 1679) and the (non-statutory but industry-standard)
projekt wykonawczy will require beyond the Phase 15 PZT draft package, and
verifies that the CURRENT schemas do not block that deepening.

## What PT/PW need beyond PZT

1. **Projekty branżowe** — konstrukcja, instalacje sanitarne (w/k/c.o./gaz),
   elektryczne i teletechniczne, wentylacja/klimatyzacja, drogi. Each is a
   separate, signed elaboration by a projektant with branżowe uprawnienia. The
   tool's role stays upstream: the PZT draft + capacity metrics + site context
   (GESUT collisions, terrain, geotechnics brief) are the INPUT package handed
   to those designers — the tool never authors branżowe content.
2. **Detale i rozwiązania konstrukcyjno-materiałowe** (§23: rozwiązania
   konstrukcyjne, charakterystyka energetyczna, warunki geotechniczne w formie
   adekwatnej do kategorii) — requires survey-grade inputs (mapa do celów
   projektowych, badania gruntowe) the tool explicitly marks as
   `requires_uprawnienia` / `wymaga_badan_gruntowych` today.
3. **IFC deepening to elements** — walls/slabs/columns/openings/MEP
   (IfcWall/IfcSlab/IfcColumn/IfcDoor/IfcWindow/IfcDistribution\*). The Phase 14
   exporter is deliberately **massing-only** (IfcProject/Site/Building/Storey +
   extruded footprints, EPSG:2180 IfcMapConversion) and tests ENFORCE zero
   IfcWall/IfcSlab/IfcWindow entities. That massing model is the right
   substrate: PT/PW deepening replaces each storey extrusion with element
   geometry INSIDE the already-georeferenced spatial structure — no exporter
   rewrite, only additive element generation per storey.
4. **Lokal-level measurement (PN-ISO 9836)** — PUM at PW grade is measured per
   lokal from internal partition geometry (including the sloped-ceiling 100/50/0%
   brackets of PB-reg §20 ust. 1 pkt 4 lit. b), not estimated by the
   `pum_efficiency` heuristic. The Phase 9/15 outputs always carry
   `basis: industry_heuristic` exactly so a PW-grade `basis: computed`/
   `surveyed` value can replace them without changing consumers.

## Why the current schemas don't block it

* `BuildingRecord.storeys: list[StoreyRecord]` — filled since Phase 15 from the
  DSL (level / height_m / use / area_m2 + per-quantity `basis`). PT/PW deepening
  changes the VALUES (surveyed heights, measured areas) and the basis tags, not
  the shape.
* `StoreyRecord.lokale: list[LokalRecord]` — the Phase 15 PLACEHOLDER
  (`id / storey_level / use / area_m2`). It is an empty list everywhere in v2;
  a PW phase fills it with PN-ISO 9836 lokal measurements. Adding fields
  (rooms, heights, finish standards) later is additive — Pydantic models with
  defaulted new fields keep old payloads valid (the Phase 9 → Phase 15 storeys
  fill already proved this pattern: no schema break, no migration).
* `MasterplanVariant.metadata` is an open dict — branżowe artifacts, PT
  checklists and survey references can attach without schema changes.
* The Phase 14 IFC exporter reads `floors_by_segment`/`underground_floors`/
  `storeys` off the domain record — element-level deepening extends the same
  reader.

## Explicit non-goals (v2)

* No code generates PT/PW content, branżowe drawings, construction details or
  element-level IFC in v2.
* No lokal data is invented — `lokale` stays empty until a real measurement
  source exists.
* The PZT package remains a DRAFT for a projektant (mandatory disclaimer,
  Phase 15 §15.4); nothing on the PW horizon changes that boundary — it moves
  MORE work to licensed professionals, not less.
