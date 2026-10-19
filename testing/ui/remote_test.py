"""Exercise remote controls in a phone viewport with offline API fixtures."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, Route, expect, sync_playwright


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
    page.reload()
    page.get_by_text("Only the active orator can use this remote.").wait_for()
    assert page.get_by_role("button", name="New image").is_disabled()


def test_preview_tracks_scene_changes_without_reloading_unchanged_images(remote_page: Page) -> None:
    page = remote_page
    scene = "first"
    images: list[str] = []

    def status(route: Route) -> None:
        route.fulfill(json={"agent_running": True, "pinned": False, "music_pinned": False,
                            "viewer_collab_enabled": False, "preview_url": f"/scenes/{scene}.svg"})

    def image(route: Route) -> None:
        images.append(route.request.url)
        route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="navy"/></svg>',
                      content_type="image/svg+xml")

    page.route("**/api/theaters/stage/remote", status)
    page.route("**/scenes/*", image)
    page.reload()
    assert images == []
    page.get_by_role("button", name="Preview canvas", exact=True).click()
    page.get_by_text("Current scene", exact=True).wait_for()
    assert page.locator("#preview-image").is_visible()
    page.get_by_role("button", name="Back to controls", exact=True).click()
    page.get_by_role("button", name="Pin scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    assert len(images) == 1
    scene = "second"
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    page.get_by_role("button", name="Preview canvas", exact=True).click()
    page.wait_for_function("document.getElementById('preview-image').getAttribute('src') === '/scenes/second.svg' && !document.getElementById('preview-image').hidden")
    assert len(images) == 2
    assert page.locator("#preview-image").evaluate("image => getComputedStyle(image).objectFit") == "contain"

    def revoked(route: Route) -> None:
        route.fulfill(status=403, json={"detail": "Only the active orator can use this remote."})

    page.route("**/api/theaters/stage/remote", revoked)
    page.get_by_role("button", name="Back to controls", exact=True).click()
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("document.getElementById('controls').disabled && document.getElementById('connection-status').dataset.connected === 'false'")
    page.get_by_role("button", name="Preview canvas", exact=True).click()
    page.get_by_text("Reconnect to see the canvas.").wait_for()
    assert page.locator("#preview-image").is_hidden()
    assert page.locator("#preview-image").get_attribute("src") is None


def test_preview_empty_state_keeps_controls_available(remote_page: Page) -> None:
    page = remote_page
    page.get_by_role("button", name="Preview canvas", exact=True).click()
    page.get_by_text("No scene on the canvas yet.").wait_for()
    assert page.locator("#preview-image").is_hidden()
    page.get_by_role("button", name="Back to controls", exact=True).click()
    assert page.get_by_role("button", name="Next scene", exact=True).is_enabled()


@pytest.mark.parametrize("width", [320, 390, 700])
def test_preview_is_a_separate_view_with_back_navigation(remote_page: Page, width: int) -> None:
    page = remote_page
    page.set_viewport_size({"width": width, "height": 568})
    page.locator("#command").fill("Keep my direction draft")
    page.get_by_role("button", name="Preview canvas", exact=True).click()
    page.wait_for_url("**#preview")
    expect(page.locator("#remote-controls")).to_be_hidden()
    expect(page.locator("#canvas-preview")).to_be_visible()
    assert page.locator("#close-preview").evaluate("button => button === document.activeElement")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.go_back()
    expect(page.locator("#canvas-preview")).to_be_hidden()
    assert page.locator("#command").input_value() == "Keep my direction draft"
    assert page.locator("#open-preview").evaluate("button => button === document.activeElement")
    page.go_forward()
    expect(page.locator("#canvas-preview")).to_be_visible()
    page.reload()
    expect(page.locator("#canvas-preview")).to_be_visible()
    page.get_by_role("button", name="Back to controls", exact=True).click()
    expect(page.locator("#remote-controls")).to_be_visible()
    assert page.url == "http://remote.test/remote?theater_id=stage"


def swipe_preview(page: Page, dx: int, dy: int, cancel: bool = False) -> None:
    frame = page.locator('.preview-frame').bounding_box()
    assert frame is not None
    x = frame['x'] + frame['width'] / 2
    y = frame['y'] + frame['height'] / 2
    session = page.context.new_cdp_session(page)
    session.send('Input.dispatchTouchEvent', {
        'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}],
    })
    session.send('Input.dispatchTouchEvent', {
        'type': 'touchMove', 'touchPoints': [{'x': x + dx, 'y': y + dy}],
    })
    session.send('Input.dispatchTouchEvent', {
        'type': 'touchCancel' if cancel else 'touchEnd', 'touchPoints': [],
    })
    session.detach()


@pytest.mark.parametrize('direct_link', [False, True])
def test_swipe_down_dismisses_preview(remote_page: Page, direct_link: bool) -> None:
    page = remote_page
    if direct_link:
        page.goto('http://remote.test/remote?theater_id=stage#preview')
    else:
        page.locator('#command').fill('Keep my draft')
        page.get_by_role('button', name='Preview canvas', exact=True).click()
    expect(page.locator('#canvas-preview')).to_be_visible()
    swipe_preview(page, dx=10, dy=140)
    expect(page.locator('#canvas-preview')).to_be_hidden()
    expect(page.locator('#remote-controls')).to_be_visible()
    assert page.url == 'http://remote.test/remote?theater_id=stage'
    assert page.locator('#open-preview').evaluate('button => button === document.activeElement')
    if not direct_link:
        assert page.locator('#command').input_value() == 'Keep my draft'


@pytest.mark.parametrize('dx,dy,cancel', [
    (0, 30, False), (0, -140, False), (140, 10, False), (100, 100, False), (0, 140, True),
])
def test_preview_ignores_other_or_cancelled_gestures(
    remote_page: Page, dx: int, dy: int, cancel: bool,
) -> None:
    page = remote_page
    page.get_by_role('button', name='Preview canvas', exact=True).click()
    expect(page.locator('#canvas-preview')).to_be_visible()
    swipe_preview(page, dx, dy, cancel)
    assert page.url.endswith('#preview')
    expect(page.locator('#canvas-preview')).to_be_visible()
    swipe_preview(page, 0, 140)
    expect(page.locator('#canvas-preview')).to_be_hidden()


def test_preview_retries_failed_image_without_disabling_controls(remote_page: Page) -> None:
    page = remote_page
    failed = True

    def status(route: Route) -> None:
        route.fulfill(json={"agent_running": True, "pinned": False, "music_pinned": False,
                            "viewer_collab_enabled": False, "preview_url": "/scene.svg"})

    def image(route: Route) -> None:
        if failed:
            route.fulfill(status=404, body="Missing")
        else:
            route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"/>',
                          content_type="image/svg+xml")

    page.route("**/api/theaters/stage/remote", status)
    page.route("**/scene.svg", image)
    page.reload()
    page.get_by_role("button", name="Preview canvas", exact=True).click()
    page.get_by_text("Scene could not load. Retrying…").wait_for()
    page.get_by_role("button", name="Back to controls", exact=True).click()
    assert page.get_by_role("button", name="Next scene", exact=True).is_enabled()
    failed = False
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    page.get_by_role("button", name="Preview canvas", exact=True).click()
    page.get_by_text("Current scene", exact=True).wait_for()
    assert page.locator("#preview-image").is_visible()


def test_failed_instruction_preserves_draft(remote_page: Page) -> None:
    page = remote_page

    def reject(route: Route) -> None:
        route.fulfill(status=409, json={"detail": "The live agent is not connected."})

    page.route("**/api/orator/command?*", reject)
    page.locator("#command").fill("Keep this draft")
    page.get_by_role("button", name="Send instruction").click()
    page.get_by_text("The live agent is not connected.", exact=True).wait_for()
    assert page.locator("#command").input_value() == "Keep this draft"


@pytest.mark.parametrize("selector,path", [
    ('[data-action="new_image"]', "**/orator-action"),
    ("#scene-pin", "**/orator-action"),
    ("#agent-toggle", "**/agent/stop"),
    ("#collaboration", "**/collab"),
    ('#command-form button', "**/api/orator/command?*"),
])
def test_remote_traces_only_pending_button(remote_page: Page, selector: str, path: str) -> None:
    page = remote_page
    pending: list[Route] = []

    def hold(route: Route) -> None:
        pending.append(route)

    page.route(path, hold)
    if selector == '#command-form button':
        page.locator("#command").fill("A quiet moment.")
    button = page.locator(selector)
    button.click()
    page.wait_for_function("document.querySelectorAll('button[aria-busy=true]').length === 1")
    assert button.get_attribute("aria-busy") == "true"
    assert button.evaluate("button => getComputedStyle(button, '::before').animationName") == "pending-trace"
    assert page.locator("#feedback").bounding_box()["height"] <= 1
    assert page.locator("#controls").evaluate("controls => controls.disabled")
    assert len(pending) == 1
    pending[0].fulfill(json={"status": "accepted"})
    page.wait_for_function("!document.querySelector('button[aria-busy=true]')")
    assert not page.locator("#controls").evaluate("controls => controls.disabled")


def test_remote_failure_clears_trace_and_marks_button(remote_page: Page) -> None:
    page = remote_page

    def reject(route: Route) -> None:
        route.fulfill(status=409, json={"detail": "Try another scene."})

    page.route("**/orator-action", reject)
    button = page.get_by_role("button", name="New image")
    button.click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    assert button.get_attribute("aria-busy") is None
    assert button.get_attribute("data-error") == "Try another scene."
    assert button.get_attribute("aria-describedby") == "feedback"


def test_canvas_link_does_not_retain_join_key(remote_page: Page) -> None:
    page = remote_page
    page.goto("http://remote.test/remote?theater_id=other&join_key=secret")
    assert page.locator("#canvas-link").get_attribute("href") == "/canvas?theater_id=other"
    assert page.locator("#theater").count() == 0
    assert page.locator("header #theater-id").inner_text() == "other"


def test_stopped_agent_preserves_history_and_pin_controls(remote_page: Page) -> None:
    page = remote_page

    def stopped(route: Route) -> None:
        route.fulfill(json={"agent_running": False, "pinned": True, "music_pinned": True,
                            "viewer_collab_enabled": True})

    page.route("**/api/theaters/stage/remote", stopped)
    page.reload()
    page.get_by_text("Connected · Agent off").wait_for()
    assert page.get_by_role("button", name="New image").is_disabled()
    assert page.get_by_role("button", name="Send instruction").is_disabled()
    assert page.get_by_role("button", name="Previous scene", exact=True).is_enabled()
    assert page.get_by_role("button", name="Unpin scene").get_attribute("aria-pressed") == "true"
    assert page.get_by_role("button", name="Unpin music").is_enabled()
    with page.expect_request("**/collab") as collaboration:
        page.get_by_role("button", name="Turn collaboration off").click()
    assert collaboration.value.post_data_json == {"enabled": False}


def test_remote_agent_toggle_starts_and_stops_agent(remote_page: Page) -> None:
    page = remote_page
    running = True

    def status(route: Route) -> None:
        route.fulfill(json={"agent_running": running, "pinned": False, "music_pinned": False,
                            "viewer_collab_enabled": False})

    def toggle(route: Route) -> None:
        nonlocal running
        running = route.request.url.endswith("/start")
        route.fulfill(json={"agent_running": running})

    page.route("**/api/theaters/stage/remote", status)
    page.route("**/api/theaters/stage/agent/*", toggle)
    running_color = page.locator("#agent-toggle").evaluate("button => getComputedStyle(button).color")
    with page.expect_request("**/agent/stop") as stop:
        page.get_by_role("button", name="Turn agent off").click()
    assert stop.value.method == "POST"
    page.wait_for_function("!document.getElementById('controls').disabled")
    assert page.get_by_role("button", name="New image").is_disabled()
    assert page.locator("#agent-toggle").get_attribute("aria-pressed") == "false"
    assert page.locator("#agent-toggle").evaluate("button => getComputedStyle(button).color") != running_color
    with page.expect_request("**/agent/start") as start:
        page.get_by_role("button", name="Turn agent on").click()
    assert start.value.method == "POST"
    page.wait_for_function("!document.getElementById('controls').disabled")
    assert page.get_by_role("button", name="New image").is_enabled()
    assert page.locator("#agent-toggle").get_attribute("aria-pressed") == "true"


@pytest.mark.parametrize("width", [320, 390, 700])
def test_remote_bars_fit_on_one_line(remote_page: Page, width: int) -> None:
    page = remote_page
    page.set_viewport_size({"width": width, "height": 568})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("""() => [...document.querySelectorAll('.bar')].every(bar => {
        const children = [...bar.children].filter(child => !child.classList.contains('sr-only'));
        const bounds = children.map(child => child.getBoundingClientRect());
        return bounds.every(rect => Math.abs(rect.top + rect.height / 2 -
            bounds[0].top - bounds[0].height / 2) < 1) && bar.scrollWidth <= bar.clientWidth;
    })""")
    assert page.locator("#controls").bounding_box()["height"] < 400
    assert page.locator("#canvas-preview").is_hidden()
    assert page.locator("#open-preview").bounding_box()["y"] >= page.locator("#controls").bounding_box()["y"] + page.locator("#controls").bounding_box()["height"]
    assert page.locator("main").bounding_box()["height"] < 568
    assert page.locator(".desk-heading").count() == 0
    assert page.locator('[aria-labelledby="agent-title"] #connection-status').count() == 1
    assert page.locator("header #theater-id").inner_text() == "stage"
    for power in page.locator(".power-button").all():
        assert power.bounding_box()["width"] >= 60
        assert power.bounding_box()["height"] >= 52
    for media, pin, new_action in [("scene", "scene-pin", "new_image"), ("music", "music-pin", "new_music")]:
        buttons = page.locator(f'[aria-labelledby="{media}-title"] button')
        assert buttons.nth(1).get_attribute("id") == pin
        assert buttons.last.get_attribute("data-action") == new_action
        assert buttons.nth(0).get_attribute("aria-label").startswith("Previous")
        assert buttons.nth(2).get_attribute("aria-label").startswith("Next")


def test_login_gate_opens_login_and_reloads_original_theater() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        logged_in = False

        def serve(route: Route) -> None:
            nonlocal logged_in
            path = urlsplit(route.request.url).path
            if path == "/remote":
                route.fulfill(body=Path("templates/remote_login.html").read_text(encoding="utf-8")
                              if not logged_in else "Signed in", content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(body=Path(path.lstrip("/")).read_text(encoding="utf-8"),
                              content_type="text/javascript" if path.endswith(".js") else "text/css")
            elif path == "/api/auth/me":
                route.fulfill(json={"authenticated": logged_in,
                                    "user": {"id": 2, "username": "Orator"} if logged_in else None})
            elif path == "/api/auth/login":
                logged_in = True
                route.fulfill(json={"user": {"id": 2, "username": "Orator"}})
            else:
                route.abort()
                pytest.fail(f"Unexpected login gate request: {route.request.url}")

        page.route("**/*", serve)
        page.goto("http://remote.test/remote?theater_id=stage")
        page.locator("#loginUserEmail").fill("Orator")
        page.locator("#loginPassword").fill("password")
        page.get_by_role("button", name="Log In", exact=True).click()
        page.get_by_text("Signed in", exact=True).wait_for()
        assert page.url == "http://remote.test/remote?theater_id=stage"
        browser.close()


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
