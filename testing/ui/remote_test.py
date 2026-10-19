"""Exercise remote controls in a phone viewport with offline API fixtures."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import json
import pytest
from playwright.sync_api import Page, Route, WebSocketRoute, expect, sync_playwright


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
            elif path.endswith("/scenes") or path.endswith("/playlists"):
                route.fulfill(json=[])
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
    page.get_by_role("button", name="Keyboard").click()
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
    page.get_by_role("tab", name="Preview", exact=True).click()
    page.get_by_text("Current scene", exact=True).wait_for()
    assert page.locator("#preview-image").is_visible()
    page.get_by_role("tab", name="Control Panel", exact=True).click()
    page.get_by_role("button", name="Pin scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    assert len(images) == 1
    scene = "second"
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    page.get_by_role("tab", name="Preview", exact=True).click()
    page.wait_for_function("document.getElementById('preview-image').getAttribute('src') === '/scenes/second.svg' && !document.getElementById('preview-image').hidden")
    assert len(images) == 2
    assert page.locator("#preview-image").evaluate("image => getComputedStyle(image).objectFit") == "contain"

    def revoked(route: Route) -> None:
        route.fulfill(status=403, json={"detail": "Only the active orator can use this remote."})

    page.route("**/api/theaters/stage/remote", revoked)
    page.get_by_role("tab", name="Control Panel", exact=True).click()
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("document.getElementById('controls').disabled && document.getElementById('connection-status').dataset.connected === 'false'")
    page.get_by_role("tab", name="Preview", exact=True).click()
    page.get_by_text("Reconnect to see the canvas.").wait_for()
    assert page.locator("#preview-image").is_hidden()
    assert page.locator("#preview-image").get_attribute("src") is None


def test_preview_empty_state_keeps_controls_available(remote_page: Page) -> None:
    page = remote_page
    page.get_by_role("tab", name="Preview", exact=True).click()
    page.get_by_text("No scene on the canvas yet.").wait_for()
    assert page.locator("#preview-image").is_hidden()
    page.get_by_role("tab", name="Control Panel", exact=True).click()
    assert page.get_by_role("button", name="Next scene", exact=True).is_enabled()


@pytest.mark.parametrize("width", [320, 390, 700])
def test_preview_is_a_separate_view_with_back_navigation(remote_page: Page, width: int) -> None:
    page = remote_page
    page.set_viewport_size({"width": width, "height": 568})
    page.get_by_role("button", name="Keyboard").click()
    page.locator("#command").fill("Keep my direction draft")
    page.locator("#command-modal-close").click()
    page.get_by_role("tab", name="Preview", exact=True).click()
    page.wait_for_url("**#preview")
    expect(page.locator("#remote-controls")).to_be_hidden()
    expect(page.locator("#canvas-preview")).to_be_visible()
    assert page.locator("#preview-tab").evaluate("button => button === document.activeElement")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.go_back()
    expect(page.locator("#canvas-preview")).to_be_hidden()
    assert page.locator("#command").input_value() == "Keep my direction draft"
    assert page.locator("#controls-tab").evaluate("button => button === document.activeElement")
    page.go_forward()
    expect(page.locator("#canvas-preview")).to_be_visible()
    page.reload()
    expect(page.locator("#canvas-preview")).to_be_visible()
    page.get_by_role("tab", name="Control Panel", exact=True).click()
    expect(page.locator("#remote-controls")).to_be_visible()
    assert page.url == "http://remote.test/remote?theater_id=stage#controls"


def swipe_preview(page: Page, dx: int, dy: int, cancel: bool = False) -> None:
    expect(page.locator('.preview-frame')).to_be_visible()
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


def test_horizontal_swipes_switch_all_tabs_and_preserve_draft(remote_page: Page) -> None:
    page = remote_page
    page.get_by_role("button", name="Keyboard").click()
    page.locator('#command').fill('Keep my draft')
    page.locator("#command-modal-close").click()
    page.get_by_role('tab', name='Preview', exact=True).click()
    swipe_preview(page, 140, 10)
    expect(page.get_by_role('tab', name='Control Panel')).to_have_attribute('aria-selected', 'true')
    swipe_panel(page, 140, 10)
    expect(page.get_by_role('tab', name='Asset Manager')).to_have_attribute('aria-selected', 'true')
    swipe_panel(page, -140, 10)
    expect(page.get_by_role('tab', name='Control Panel')).to_have_attribute('aria-selected', 'true')
    assert page.locator('#command').input_value() == 'Keep my draft'
    swipe_panel(page, -140, 10)
    expect(page.get_by_role('tab', name='Preview', exact=True)).to_have_attribute('aria-selected', 'true')


def swipe_panel(page: Page, dx: int, dy: int, selector: str = '#remote-views') -> None:
    page.locator(selector).evaluate("""(element, delta) => {
        const start = new Event('touchstart', {bubbles: true});
        start.touches = [{clientX: 200, clientY: 500}];
        element.dispatchEvent(start);
        const end = new Event('touchend', {bubbles: true});
        end.touches = [];
        end.changedTouches = [{clientX: 200 + delta.dx, clientY: 500 + delta.dy}];
        element.dispatchEvent(end);
    }""", {'dx': dx, 'dy': dy})


@pytest.mark.parametrize('dx,dy,cancel', [
    (0, 30, False), (0, -140, False), (10, 140, False), (100, 100, False), (140, 0, True),
])
def test_preview_ignores_vertical_small_or_cancelled_gestures(
    remote_page: Page, dx: int, dy: int, cancel: bool,
) -> None:
    page = remote_page
    page.get_by_role('tab', name='Preview', exact=True).click()
    swipe_preview(page, dx, dy, cancel)
    expect(page.get_by_role('tab', name='Preview', exact=True)).to_have_attribute('aria-selected', 'true')


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
    page.get_by_role("tab", name="Preview", exact=True).click()
    page.get_by_text("Scene could not load. Retrying…").wait_for()
    page.get_by_role("tab", name="Control Panel", exact=True).click()
    assert page.get_by_role("button", name="Next scene", exact=True).is_enabled()
    failed = False
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    page.get_by_role("tab", name="Preview", exact=True).click()
    page.get_by_text("Current scene", exact=True).wait_for()
    assert page.locator("#preview-image").is_visible()


def test_failed_instruction_preserves_draft(remote_page: Page) -> None:
    page = remote_page

    def reject(route: Route) -> None:
        route.fulfill(status=409, json={"detail": "The live agent is not connected."})

    page.route("**/api/orator/command?*", reject)
    page.get_by_role("button", name="Keyboard").click()
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
        page.get_by_role("button", name="Keyboard").click()
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
    assert page.get_by_role("button", name="Keyboard").is_disabled()
    assert page.get_by_role("button", name="Microphone").is_disabled()
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
    assert page.locator("#controls-tab").get_attribute("aria-selected") == "true"
    assert page.locator(".remote-tabs").bounding_box()["width"] <= width
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


def test_asset_carousels_load_and_push_scenes_and_music(remote_page: Page) -> None:
    page = remote_page
    page.route('**/api/theaters/stage/scenes', lambda route: route.fulfill(json=[
        {'id': 'forest', 'name': 'Forest', 'description': 'A misty wood', 'url': '/forest.svg'},
    ]))
    page.route('**/forest.svg', lambda route: route.fulfill(
        body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"/>', content_type='image/svg+xml'))
    page.route('**/api/theaters/stage/playlists', lambda route: route.fulfill(json=[
        {'id': 'playlist:rain', 'name': 'Rain', 'tracks': [
            {'id': 'track:storm', 'name': 'Storm', 'url': '/storm.mp3'},
        ]},
    ]))
    page.get_by_role('tab', name='Asset Manager').click()
    expect(page.get_by_role('heading', name='Forest')).to_be_visible()
    expect(page.get_by_role('heading', name='Rain')).to_be_visible()
    expect(page.get_by_role('heading', name='Storm')).to_be_visible()
    expect(page.get_by_text('A misty wood')).to_have_count(0)
    assert page.locator('.asset-card button').count() == 0
    forest_card = page.locator('.asset-card').filter(has=page.get_by_role('heading', name='Forest', exact=True))
    with page.expect_request('**/scenes/push') as scene:
        forest_card.dblclick()
    assert scene.value.post_data_json == {'scene_id': 'forest'}
    page.wait_for_function("!document.getElementById('controls').disabled")
    rain_card = page.locator('.asset-card').filter(has=page.get_by_role('heading', name='Rain', exact=True))
    with page.expect_request('**/playlists/push') as music:
        rain_card.dblclick()
    assert music.value.post_data_json == {'id': 'playlist:rain'}
    page.wait_for_function("!document.getElementById('controls').disabled")
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    swipe_panel(page, -150, 0, '#scenes-carousel')
    expect(page.get_by_role('tab', name='Asset Manager')).to_have_attribute('aria-selected', 'true')


def test_asset_loading_failure_can_be_retried(remote_page: Page) -> None:
    page = remote_page
    page.route('**/api/theaters/stage/scenes', lambda route: route.fulfill(status=503, json={'detail': 'Try again later.'}))
    page.get_by_role('tab', name='Asset Manager').click()
    expect(page.locator('#asset-status')).to_have_text('Try again later.')
    page.route('**/api/theaters/stage/scenes', lambda route: route.fulfill(json=[]))
    page.get_by_role('button', name='Retry loading assets').click()
    expect(page.locator('#asset-status')).to_have_text('No scenes available in this theater yet.')


def test_tabs_support_keyboard_navigation(remote_page: Page) -> None:
    page = remote_page
    expect(page.get_by_role('tab', name='Control Panel')).to_have_attribute('aria-selected', 'true')
    assert page.locator('#open-preview').count() == 0
    page.get_by_role('tab', name='Control Panel').focus()
    page.keyboard.press('ArrowLeft')
    expect(page.get_by_role('tab', name='Asset Manager')).to_be_focused()
    page.keyboard.press('End')
    expect(page.get_by_role('tab', name='Preview', exact=True)).to_be_focused()


def test_action_error_surfaces_as_dismissable_toast(remote_page: Page) -> None:
    page = remote_page

    def reject(route: Route) -> None:
        route.fulfill(status=409, json={"detail": "Unable to switch scene right now."})

    page.route("**/orator-action", reject)
    expect(page.locator("#toast")).to_be_hidden()
    page.get_by_role("button", name="New image").click()
    page.wait_for_function("!document.getElementById('controls').disabled")

    toast = page.locator("#toast")
    expect(toast).to_be_visible()
    expect(page.locator("#toast-message")).to_have_text("Unable to switch scene right now.")

    page.get_by_role("button", name="Dismiss error").click()
    expect(toast).to_be_hidden()


def test_empty_command_surfaces_as_toast_dismissable_with_escape(remote_page: Page) -> None:
    page = remote_page
    expect(page.locator("#toast")).to_be_hidden()
    page.get_by_role("button", name="Keyboard").click()
    page.locator("#command").fill("")
    page.get_by_role("button", name="Send instruction").click()

    toast = page.locator("#toast")
    expect(toast).to_be_visible()
    expect(page.locator("#toast-message")).to_have_text("Enter an instruction first.")

    page.keyboard.press("Escape")
    expect(toast).to_be_hidden()


def test_asset_loading_error_surfaces_as_toast(remote_page: Page) -> None:
    page = remote_page
    page.route("**/api/theaters/stage/scenes", lambda route: route.fulfill(status=500, json={"detail": "Scenes failed to load."}))
    expect(page.locator("#toast")).to_be_hidden()
    page.get_by_role("tab", name="Asset Manager").click()

    toast = page.locator("#toast")
    expect(toast).to_be_visible()
    expect(page.locator("#toast-message")).to_have_text("Scenes failed to load.")

    page.get_by_role("button", name="Dismiss error").click()
    expect(toast).to_be_hidden()


def test_keyboard_button_pops_up_modal_and_closes(remote_page: Page) -> None:
    page = remote_page
    modal = page.locator("#command-modal")
    expect(modal).to_be_hidden()

    keyboard_btn = page.get_by_role("button", name="Keyboard")
    keyboard_btn.click()
    expect(modal).to_be_visible()
    expect(keyboard_btn).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#command")).to_be_focused()

    page.locator("#command-cancel-btn").click()
    expect(modal).to_be_hidden()
    expect(keyboard_btn).to_have_attribute("aria-expanded", "false")

    keyboard_btn.click()
    expect(modal).to_be_visible()
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()


def test_microphone_denied_permission_shows_toast(remote_page: Page) -> None:
    page = remote_page
    # Deny microphone permission
    page.context.clear_permissions()
    mic_btn = page.get_by_role("button", name="Microphone")
    expect(mic_btn).to_be_visible()
    mic_btn.click()
    page.locator("#toast").wait_for(state="visible")
    assert mic_btn.get_attribute("aria-pressed") == "false"


def test_microphone_button_streams_audio_and_stops_on_release(remote_page: Page) -> None:
    page = remote_page
    messages: list[str] = []

    def mock_agent_socket(ws: WebSocketRoute) -> None:
        def on_msg(msg: str | bytes) -> None:
            if type(msg) is str:
                messages.append(msg)
            elif type(msg) is bytes:
                messages.append(msg.decode("utf-8", errors="ignore"))

        ws.on_message(on_msg)
        ws.send(json.dumps({"setupComplete": True}))

    page.route_web_socket("**/ws/**", mock_agent_socket)

    page.evaluate("""() => {
        class MockEncoder {
            constructor(init) {
                this.init = init;
                this.state = 'configured';
            }
            configure() {}
            encode() {
                this.init.output({
                    byteLength: 4,
                    copyTo: (arr) => arr.set([1, 2, 3, 4])
                });
            }
            flush() { return Promise.resolve(); }
            close() { this.state = 'closed'; }
        }
        window.AudioEncoder = MockEncoder;
        window.AudioData = class {
            constructor() {}
            close() {}
        };
        Object.defineProperty(navigator, 'mediaDevices', {
            value: {
                getUserMedia: () => Promise.resolve({
                    getTracks: () => [{ stop() {} }]
                })
            },
            configurable: true,
        });
        window.AudioContext = class {
            constructor() {
                this.state = 'running';
                this.destination = {};
                this.audioWorklet = {
                    addModule: () => Promise.resolve()
                };
            }
            createMediaStreamSource() {
                return { connect() {} };
            }
            close() { this.state = 'closed'; return Promise.resolve(); }
        };
        window.AudioWorkletNode = class {
            constructor() {
                this.port = {
                    onmessage: null,
                    postMessage() {}
                };
            }
            connect() {}
            disconnect() {}
        };
    }""")

    mic_btn = page.get_by_role("button", name="Microphone")
    expect(mic_btn).to_be_visible()

    mic_btn.click()
    page.wait_for_function("document.getElementById('mic-btn').getAttribute('aria-pressed') === 'true'")
    expect(mic_btn).to_have_text("Listening…")

    mic_btn.click()
    page.wait_for_function("document.getElementById('mic-btn').getAttribute('aria-pressed') === 'false'")
    expect(mic_btn).to_have_text("Microphone")

    parsed = [json.loads(m) for m in messages if m.startswith("{")]
    types = [p.get("type") for p in parsed]
    assert "activity_start" in types
    assert "activity_end" in types


