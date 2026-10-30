"""Exercise front-page joining with spoken codes and pasted keys."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Route, expect, sync_playwright
import pytest


@pytest.mark.parametrize("entry", ["ABC234", "abc234", "KEY-ABC234", " key-abc234 "])
@pytest.mark.parametrize("submit", ["button", "enter"])
def test_join_accepts_code_or_full_key(entry: str, submit: str) -> None:
    template = Path("templates/join_splash.html").read_text(encoding="utf-8")
    template = re.sub(r'<script[^>]+src="[^"]+"[^>]*></script>', "", template)
    template = re.sub(r'<link[^>]+href="https?://[^>]*>', "", template)
    template = template.replace(
        "<head>", "<head><script>function Splide() { this.mount = () => {}; }</script>"
    )
    submitted_keys: list[str] = []

    def serve(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/":
            route.fulfill(body=template, content_type="text/html")
        elif path == "/api/theaters/resolve-join-key":
            submitted_keys.append(route.request.post_data_json["join_key"])
            route.fulfill(json={"theater_id": "test-stage"})
        elif path == "/canvas":
            route.fulfill(body="<h1>Theater canvas</h1>", content_type="text/html")
        else:
            route.fulfill(body="")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page()
        page.route("**/*", serve)
        page.goto("http://narratron.test/")
        field = page.get_by_role("textbox", name="Theater code")
        expect(field).to_have_attribute("placeholder", "XXXXXX")
        field.fill(entry)
        if submit == "enter":
            field.press("Enter")
        else:
            page.get_by_role("button", name="Join", exact=True).click()
        expect(page).to_have_url("http://narratron.test/canvas?theater_id=test-stage")
        assert submitted_keys == ["KEY-ABC234"]
        browser.close()
