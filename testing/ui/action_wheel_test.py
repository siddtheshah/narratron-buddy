from pathlib import Path
import json
from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error, Page, Route, sync_playwright


@pytest.fixture
def wheel_page() -> Iterator[Page]:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    source = Path("static/js/action-wheel.js").read_text(encoding="utf-8").replace("export ", "")
    markup = template[template.index('    <style>\n        #orator-action-wheel'):template.index('    <div id="action-wheel-status"')]
    script = source + '''window.sent = []; window.isOrator = true;
        window.wheelController = initializeActionWheel({isOrator: () => window.isOrator,
            sendAction: async action => { window.sent.push(action); }});'''
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 600})

        def serve(route: Route) -> None:
            route.fulfill(body=markup + '''<button id="action-wheel-rebind"><span id="action-wheel-binding-label"></span></button>
                <div id="action-wheel-status" hidden></div><input id="text">'''
                + '<script>' + script + '</script>', content_type="text/html")

        page.route("**/*", serve)
        page.goto("http://wheel.test/")
        yield page
        browser.close()


@pytest.mark.parametrize("button,name", [("middle", "Middle"), ("right", "Right"),
                                       ("back", "Back"), ("forward", "Forward")])
def test_freeform_native_mouse_rebinding(wheel_page: Page, button: str, name: str) -> None:
    page = wheel_page
    masks = {"left": 1, "middle": 4, "right": 2, "back": 8, "forward": 16}
    cdp = page.context.new_cdp_session(page)
    page.click("#action-wheel-rebind")
    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "button": button, "buttons": masks[button], "x": 400, "y": 300, "clickCount": 1})
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "button": button, "buttons": 0, "x": 400, "y": 300, "clickCount": 1})
    assert name in page.locator("#action-wheel-binding-label").inner_text()
    assert page.locator("#orator-action-wheel").is_hidden()
    saved = page.evaluate("JSON.parse(localStorage.getItem('narratron_action_wheel_binding'))")
    assert saved["button"] == {"left": 0, "middle": 1, "right": 2, "back": 3, "forward": 4}[button]
    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "button": button, "buttons": masks[button], "x": 400, "y": 300, "clickCount": 1})
    assert page.locator("#orator-action-wheel").is_visible()
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "button": "none", "buttons": masks[button], "x": 340, "y": 260})
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "button": button, "buttons": 0, "x": 340, "y": 260, "clickCount": 1})
    page.wait_for_function("window.sent.length === 1")
    assert page.evaluate("window.sent") == ["previous_image"]
    page.reload()
    assert name in page.locator("#action-wheel-binding-label").inner_text()


def test_left_click_cannot_be_bound(wheel_page: Page) -> None:
    page = wheel_page
    page.click("#action-wheel-rebind")
    page.mouse.click(400, 300, button="left")
    assert "not left" in page.locator("#action-wheel-binding-label").inner_text()
    assert page.evaluate("localStorage.getItem('narratron_action_wheel_binding')") is None
    page.mouse.click(400, 300, button="middle")
    assert "Middle mouse" in page.locator("#action-wheel-binding-label").inner_text()
    page.mouse.move(400, 300)
    page.mouse.down(button="left")
    assert page.locator("#orator-action-wheel").is_hidden()
    page.mouse.up(button="left")


def test_saved_left_click_binding_falls_back_to_right(wheel_page: Page) -> None:
    page = wheel_page
    page.evaluate("""localStorage.setItem('narratron_action_wheel_binding', JSON.stringify(
        {type: 'mouse', button: 0, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false}))""")
    page.reload()
    assert "Action Wheel: Right mouse" in page.locator("#action-wheel-binding-label").inner_text()


