"""Profile stamp creation offers uploads and credit-priced generation."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Route, expect, sync_playwright


def test_profile_stamp_generation_errors_retry_and_capacity(tmp_path: Path) -> None:
    stamps: list[dict[str, str | int]] = []
    requests: list[dict[str, str]] = []
    state = {"owner": True, "fail": True}

    def serve(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/users/Alice":
            html = Path("templates/profile.html").read_text(encoding="utf-8")
            html = re.sub(r'<script[^>]+src="[^"]+"[^>]*></script>', '', html)
            html = re.sub(r'<link[^>]*>', '', html)
            html = html.replace('<head>', '<head><script>function setupProfileFriends() {}</script>')
            route.fulfill(body=html, content_type="text/html")
        elif path == "/api/users/Alice/profile":
            route.fulfill(json={"username": "Alice", "is_owner": state["owner"], "bio": "", "stats": None, "stamps": stamps})
        elif path == "/api/pricing":
            route.fulfill(json={"image_credit_rate": 4})
        elif path == "/api/users/me/stamps/generate":
            requests.append(route.request.post_data_json)
            if state["fail"]:
                route.fulfill(status=402, json={"detail": "Generation requires 4 credits. Top up on /deploy."})
            else:
                stamp = {"id": 42, "name": "Goblin", "url": "/api/stamps/42"}
                stamps.append(stamp)
                route.fulfill(json={"stamp": stamp, "credits_charged": 4, "credits": 6})
        elif path == "/api/stamps/42":
            route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><circle cx="40" cy="40" r="30" fill="green"/></svg>', content_type="image/svg+xml")
        else:
            route.abort()
            raise AssertionError(f"Unexpected request: {path}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", serve)
        page.goto("http://narratron.test/users/Alice")
        page.get_by_role("tab", name=re.compile("Stamps")).click()
        expect(page.get_by_role("button", name="Upload", exact=True)).to_be_hidden()
        page.locator("#stamp-upload summary").click()
        expect(page.get_by_role("button", name="Upload", exact=True)).to_be_visible()
        page.locator("#stamp-upload summary").focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("button", name="Upload", exact=True)).to_be_hidden()
        expect(page.locator("#stamp-generate-form")).to_be_hidden()
        page.locator("#stamp-generation summary").click()
        expect(page.locator("#stamp-generate-form")).to_be_visible()
        expect(page.locator("#stamp-generation-cost")).to_contain_text("4 credits per stamp")
        page.get_by_label("Stamp name", exact=True).fill("Goblin")
        page.get_by_label("Describe your stamp").fill("Goblin scout in a green cloak")
        page.get_by_role("button", name="Generate stamp", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("Generation requires 4 credits")
        expect(page.get_by_role("button", name="Generate stamp", exact=True)).to_be_enabled()
        expect(page.get_by_label("Describe your stamp")).to_have_value("Goblin scout in a green cloak")
        state["fail"] = False
        page.get_by_role("button", name="Generate stamp", exact=True).click()
        expect(page.locator(".stamp-title")).to_have_text("Goblin")
        expect(page.locator("#stamps")).to_be_visible()
        assert requests == [{"name": "Goblin", "prompt": "Goblin scout in a green cloak"}] * 2
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("#stamp-generate-form")).to_be_hidden()
        page.locator("#stamp-generation summary").focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("button", name="Generate stamp", exact=True)).to_be_visible()
        page.locator("#stamp-generation summary").click()
        expect(page.locator("#stamp-generate-form")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(tmp_path / "profile-stamps-mobile.png"), full_page=True)
        stamps[:] = [{"id": 42, "name": f"Stamp {index}", "url": "/api/stamps/42"} for index in range(10)]
        page.reload()
        page.get_by_role("tab", name=re.compile("Stamps")).click()
        page.locator("#stamp-upload summary").click()
        expect(page.get_by_text("You have reached your limit.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Generate stamp", exact=True)).to_have_count(0)
        state["owner"] = False
        stamps.clear()
        page.reload()
        page.get_by_role("tab", name=re.compile("Stamps")).click()
        expect(page.get_by_role("button", name="Generate stamp", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="Upload", exact=True)).to_have_count(0)
        assert errors == []
        browser.close()
