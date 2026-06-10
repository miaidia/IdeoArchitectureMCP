# Sources — PL planning rulesets

Citation sidecar for rules whose `source_reference` points at an anchor in this
file. Rules added in Phase 8 cite the Dz.U. position directly in their own
`source_reference` / `source_quote` fields instead.

## mpzp-mn-coverage

Ustawa z dnia 27 marca 2003 r. o planowaniu i zagospodarowaniu przestrzennym —
tekst jednolity **Dz.U. 2023 poz. 977 ze zm.**, art. 15 ust. 2 pkt 6
(obowiązkowy zakres planu miejscowego: zasady kształtowania zabudowy oraz
wskaźniki zagospodarowania terenu, w tym maksymalna i minimalna intensywność
zabudowy oraz maksymalny udział powierzchni zabudowy). Konkretna wartość
wskaźnika powierzchni zabudowy dla terenów MN pochodzi ZAWSZE z danego MPZP —
nie istnieje krajowa wartość domyślna; `default_max_coverage_ratio` w
`mn-coverage.yaml` jest wyłącznie wartością demonstracyjną dla testu
hot-reload (Phase 2), nie progiem prawnym.

Used by: `mn-coverage.yaml` (`PL-PLAN-MN-COVERAGE-001`).
