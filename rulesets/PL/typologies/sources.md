# Typology knowledge — sources (Phase 11 §11.1.2)

These YAML documents encode **design-practice typology knowledge** for multifamily
masterplans (kwartał obrzeżny, klatkowiec sekcyjny, punktowiec, galeriowiec,
usługi w parterze, hala garażowa pod dziedzińcem, zasada traktu).

**They carry NO legal force** (`basis: design_practice`, `legal_force: none`):

- they are SUGGESTIONS surfaced in the design brief
  (`plot_planning.brief` / `plot_planning.typologies.recommend_typologies`),
- they are NEVER evaluated by the rules engine as validators
  (no `checks:` block; `severity: soft`; anti-pattern guard plan §11.4),
- their numeric parameters (trakt depth, section length, typical floors) are
  industry rules of thumb from Polish multifamily development practice
  (neufert-class design heuristics + deweloper masterplan exemplars), not Dz.U.
  values — editing them changes recommendations, never compliance results.

`source_reference` of each document points back to this file.