def test_keyboard_combo_rebinding_and_cancel(wheel_page: Page) -> None:
    page = wheel_page
    page.click("#action-wheel-rebind")
    page.keyboard.press("Control+Shift+K")
    assert "Ctrl + Shift + K" in page.locator("#action-wheel-binding-label").inner_text()
    page.mouse.move(400, 300)
    page.keyboard.down("k")
    assert page.locator("#orator-action-wheel").is_hidden()
    page.keyboard.up("k")
    page.keyboard.down("Control")
    page.keyboard.down("Shift")
    page.keyboard.down("K")
    assert page.locator("#orator-action-wheel").is_visible()
    page.mouse.move(460, 260)
    page.keyboard.up("K")
    page.keyboard.up("Shift")
    page.keyboard.up("Control")
    page.wait_for_function("window.sent.length === 1")
    assert page.evaluate("window.sent") == ["new_image"]
    page.click("#action-wheel-rebind")
    page.keyboard.press("Escape")
    assert "Ctrl + Shift + K" in page.locator("#action-wheel-binding-label").inner_text()


def test_rebound_button_in_mouse_chord(wheel_page: Page) -> None:
    page = wheel_page
    page.click("#action-wheel-rebind")
    page.mouse.click(400, 300, button="middle")
    page.mouse.move(400, 300)
    page.mouse.down(button="left")
    page.mouse.down(button="middle")
    assert page.locator("#orator-action-wheel").is_visible()
    page.mouse.move(340, 260)
    page.mouse.up(button="middle")
    page.mouse.up(button="left")
    assert page.evaluate("window.sent") == ["previous_image"]


def test_mouse_modifiers_and_focus_cancellation(wheel_page: Page) -> None:
    page = wheel_page
    page.click("#action-wheel-rebind")
    page.keyboard.down("Control")
    page.mouse.click(400, 300, button="middle")
    page.keyboard.up("Control")
    assert "Ctrl + Middle mouse" in page.locator("#action-wheel-binding-label").inner_text()
    page.mouse.down(button="middle")
    assert page.locator("#orator-action-wheel").is_hidden()
    page.mouse.up(button="middle")
    page.keyboard.down("Control")
    page.mouse.down(button="middle")
    assert page.locator("#orator-action-wheel").is_visible()
    page.mouse.move(340, 260)
    page.evaluate("window.dispatchEvent(new Event('blur'))")
    page.mouse.up(button="middle")
    page.keyboard.up("Control")
    assert page.evaluate("window.sent") == []


