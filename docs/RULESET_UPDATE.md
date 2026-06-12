# Ruleset update workflow — no code change (§18.3, Phase 16)

The §18.3 acceptance criterion "rulesets are versioned, tested and updatable
WITHOUT a code change" is test-enforced. This is the workflow.

## Where rules live

`rulesets/PL/**/*.yaml` — one rule per file, declarative
(`applies_when`/`thresholds`/`select`/`checks`/`modifiers`/`input_defaults`).
The engine (`plot_rules.engine`) is value-free: every legal number lives in
YAML with its `source_reference` (Dz.U. citation) — a grep guard fails the
suite if a legal constant appears in validator code.

## Updating a rule (e.g. an amendment changed a threshold)

1. **Edit the YAML** — change the threshold/bracket; update `source_reference`
   and `source_quote` to the amending act.
2. **Set `valid_from`** to the amendment's entry-into-force date (every rule
   must carry `valid_from` — a registry test enforces it).
3. **Reload**:
   - dev (`PLOT_DEV_HOT_RELOAD=true`): call the `dev_reload` MCP tool — the
     registry reloads in place, `tools/list_changed` is emitted and the
     content-hash `ruleset_version` changes (`tests/test_hot_reload.py`);
   - production: restart the process (rulesets load at startup). The
     `ruleset_version` content hash makes the change visible in every
     subsequent report/audit record. Note: the production registry cache
     (F-0509 stat fingerprint in `plot_rules/loader.py`) auto-invalidates on
     any YAML mtime/size change, so code paths that call `load_rulesets`
     per call pick the edit up immediately — only the MCP runtime's
     startup-held registry needs the restart (or `dev_reload` in dev).
4. **Run the regression suite**:

   ```bash
   uv run pytest tests/test_ruleset_regression.py tests/test_rulesets_wt.py tests/corpus
   ```

   - `test_ruleset_regression.py` sweeps EVERY rule: a NEW rule without a
     golden vector in `tests/ruleset_vectors.py` FAILS the suite — add the
     vector(s) in the same change (that file is test data, not engine code);
   - the per-rule golden tests (`test_rulesets_wt.py`) pin the legal semantics;
   - the masterplan corpus re-validates whole layouts against the changed law.
5. **Update expectations deliberately** — if the amendment legitimately flips a
   golden expectation, change the vector WITH the Dz.U. citation in the diff.
   Never adjust a vector to silence a failure you cannot cite.

## Adding a new rule

Same as above, plus: the file must satisfy the ruleset JSON schema
(`plot_rules.schema`, loader-validated), carry `valid_from`,
`source_reference` with a Dz.U. citation, and at least one vector in
`tests/ruleset_vectors.py` (coverage is asserted, untested rules fail CI).

## What requires code (by design)

Only NEW GEOMETRIC INPUTS: a rule that needs a quantity no validator computes
yet (e.g. a new kind of distance) needs a validator extension to produce that
input. The legal VALUES never do.
