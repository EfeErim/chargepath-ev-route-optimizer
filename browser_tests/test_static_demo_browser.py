"""Smoke test of the static demo in real Chromium with real Pyodide and the real Python planner.

Not part of the offline gate: it needs ``pip install -e ".[dev,browser]"``,
``python -m playwright install chromium``, and network access to the Pyodide CDN. Map tiles are
blocked on purpose, so the test also shows that planning does not depend on the basemap.

    python -m pytest -q browser_tests
"""

from __future__ import annotations

import functools
import importlib.util
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import ConsoleMessage, Page, Route, expect, sync_playwright

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TILE_HOST = "tile.openstreetmap.org"
# Pyodide, its runtime, and the planner's first run; generous for a cold CDN cache in CI.
PLANNER_TIMEOUT_MS = 120_000


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    spec = importlib.util.spec_from_file_location(
        "build_static_demo", REPOSITORY_ROOT / "scripts/build_static_demo.py"
    )
    assert spec is not None and spec.loader is not None
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    site = tmp_path_factory.mktemp("static-demo") / "site"
    build.build(site)
    handler = functools.partial(_QuietHandler, directory=str(site))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser.new_page(viewport={"width": 1280, "height": 800})
        finally:
            browser.close()


def test_fixture_plan_runs_in_pyodide_with_tiles_blocked(page: Page, site_url: str) -> None:
    unexpected: list[str] = []
    blocked_tiles: list[str] = []

    def block_tile(route: Route) -> None:
        blocked_tiles.append(route.request.url)
        route.abort()

    def on_console(message: ConsoleMessage) -> None:
        # Aborted tile requests log a resource error; anything else is a real failure.
        if message.type == "error" and TILE_HOST not in message.location.get("url", ""):
            unexpected.append(f"console: {message.text}")

    page.route(f"https://{TILE_HOST}/**", block_tile)
    page.on("pageerror", lambda error: unexpected.append(f"pageerror: {error}"))
    page.on("console", on_console)

    page.goto(site_url)
    expect(page.locator("#modePill")).to_have_text("Demo mode", timeout=PLANNER_TIMEOUT_MS)
    expect(page.locator("#dataLabel")).to_have_text("Synthetic demo data · not live")
    plan_button = page.locator("#planButton")
    expect(plan_button).to_be_enabled(timeout=PLANNER_TIMEOUT_MS)

    plan_button.click()

    expect(page.locator("#resultContent")).to_be_visible(timeout=PLANNER_TIMEOUT_MS)
    expect(page.locator("#resultFailure")).to_be_hidden()
    # 205.2 modeled minutes: the fixture's exact fastest plan, computed by the Python planner.
    expect(page.locator("#totalTime")).to_have_text("3 hr 25 min")
    expect(page.locator("#routeTitle")).to_contain_text("Fast Hub")
    expect(page.locator("#optionTabs [role=tab]")).to_have_count(4)
    expect(page.locator(".result-status")).to_have_text("Estimated")
    expect(page.locator(".data-note")).to_contain_text("Charging and arrival times are estimates")
    expect(page.locator(".charge-fallback").first).to_contain_text("structure only")

    # The bridge really loaded Pyodide and answered through the Python package.
    assert page.evaluate("typeof loadPyodide") == "function"
    assert page.evaluate("typeof window.chargepathApi") == "object"
    assert blocked_tiles, "the map requested no tiles, so the blocking path was not exercised"
    assert not unexpected, unexpected
