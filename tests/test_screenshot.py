"""Playwright web-map screenshot path tests (Phase 3 §3.1 deliverable 4).

Best-effort by design: on hosts where headless Chromium cannot launch (common on
WSL2 without ``playwright install-deps`` — missing libgbm/libatk/libxkbcommon), the
typed :class:`BrowserUnavailableError` is raised and the screenshot test SKIPS
gracefully. The matplotlib render path does NOT depend on a browser (Phase 3 §3.1).
"""

from __future__ import annotations

import pytest
from plot_reports import BrowserUnavailableError, leaflet_preview_html, screenshot_web_map


def test_leaflet_preview_html_is_self_contained() -> None:
    html = leaflet_preview_html()
    assert "<html" in html and "id=\"map\"" in html


def test_screenshot_web_map_or_skip() -> None:
    """Screenshot the built-in Leaflet page; SKIP if no browser is available."""
    try:
        png = screenshot_web_map(width=640, height=480)
    except BrowserUnavailableError as exc:
        pytest.skip(f"headless browser unavailable on this host (WSL2): {exc}")
    # PNG magic bytes + non-trivial size (non-blank capture).
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000
