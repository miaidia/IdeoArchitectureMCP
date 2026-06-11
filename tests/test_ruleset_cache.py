"""Ruleset registry cache tests (Phase 14B; F-0509) — hot-reload stays green.

* prod (``dev_hot_reload=False``): the stat-fingerprint cache returns the SAME
  parsed registry while no rule file changed, and invalidates on ANY mtime/size
  change (§18.3: ruleset update without code change still works);
* dev (``dev_hot_reload=True``): fresh parse on every call — the Phase 2
  hot-reload contract is untouched.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from plot_rules import clear_registry_cache, load_rulesets

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def ruleset_copy(tmp_path: Path) -> Path:
    target = tmp_path / "PL"
    shutil.copytree(REPO / "rulesets" / "PL", target)
    return target


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch):
    import plot_shared.config as cfg

    def apply(dev_hot_reload: bool):
        monkeypatch.setenv("PLOT_DEV_HOT_RELOAD", "true" if dev_hot_reload else "false")
        cfg.get_settings.cache_clear()
        clear_registry_cache()

    yield apply
    cfg.get_settings.cache_clear()
    clear_registry_cache()


def _touch_rule(root: Path) -> Path:
    rule = next(root.rglob("*.yaml"))
    text = rule.read_text(encoding="utf-8")
    rule.write_text(text + "\n# touched by test\n", encoding="utf-8")
    # mtime_ns granularity guard on coarse filesystems
    time.sleep(0.01)
    return rule


def test_prod_cache_hit_returns_same_registry(ruleset_copy: Path, settings_env) -> None:
    settings_env(dev_hot_reload=False)
    first = load_rulesets(ruleset_copy)
    second = load_rulesets(ruleset_copy)
    assert second is first  # cache hit — no re-parse
    assert len(first.rules) > 0


def test_prod_cache_invalidates_on_file_change(ruleset_copy: Path, settings_env) -> None:
    settings_env(dev_hot_reload=False)
    first = load_rulesets(ruleset_copy)
    _touch_rule(ruleset_copy)
    second = load_rulesets(ruleset_copy)
    assert second is not first
    # the content hash version changes with the file bytes (hot ruleset update)
    assert second.ruleset_version != first.ruleset_version


def test_dev_mode_loads_fresh_every_call(ruleset_copy: Path, settings_env) -> None:
    settings_env(dev_hot_reload=True)
    first = load_rulesets(ruleset_copy)
    second = load_rulesets(ruleset_copy)
    assert second is not first  # fresh-per-call (Phase 2 contract)
    assert second.ruleset_version == first.ruleset_version  # same content though
