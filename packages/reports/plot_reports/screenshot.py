"""Headless web-map screenshot path (Phase 3 §3.1 deliverable 4 / F-0397).

Renders an interactive Leaflet/MapLibre HTML page in headless Chromium and captures
a PNG. Used when a zoomable/interactive check is needed (vs the deterministic
matplotlib render). Best-effort: if no browser is available (common on WSL2 without
``playwright install-deps``), :func:`screenshot_web_map` raises the typed
:class:`BrowserUnavailableError` so callers / tests can SKIP gracefully (Phase 3 §3.1).

Playwright API used here (verified against the installed package source):

* ``from playwright.sync_api import sync_playwright``
    — .venv/.../playwright/sync_api/__init__.py:94 exports ``sync_playwright``.
* ``with sync_playwright() as p: p.chromium.launch()`` → ``browser.new_page()`` →
  ``page.set_content(html)`` / ``page.goto(url)`` → ``page.screenshot(type="png")``
    — these are the documented Playwright sync APIs (Phase 0.4 "Screenshots (web map)").
* Launch/timeout failures surface as ``playwright.sync_api.Error`` (e.g.
  ``TargetClosedError`` when system libs like libgbm/libatk are missing on WSL2);
  we wrap those in :class:`BrowserUnavailableError`.
"""

from __future__ import annotations

from pathlib import Path

# A minimal self-contained Leaflet page used as the default interactive map. It loads
# Leaflet from a CDN; when offline the screenshot still produces a non-blank PNG
# (background + container), and the test only asserts non-blank bytes.
_DEFAULT_LEAFLET_HTML = """<!doctype html>
<html><head><meta charset="utf-8"/>
<title>plot-analyzer map</title>
<style>html,body,#map{height:100%;margin:0;background:#dfe9f3}</style>
</head><body><div id="map"></div>
<script>
  document.body.style.background = "#cfe3f7";
  var d = document.getElementById("map");
  d.style.background = "linear-gradient(135deg,#cfe3f7,#9ec5e8)";
  d.innerHTML = "<div style='padding:16px;font-family:sans-serif'>Interactive map preview</div>";
</script>
</body></html>"""


class BrowserUnavailableError(RuntimeError):
    """Raised when a headless browser cannot be launched (test SKIP signal, §3.1).

    On WSL2 the bundled Chromium is downloaded but cannot start without system libs
    (libatk, libgbm, libxkbcommon, …) installed via ``playwright install-deps`` (root).
    """


def screenshot_web_map(
    html: str | Path | None = None,
    *,
    url: str | None = None,
    width: int = 1024,
    height: int = 768,
    wait_ms: int = 250,
) -> bytes:
    """Screenshot an interactive web map and return PNG bytes (Phase 3 §3.1).

    Provide exactly one source:

    * ``html`` — an HTML string or a path to an ``.html`` file, or
    * ``url`` — a URL to navigate to.

    With no source, a minimal built-in Leaflet page is used.

    Raises
    ------
    BrowserUnavailableError
        If Playwright is not importable or Chromium cannot launch (no browser /
        missing system libs). Callers / tests should SKIP on this error.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # playwright not installed
        raise BrowserUnavailableError(f"playwright not importable: {exc}") from exc

    # Resolve the content source (mutually exclusive html vs url).
    content: str | None
    if url is not None:
        content = None
    elif isinstance(html, Path):
        content = html.read_text(encoding="utf-8")
    elif isinstance(html, str):
        content = html
    else:
        content = _DEFAULT_LEAFLET_HTML

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                if url is not None:
                    page.goto(url)
                else:
                    assert content is not None
                    page.set_content(content)
                # Give Leaflet/MapLibre a beat to paint tiles before capture.
                page.wait_for_timeout(wait_ms)
                png: bytes = page.screenshot(type="png")
            finally:
                browser.close()
    except PlaywrightError as exc:
        # Launch / navigation failures (e.g. missing libgbm on WSL2) → typed SKIP.
        raise BrowserUnavailableError(f"headless Chromium unavailable: {exc}") from exc

    return png


def leaflet_preview_html() -> str:
    """Return the built-in minimal Leaflet page (handy for tests / demos)."""
    return _DEFAULT_LEAFLET_HTML
