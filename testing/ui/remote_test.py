"""Exercise remote controls in a phone viewport with offline API fixtures."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, Route, sync_playwright


@pytest.fixture
def remote_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)

        def serve(route: Route) -> None:
            path = urlsplit(route.request.url).path
            files = {
                "/remote": ("templates/remote.html", "text/html"),
                "/static/js/remote.js": ("static/js/remote.js", "text/javascript"),
                "/static/js/device-navigation.js": ("static/js/device-navigation.js", "text/javascript"),
                "/static/css/remote.css": ("static/css/remote.css", "text/css"),
            }
            if path in files:
                filename, content_type = files[path]
                route.fulfill(body=Path(filename).read_text(encoding="utf-8"), content_type=content_type)
            elif path.endswith("/remote"):
                route.fulfill(json={"agent_running": True, "pinned": False, "music_pinned": False,
                                    "viewer_collab_enabled": False})
            else:
                route.fulfill(json={"status": "accepted"})

        page.route("**/*", serve)
        page.goto("http://remote.test/remote?theater_id=stage")
        page.wait_for_function("!document.getElementById('controls').disabled")
        yield page
        browser.close()


def test_phone_remote_sends_actions_and_instructions(remote_page: Page) -> None:
    page = remote_page
    with page.expect_request("**/orator-action") as action:
        page.get_by_role("button", name="New image").click()
    assert action.value.post_data_json == {"action": "new_image"}
    page.wait_for_function("!document.getElementById('controls').disabled")
    page.locator("#command").fill("A storm rolls in.")
    with page.expect_request("**/api/orator/command?*") as instruction:
        page.get_by_role("button", name="Send instruction").click()
    assert instruction.value.post_data_json == {"text": "A storm rolls in."}
    page.wait_for_function("document.getElementById('command').value === ''")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_remote_disables_controls_when_access_is_revoked(remote_page: Page) -> None:
    page = remote_page

    def revoked(route: Route) -> None:
        route.fulfill(status=403, json={"detail": "Only the active orator can use this remote."})

    page.route("**/api/theaters/stage/remote", revoked)
    page.locator("#connect-form button").click()
    page.get_by_text("Only the active orator can use this remote.").wait_for()
    assert page.get_by_role("button", name="New image").is_disabled()


def test_failed_instruction_preserves_draft(remote_page: Page) -> None:
    page = remote_page

    def reject(route: Route) -> None:
        route.fulfill(status=409, json={"detail": "The live agent is not connected."})

    page.route("**/api/orator/command?*", reject)
    page.locator("#command").fill("Keep this draft")
    page.get_by_role("button", name="Send instruction").click()
    page.get_by_text("The live agent is not connected.", exact=True).wait_for()
    assert page.locator("#command").input_value() == "Keep this draft"


def test_canvas_link_does_not_retain_join_key(remote_page: Page) -> None:
    page = remote_page
    page.locator("#theater").fill("https://narratron.app/canvas?theater_id=other&join_key=secret")
    page.locator("#connect-form button").click()
    assert page.url == "http://remote.test/remote?theater_id=other"
    assert page.locator("#canvas-link").get_attribute("href") == "/canvas?theater_id=other"


def test_stopped_agent_preserves_history_and_pin_controls(remote_page: Page) -> None:
    page = remote_page

    def stopped(route: Route) -> None:
        route.fulfill(json={"agent_running": False, "pinned": True, "music_pinned": True,
                            "viewer_collab_enabled": True})

    page.route("**/api/theaters/stage/remote", stopped)
    page.locator("#connect-form button").click()
    page.get_by_text("Connected · Start Narratron on the main canvas").wait_for()
    assert page.get_by_role("button", name="New image").is_disabled()
    assert page.get_by_role("button", name="Send instruction").is_disabled()
    assert page.get_by_role("button", name="← Previous", exact=True).is_enabled()
    assert page.get_by_role("button", name="Unpin scene").get_attribute("aria-pressed") == "true"
    assert page.get_by_role("button", name="Unpin music").is_enabled()
    with page.expect_request("**/collab") as collaboration:
        page.get_by_role("button", name="Audience collaboration on").click()
    assert collaboration.value.post_data_json == {"enabled": False}


@pytest.mark.parametrize("width,user_agent,mobile", [
    (390, "Desktop", True), (1280, "Desktop", False), (900, "iPhone Mobile", True),
])
def test_canvas_device_navigation(width: int, user_agent: str, mobile: bool) -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    # Exercise the actual early navigation script before canvas resources load.
    head = template[:template.index('<link rel="icon"')]
    navigation = Path("static/js/device-navigation.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 844}, user_agent=user_agent)

        def serve(route: Route) -> None:
            path = urlsplit(route.request.url).path
            if path == "/static/js/device-navigation.js":
                route.fulfill(body=navigation, content_type="text/javascript")
            else:
                route.fulfill(body=head + "</head><body>Canvas</body>" if path == "/canvas" else "Remote",
                              content_type="text/html")

        page.route("**/*", serve)
        page.goto("http://remote.test/canvas?theater_id=stage&join_key=SECRET")
        if mobile:
            page.wait_for_url("http://remote.test/remote?theater_id=stage")
        else:
            assert page.url == "http://remote.test/canvas?theater_id=stage&join_key=SECRET"
        browser.close()
