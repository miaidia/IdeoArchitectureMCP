"""Hot-reload proof (Phase 2 §2.3 / task evidence #4).

Edits a ruleset YAML value on disk, triggers a reload via the SAME running
``AppContext`` object (no new server process), and asserts ``ruleset_explain``
reflects the new value and the ``ruleset_version`` content-hash changed.

This exercises the reload path directly (``AppContext.reload`` -> fresh
``load_rulesets`` + ``importlib.reload(usecases)``) which is exactly what the
``dev_reload`` tool calls — proving the registry updates without restarting the
server object or its transport (Phase 0.5 / §2.4 anti-pattern).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from plot_rules import load_rulesets

REPO_ROOT = Path(__file__).resolve().parents[1]
RULE_YAML = REPO_ROOT / "rulesets" / "PL" / "planning" / "mn-coverage.yaml"


@pytest.fixture
def restore_rule_yaml() -> object:
    original = RULE_YAML.read_text(encoding="utf-8")
    yield
    RULE_YAML.write_text(original, encoding="utf-8")


def test_load_rulesets_finds_example_rules() -> None:
    reg = load_rulesets(REPO_ROOT / "rulesets" / "PL")
    ids = {r.id for r in reg.rules}
    assert "PL-PLAN-MN-COVERAGE-001" in ids
    assert "PL-WT-SETBACK-GRANICA-001" in ids
    assert "planning" in reg.categories
    assert "building-technical" in reg.categories
    assert reg.ruleset_version.startswith("PL-")


def test_ruleset_edit_reflected_after_reload_without_restart(restore_rule_yaml: object) -> None:
    from plot_mcp_server.runtime import AppContext
    from plot_shared import Settings

    # One long-lived AppContext that we will reload in place (stands in for the
    # running server; the transport is never recreated).
    ctx = AppContext.create(Settings(dev_hot_reload=True))

    before = ctx.usecases.ruleset_explain(ctx.ruleset_registry)
    before_version = ctx.ruleset_registry.ruleset_version
    before_rule = next(r for r in before["rules"] if r["id"] == "PL-PLAN-MN-COVERAGE-001")
    assert before_rule["raw"]["default_max_coverage_ratio"] == 0.30

    # Edit the YAML value on disk (simulating a developer changing a rule).
    doc = yaml.safe_load(RULE_YAML.read_text(encoding="utf-8"))
    doc["default_max_coverage_ratio"] = 0.45
    RULE_YAML.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # Trigger reload on the SAME context object (what dev_reload does).
    new_registry = ctx.reload()

    after = ctx.usecases.ruleset_explain(ctx.ruleset_registry)
    after_rule = next(r for r in after["rules"] if r["id"] == "PL-PLAN-MN-COVERAGE-001")

    # The edited value is reflected live, and the content-hash version changed.
    assert after_rule["raw"]["default_max_coverage_ratio"] == 0.45
    assert new_registry.ruleset_version != before_version
    assert ctx.reload_count == 1
    assert ctx.last_reload_at is not None