def test_action_wheel_initializes_on_full_canvas() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    actions: list[str] = []

    def respond(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/canvas":
            route.fulfill(body=template, content_type="text/html")
        elif path.startswith("/static/"):
            asset = Path(path.lstrip("/"))
            if asset.is_file():
                route.fulfill(path=asset)
            else:
                route.fulfill(status=404)
        elif path == "/api/auth/me":
            route.fulfill(json={"authenticated": True, "user": {"id": 1, "username": "orator"}})
        elif path == "/api/theaters/stage":
            route.fulfill(json={"metadata": {"is_owner": True, "is_active_orator": True}})
        elif path == "/api/theaters/stage/baton":
            route.fulfill(json={"owner": {"id": 1}, "active_orator": {"id": 1}})
        elif path.endswith("/orator-action"):
            actions.append(json.loads(route.request.post_data or "{}")["action"])
            route.fulfill(json={"status": "accepted", "pinned": False, "music_pinned": False})
        elif path.startswith("/api/"):
            route.fulfill(json={})
        else:
            route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        page.add_init_script("localStorage.setItem('narratron_orator_howto_seen', 'true');")
        errors: list[str] = []
        def record_error(error: Error) -> None:
            errors.append(str(error))

        page.on("pageerror", record_error)
        page.route("**/*", respond)
        page.goto("http://wheel.test/canvas?theater_id=stage")
        page.wait_for_function("window._isActiveOratorState === true", timeout=5000)
        # Default must be right click
        assert "Action Wheel: Right mouse" in page.locator("#action-wheel-binding-label").inner_text()
        # Right click over header must NOT open wheel
        page.mouse.move(600, 20)
        page.mouse.down(button="right")
        assert page.locator("#orator-action-wheel").is_hidden()
        page.mouse.up(button="right")
        # Right click over chat sidebar must NOT open wheel
        page.mouse.move(1350, 400)
        page.mouse.down(button="right")
        assert page.locator("#orator-action-wheel").is_hidden()
        page.mouse.up(button="right")
        # Right click over canvas DOES open wheel
        page.mouse.move(600, 400)
        page.mouse.down(button="right")
        assert page.locator("#orator-action-wheel").is_visible(), errors
        page.mouse.move(660, 365)
        page.mouse.up(button="right")
        page.wait_for_function("document.getElementById('action-wheel-status').textContent.includes('applied')")
        assert actions == ["new_image"]
        page.locator("#menu-item-mic-config").evaluate("el => el.click()")
        page.click("#action-wheel-rebind")
        page.keyboard.press("Control+Shift+K")
        assert "Ctrl + Shift + K" in page.locator("#action-wheel-binding-label").inner_text()
        page.click("#mic-config-done-btn")
        # Keyboard combo over chat sidebar must NOT open wheel
        page.mouse.move(1350, 400)
        page.keyboard.down("Control")
        page.keyboard.down("Shift")
        page.keyboard.down("K")
        assert page.locator("#orator-action-wheel").is_hidden()
        page.keyboard.up("K")
        page.keyboard.up("Shift")
        page.keyboard.up("Control")
        # Keyboard combo over canvas DOES open wheel
        page.mouse.move(600, 400)
        page.keyboard.down("Control")
        page.keyboard.down("Shift")
        page.keyboard.down("K")
        assert page.locator("#orator-action-wheel").is_visible()
        page.mouse.move(660, 435)
        page.keyboard.up("K")
        page.keyboard.up("Shift")
        page.keyboard.up("Control")
        page.wait_for_function("document.getElementById('action-wheel-status').textContent === 'New music applied'")
        assert actions == ["new_image", "new_music"]
        assert errors == []
        browser.close()


def test_action_wheel_in_browser() -> None:
    source = Path("static/js/action-wheel.js").read_text(encoding="utf-8").replace("export ", "")
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    wheel_markup = template[template.index('    <style>\n        #orator-action-wheel'):template.index('    <div id="action-wheel-status"')]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.set_content(wheel_markup + '''<button id="action-wheel-rebind"><span id="action-wheel-binding-label"></span></button>
            <div id="action-wheel-status" hidden></div>
            <input id="text" style="position:absolute;left:100px;top:100px;width:200px;height:30px;">''')
        page.add_script_tag(content=source + '''
            window.activeOrator = true;
            window.sent = [];
            window.wheelController = initializeActionWheel({isOrator: () => window.activeOrator,
                sendAction: async (action) => { window.sent.push(action); }});
        ''')
        expected_actions = [
            (0, -70, "up", "toggle_canvas_pin"),
            (60, -35, "upright", "new_image"),
            (60, 35, "downright", "new_music"),
            (0, 70, "down", "toggle_music_pin"),
            (-60, 35, "downleft", "previous_music"),
            (-60, -35, "upleft", "previous_image"),
        ]
        for dx, dy, direction, action in expected_actions:
            page.mouse.move(400, 300)
            page.mouse.down(button="right")
            assert page.locator("#orator-action-wheel").is_visible()
            assert page.locator("#orator-action-wheel").evaluate("el => el.style.left") == "400px"
            page.mouse.move(400 + dx, 300 + dy)
            assert page.locator(f'[data-direction="{direction}"]').evaluate("el => el.classList.contains('selected')")
            page.mouse.up(button="right")
            assert page.locator("#orator-action-wheel").is_hidden()
            page.wait_for_function("expected => window.sent.at(-1) === expected", arg=action)
        assert page.evaluate("window.sent") == [
            "toggle_canvas_pin", "new_image", "new_music", "toggle_music_pin", "previous_music", "previous_image"
        ]
        page.evaluate("window.wheelController.updateState(true, true)")
        assert "Unpin image" in page.locator('[data-direction="up"]').inner_text()
        assert "Unpin music" in page.locator('[data-direction="down"]').inner_text()
        page.mouse.move(400, 300)
        page.mouse.down(button="right")
        page.mouse.move(400, 220)
        page.keyboard.press("Escape")
        assert page.locator("#orator-action-wheel").is_hidden()
        page.mouse.up(button="right")
        # Tiny drags, viewer access, disabled controls, and text input must not send.
        page.mouse.move(400, 300)
        page.mouse.down(button="right")
        page.mouse.move(404, 304)
        page.mouse.up(button="right")
        page.mouse.move(5, 580)
        page.mouse.down(button="right")
        bounds = page.locator("#orator-action-wheel").bounding_box()
        assert bounds is not None
        assert bounds["x"] >= 0 and bounds["y"] >= 0
        assert bounds["x"] + bounds["width"] <= 800
        assert bounds["y"] + bounds["height"] <= 600
        page.mouse.up(button="right")
        page.evaluate("window.activeOrator = false")
        page.mouse.move(400, 300)
        page.mouse.down(button="right")
        page.mouse.move(480, 300)
        page.mouse.up(button="right")
        page.evaluate("window.activeOrator = true")
        page.mouse.move(130, 115)
        page.mouse.down(button="right")
        page.mouse.move(210, 115)
        page.mouse.up(button="right")
        page.click("#action-wheel-rebind")
        page.mouse.click(400, 300, button="middle")
        page.mouse.move(400, 300)
        page.mouse.down(button="middle")
        page.mouse.move(460, 260)
        page.mouse.up(button="middle")
        page.wait_for_function("window.sent.length === 7")
        assert "Pin image" in page.locator('[data-direction="up"]').inner_text()
        page.mouse.move(400, 300)
        page.mouse.down(button="middle")
        page.mouse.move(460, 340)
        page.mouse.up(button="middle")
        page.wait_for_function("window.sent.length === 8")
        assert "Pin music" in page.locator('[data-direction="down"]').inner_text()
        assert page.evaluate("window.sent") == [
            "toggle_canvas_pin", "new_image", "new_music", "toggle_music_pin", "previous_music", "previous_image", "new_image", "new_music"
        ]
        browser.close()


def test_previous_image_pages_orator_and_sync_button_ends_navigational_state() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    actions: list[str] = []
    current_cursor = [1]

    def respond(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/canvas":
            route.fulfill(body=template, content_type="text/html")
        elif path.startswith("/static/"):
            asset = Path(path.lstrip("/"))
            if asset.is_file():
                route.fulfill(path=asset)
            else:
                route.fulfill(status=404)
        elif path == "/api/auth/me":
            route.fulfill(json={"authenticated": True, "user": {"id": 1, "username": "orator"}})
        elif path == "/api/theaters/stage":
            route.fulfill(json={"metadata": {"is_owner": True, "is_active_orator": True}})
        elif path == "/api/theaters/stage/baton":
            route.fulfill(json={"owner": {"id": 1}, "active_orator": {"id": 1}})
        elif path == "/api/latest":
            route.fulfill(json={
                "latest": "/static/images/p1.png",
                "time": 2.0,
                "history": [
                    {"url": "/static/images/p1.png", "prompt": "Image 1", "time": 1.0},
                    {"url": "/static/images/p2.png", "prompt": "Image 2", "time": 2.0},
                ],
                "orator_cursor": current_cursor[0],
            })
        elif path.endswith("/orator-action"):
            action_name = str(json.loads(route.request.post_data or "{}").get("action", ""))
            actions.append(action_name)
            if action_name == "previous_image":
                current_cursor[0] = max(0, current_cursor[0] - 1)
            elif action_name == "next_image":
                current_cursor[0] = min(1, current_cursor[0] + 1)
            route.fulfill(json={"status": "accepted", "pinned": False, "music_pinned": False, "orator_cursor": current_cursor[0]})
        elif path.startswith("/api/"):
            route.fulfill(json={})
        else:
            route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        page.add_init_script("localStorage.setItem('narratron_orator_howto_seen', 'true');")
        errors: list[str] = []

        page.on("pageerror", lambda err: errors.append(str(err)))
        page.route("**/*", respond)
        page.goto("http://wheel.test/canvas?theater_id=stage")
        page.wait_for_function("window._isActiveOratorState === true", timeout=5000)
        page.wait_for_function("document.getElementById('page-indicator').textContent.trim() === '2 / 2'")

        # Sync button replaces Head and is hidden for orator
        sync_button = page.locator("#page-head-btn")
        assert "Sync" in sync_button.inner_text()
        assert sync_button.is_hidden()

        # Trigger previous image on action wheel (upleft: dx=-60, dy=-35)
        page.mouse.move(600, 400)
        page.mouse.down(button="right")
        assert page.locator("#orator-action-wheel").is_visible(), errors
        page.mouse.move(540, 365)
        page.mouse.up(button="right")
        page.wait_for_function("document.getElementById('action-wheel-status').textContent.includes('applied')")
        assert actions == ["previous_image"]

        # The orator should be paged back simultaneously to 1 / 2
        assert page.locator("#page-indicator").inner_text().strip() == "1 / 2"
        # Orator never gets a sync button or navigational state
        assert sync_button.is_hidden()

        # Orator's next button is enabled because orator is at 1 / 2 (not greyed out)
        next_button = page.locator("#page-next-btn")
        assert not next_button.is_disabled()

        # Now test a viewer connecting to the theater
        viewer_page = browser.new_page(viewport={"width": 1500, "height": 900})
        viewer_page.add_init_script("localStorage.setItem('narratron_viewer_collab_guide_seen:stage', 'true'); localStorage.setItem('narratron_orator_howto_seen', 'true');")
        viewer_errors: list[str] = []
        viewer_page.on("pageerror", lambda err: viewer_errors.append(str(err)))

        def respond_viewer(route: Route) -> None:
            path = urlsplit(route.request.url).path
            if path == "/canvas":
                route.fulfill(body=template, content_type="text/html")
            elif path.startswith("/static/"):
                asset = Path(path.lstrip("/"))
                if asset.is_file():
                    route.fulfill(path=asset)
                else:
                    route.fulfill(status=404)
            elif path == "/api/auth/me":
                route.fulfill(json={"authenticated": True, "user": {"id": 2, "username": "viewer"}})
            elif path == "/api/theaters/stage":
                route.fulfill(json={"metadata": {"is_owner": False, "is_active_orator": False}})
            elif path == "/api/theaters/stage/baton":
                route.fulfill(json={"owner": {"id": 1}, "active_orator": {"id": 1}})
            elif path == "/api/latest":
                route.fulfill(json={
                    "latest": "/static/images/p1.png",
                    "time": 2.0,
                    "history": [
                        {"url": "/static/images/p1.png", "prompt": "Image 1", "time": 1.0},
                        {"url": "/static/images/p2.png", "prompt": "Image 2", "time": 2.0},
                    ],
                    "orator_cursor": current_cursor[0],
                })
            elif path.startswith("/api/"):
                route.fulfill(json={})
            else:
                route.fulfill(status=404)

        viewer_page.route("**/*", respond_viewer)
        viewer_page.goto("http://wheel.test/canvas?theater_id=stage")
        viewer_page.wait_for_function("window._isActiveOratorState === false", timeout=5000)
        # Viewer starts synced to orator cursor (1 / 2)
        viewer_page.wait_for_function("document.getElementById('page-indicator').textContent.trim() === '1 / 2'")
        viewer_sync = viewer_page.locator("#page-head-btn")
        assert viewer_sync.is_hidden()

        # Viewer can navigate forward and enter navigational state
        viewer_page.locator("#page-next-btn").click()
        assert viewer_page.locator("#page-indicator").inner_text().strip() == "2 / 2"
        assert viewer_sync.is_visible()

        # Viewer clicks 'Sync' to end navigational state and return to orator cursor (1 / 2)
        viewer_sync.click()
        assert viewer_page.locator("#page-indicator").inner_text().strip() == "1 / 2"
        assert viewer_sync.is_hidden()

        # Orator clicks next button to return to head (2 / 2)
        next_button.click()
        page.wait_for_function("document.getElementById('page-indicator').textContent.trim() === '2 / 2'")
        assert actions == ["previous_image", "next_image"]
        # At head (2 / 2), next button is disabled
        assert next_button.is_disabled()
        assert sync_button.is_hidden()

        assert errors == []
        assert viewer_errors == []
        browser.close()


def test_orator_page_bar_previous_moves_cursor_and_has_no_sync() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    actions: list[str] = []
    current_cursor = [2]

    def respond(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/canvas":
            route.fulfill(body=template, content_type="text/html")
        elif path.startswith("/static/"):
            asset = Path(path.lstrip("/"))
            if asset.is_file():
                route.fulfill(path=asset)
            else:
                route.fulfill(status=404)
        elif path == "/api/auth/me":
            route.fulfill(json={"authenticated": True, "user": {"id": 1, "username": "orator"}})
        elif path == "/api/theaters/stage":
            route.fulfill(json={"metadata": {"is_owner": True, "is_active_orator": True}})
        elif path == "/api/theaters/stage/baton":
            route.fulfill(json={"owner": {"id": 1}, "active_orator": {"id": 1}})
        elif path == "/api/latest":
            route.fulfill(json={
                "latest": "/static/images/p3.png",
                "time": 3.0,
                "history": [
                    {"url": "/static/images/p1.png", "prompt": "Image 1", "time": 1.0},
                    {"url": "/static/images/p2.png", "prompt": "Image 2", "time": 2.0},
                    {"url": "/static/images/p3.png", "prompt": "Image 3", "time": 3.0},
                ],
                "orator_cursor": current_cursor[0],
            })
        elif path.endswith("/orator-action"):
            action_name = str(json.loads(route.request.post_data or "{}").get("action", ""))
            actions.append(action_name)
            if action_name == "previous_image":
                current_cursor[0] = max(0, current_cursor[0] - 1)
            elif action_name == "next_image":
                current_cursor[0] = min(2, current_cursor[0] + 1)
            route.fulfill(json={"status": "accepted", "pinned": False, "music_pinned": False, "orator_cursor": current_cursor[0]})
        elif path.startswith("/api/"):
            route.fulfill(json={})
        else:
            route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        page.add_init_script("localStorage.setItem('narratron_orator_howto_seen', 'true');")
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.route("**/*", respond)
        page.goto("http://wheel.test/canvas?theater_id=stage")
        page.wait_for_function("window._isActiveOratorState === true", timeout=5000)
        page.wait_for_function("document.getElementById('page-indicator').textContent.trim() === '3 / 3'")

        sync_button = page.locator("#page-head-btn")
        assert sync_button.is_hidden()

        # At 3 / 3 (head), next button is disabled
        next_button = page.locator("#page-next-btn")
        assert next_button.is_disabled()

        # Orator clicks Previous Image button in the page bar
        page.locator("#page-prev-btn").click()
        page.wait_for_function("document.getElementById('page-indicator').textContent.trim() === '2 / 3'")
        assert actions == ["previous_image"]
        # Orator still has no sync button and no navigational state
        assert sync_button.is_hidden()

        # At 2 / 3, next button is enabled; clicking it advances back to 3 / 3
        assert not next_button.is_disabled()
        next_button.click()
        page.wait_for_function("document.getElementById('page-indicator').textContent.trim() === '3 / 3'")
        assert actions == ["previous_image", "next_image"]
        assert next_button.is_disabled()
        assert sync_button.is_hidden()
        assert errors == []
        browser.close()
