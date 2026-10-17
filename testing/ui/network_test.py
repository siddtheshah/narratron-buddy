"""Verify that the browser firewall blocks escaped mocks before network access."""

import sys

from playwright.sync_api import Error, Route, sync_playwright
import pytest


def test_browser_allows_fulfilled_routes_and_rejects_unmocked_requests(
    offline_browser_contexts: list[str],
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        try:
            page = browser.new_page()

            def serve_page(route: Route) -> None:
                route.fulfill(body="<h1>Offline</h1>")

            page.route("https://narratron.test/", serve_page)
            page.goto("https://narratron.test/")
            assert page.locator("h1").inner_text() == "Offline"
            page.evaluate("window.probeSocket = new WebSocket('wss://unmocked.invalid/ws')")
            page.wait_for_function("window.probeSocket.readyState === WebSocket.OPEN")
            assert offline_browser_contexts == ["wss://unmocked.invalid/ws"]
            with pytest.raises(Error, match="ERR_BLOCKED_BY_CLIENT"):
                page.goto("https://unmocked.invalid/")
            assert offline_browser_contexts[-1] == "https://unmocked.invalid/"
            # This test deliberately exercises the guard; acknowledge its event.
            offline_browser_contexts.clear()
        finally:
            browser.close()
