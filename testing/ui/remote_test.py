"""Exercise remote controls in a phone viewport with offline API fixtures."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

import importlib
import json
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Page, Route, WebSocketRoute, expect, sync_playwright

import object_registry


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
                "/static/js/story-log-inspector.js": ("static/js/story-log-inspector.js", "text/javascript"),
                "/static/js/chat-controller.js": ("static/js/chat-controller.js", "text/javascript"),
                "/static/js/chat-emotes.js": ("static/js/chat-emotes.js", "text/javascript"),
                "/static/js/device-navigation.js": ("static/js/device-navigation.js", "text/javascript"),
                "/static/css/remote.css": ("static/css/remote.css", "text/css"),
                "/static/css/chat.css": ("static/css/chat.css", "text/css"),
            }
            if path in files:
                filename, content_type = files[path]
                route.fulfill(body=Path(filename).read_text(encoding="utf-8"), content_type=content_type)
            elif path == "/static/narratron-avatar.png":
                route.fulfill(body=Path("static/narratron-avatar.png").read_bytes(), content_type="image/png")
            elif path == "/favicon.png":
                route.fulfill(body=Path("templates/narratron favicon.png").read_bytes(), content_type="image/png")
            elif path.endswith("/scenes") or path.endswith("/playlists") or path in {"/api/chat", "/api/suggestions"}:
                route.fulfill(json=[])
            elif path.endswith("/remote"):
                route.fulfill(json={"agent_running": True, "pinned": False, "music_pinned": False,
                                    "viewer_collab_enabled": False, "is_owner": True, "is_orator": True,
                                    "is_contributor": True})
            elif path.endswith("/baton"):
                route.fulfill(json={
                    "owner": {"id": 1, "username": "owner"},
                    "active_orator": {"id": 1, "username": "owner"},
                    "contributors": [],
                    "active_viewers": [],
                    "baton_request": None,
                })
            elif path.endswith("/auth/me"):
                route.fulfill(json={"authenticated": True, "user": {"id": 1, "username": "owner"}})
            elif path.endswith("/output/story_log.jsonl"):
                route.fulfill(status=404)
            else:
                route.fulfill(json={"status": "accepted"})

        def mock_ws(ws: WebSocketRoute) -> None:
            pass

        page.route_web_socket("**/ws/**", mock_ws)
        page.route("**/*", serve)
        page.goto("http://remote.test/remote?theater_id=stage")
        page.wait_for_function("!document.getElementById('controls').disabled")
        yield page
        browser.close()


def test_story_preview_reads_updates_and_sends_instructions(remote_page: Page, tmp_path: Path) -> None:
    page = remote_page
    entries = [
        {"type": "user_action", "action": "Open the gate"},
        {"type": "story_plan", "output": {
            "narration": "The gate opens. <script>alert('unsafe')</script>",
            "dialogue": [{"speaker": "Guard", "text": "Welcome, traveler."}],
            "die_rolls": [{"notation": "1d20", "total": 17}],
        }},
    ]

    def serve_log(route: Route) -> None:
        route.fulfill(body="\n".join(json.dumps(entry) for entry in entries), content_type="text/plain")

    page.route("**/output/story_log.jsonl", serve_log)
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.get_by_role("tab", name="Story log", exact=True).click()
    log = page.get_by_role("log", name="Story log", exact=True)
    expect(log).to_contain_text("Open the gate")
    expect(log).to_contain_text("Guard: Welcome, traveler.")
    expect(log).to_contain_text("1d20: 17")
    expect(page.locator(".preview-frame")).to_be_visible()
    expect(page.locator("#preview-chat-panel")).to_be_hidden()
    assert page.locator(".preview-frame").bounding_box()["y"] < log.bounding_box()["y"]
    assert log.locator("script").count() == 0
    page.get_by_role("button", name="Keyboard", exact=True).click()
    page.locator("#command").fill("Ask the guard for directions")
    with page.expect_request("**/api/orator/command?*") as instruction:
        page.get_by_role("button", name="Send instruction").click()
    assert instruction.value.post_data_json == {"text": "Ask the guard for directions"}
    expect(page.locator("#command-modal")).to_be_hidden()
    expect(page.get_by_role("button", name="Microphone", exact=True)).to_be_enabled()
    entries.append({"type": "story_plan", "output": {"narration": "The guard points north."}})
    expect(log).to_contain_text("The guard points north.")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "remote-story-mobile.png"))
    page.get_by_role("tab", name="Inputs", exact=True).click()
    expect(page.locator("#controls #mic-btn")).to_be_visible()
    page.get_by_role("tab", name="Watch", exact=True).click()
    expect(log).to_be_visible()


def test_story_preview_empty_errors_and_disabled_input(remote_page: Page) -> None:
    page = remote_page
    page.route("**/output/story_log.jsonl", lambda route: route.fulfill(status=503))
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.get_by_role("tab", name="Story log", exact=True).click()
    log = page.get_by_role("log", name="Story log", exact=True)
    expect(log).to_have_text("The story log could not be loaded.")
    page.route("**/output/story_log.jsonl", lambda route: route.fulfill(status=404))
    expect(log).to_have_text("No story actions have been recorded yet.")
    page.route("**/api/theaters/stage/remote", lambda route: route.fulfill(json={
        "agent_running": False, "is_owner": True, "is_orator": True,
    }))
    expect(page.get_by_role("button", name="Microphone", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Keyboard", exact=True)).to_be_disabled()
    expect(page.locator("#story-input-status")).to_contain_text("Turn the agent on")
    page.get_by_role("tab", name="Chat", exact=True).click()
    expect(log).to_be_hidden()
    expect(page.locator(".preview-frame")).to_be_visible()
    expect(page.locator("#preview-chat-panel")).to_be_visible()


def test_story_preview_remains_readable_without_baton(remote_page: Page) -> None:
    page = remote_page
    page.route("**/output/story_log.jsonl", lambda route: route.fulfill(
        body=json.dumps({"type": "story_plan", "output": {"narration": "The adventure continues."}}),
    ))
    page.route("**/api/theaters/stage/remote", lambda route: route.fulfill(json={
        "agent_running": True, "is_owner": False, "is_orator": False,
    }))
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json={
        "owner": {"id": 2, "username": "other"},
        "active_orator": {"id": 2, "username": "other"},
        "contributors": [], "active_viewers": [], "baton_request": None,
    }))
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.get_by_role("tab", name="Story log", exact=True).click()
    expect(page.get_by_role("log", name="Story log", exact=True)).to_contain_text("The adventure continues.")
    expect(page.locator("#story-input-status")).to_have_text("The baton holder controls story input.")
    expect(page.locator("#mic-btn")).to_be_hidden()
    expect(page.locator("#keyboard-btn")).to_be_hidden()
    expect(page.locator("#story-input-controls")).to_have_attribute("disabled", "")


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


@pytest.mark.parametrize("is_orator", [True, False])
def test_preview_chat_sends_emotes_suggestions_and_private_help(
    remote_page: Page, is_orator: bool, tmp_path: Path,
) -> None:
    page = remote_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    messages: list[dict[str, str]] = [{"author": "bob", "text": "Welcome to the theater!"}]
    sent: list[dict[str, str]] = []
    questions: list[dict[str, str]] = []

    def chat(route: Route) -> None:
        if route.request.method == "POST":
            payload = json.loads(route.request.post_data or "{}")
            sent.append(payload)
            message_type = "suggestion" if payload["text"].startswith("/suggest ") else "chat"
            messages.append({**payload, "type": message_type})
            route.fulfill(json={"type": message_type})
        else:
            route.fulfill(json=messages)

    def help_answer(route: Route) -> None:
        questions.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(json={"type": "user_help", "text": "Use the Watch tab to chat.",
                            "html": "<p>Use the <strong>Watch</strong> tab to chat.</p>"})

    page.route("**/api/chat?theater_id=stage", chat)
    page.route("**/api/user-help?theater_id=stage", help_answer)
    if not is_orator:
        page.route("**/api/theaters/stage/remote", lambda route: route.fulfill(json={
            "agent_running": False, "is_owner": False, "is_orator": False,
        }))
        page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json={
            "owner": {"id": 2, "username": "bob"},
            "active_orator": {"id": 2, "username": "bob"},
            "contributors": [], "active_viewers": [], "baton_request": None,
        }))
    page.goto("http://remote.test/remote?theater_id=stage#preview")
    expect(page.get_by_role("log", name="Theater chat")).to_contain_text("Welcome to the theater!")
    expect(page.locator("#chat-name-display")).to_have_text("owner")
    chat_input = page.get_by_role("textbox", name="Chat message")
    chat_input.fill("Hello ")
    page.get_by_role("button", name="Emotes", exact=True).click()
    page.get_by_role("button", name="Dragon", exact=True).click()
    expect(chat_input).to_have_value("Hello 🐉")
    page.get_by_role("button", name="Send", exact=True).click()
    expect(page.get_by_role("log")).to_contain_text("Hello 🐉")
    assert sent == [{"author": "owner", "text": "Hello 🐉"}]
    expect(chat_input).to_have_value("")
    chat_input.press("Escape")

    chat_input.fill("Explore the castle")
    page.get_by_role("button", name="Send suggestion").click()
    expect(page.locator(".suggestion-message")).to_contain_text("Explore the castle")
    assert sent[-1]["text"] == "/suggest Explore the castle"
    expect(page.locator(".suggestion-upvote-btn")).to_be_disabled()
    chat_input.fill("Follow the trail")
    chat_input.press("Control+Enter")
    expect(page.locator(".suggestion-message")).to_have_count(2)
    assert sent[-1]["text"] == "/suggest Follow the trail"

    chat_input.fill("How do I chat?")
    page.get_by_role("button", name="Find basic help and documentation (free)").click()
    expect(page.locator(".user-help-message strong")).to_have_text("Watch")
    assert questions == [{"question": "How do I chat?"}]
    assert len(sent) == 3
    assert all("How do I chat?" not in message["text"] for message in messages)

    chat_input.fill("First line")
    chat_input.press("Shift+Enter")
    chat_input.press_sequentially("Second line")
    expect(chat_input).to_have_value("First line\nSecond line")
    chat_input.press("Enter")
    expect(page.get_by_role("log")).to_contain_text("Second line")
    assert sent[-1]["text"] == "First line\nSecond line"
    messages.append({"author": "bob", "text": "A new message from canvas"})
    expect(page.get_by_role("log")).to_contain_text("A new message from canvas")
    expect(page.locator(".user-help-message strong")).to_have_text("Watch")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#chat-form").evaluate("""form => {
        const bounds = form.getBoundingClientRect();
        return [...form.querySelectorAll('.chat-composer-actions > button, [data-chat-emotes-toggle]')].every(button => {
            const rect = button.getBoundingClientRect();
            return rect.left >= bounds.left && rect.right <= bounds.right && rect.height >= 44;
        });
    }""")
    if not is_orator:
        expect(page.get_by_role("tab", name="Inputs", exact=True)).to_be_hidden()
        expect(chat_input).to_be_enabled()
    page.screenshot(path=str(tmp_path / "remote-chat.png"), full_page=True)
    assert errors == []


@pytest.mark.parametrize("owner_credits", [8001.0, 0.0])
def test_phone_baton_holder_with_no_credits_starts_using_owner_balance(
    remote_page: Page, owner_credits: float,
) -> None:
    page = remote_page
    app_module = importlib.import_module("api_server.app")
    accounts = {
        1: {"id": 1, "username": "owner", "credits": owner_credits},
        2: {"id": 2, "username": "friend", "credits": 0.0},
    }
    baton_state = {
        "owner": accounts[1], "active_orator": accounts[1], "contributors": [accounts[2]],
        "active_viewers": [], "baton_request": {"target_user_id": 2, "timeout_seconds": 30},
    }
    running = False
    database = MagicMock()
    database.get_deployment.return_value = {"user_id": 1, "active_orator_id": 1}
    database.get_user_by_id.side_effect = accounts.__getitem__
    manager = MagicMock()
    manager.get_or_create_session.return_value = MagicMock(status="active")
    client = TestClient(app_module.app)
    starts: list[int] = []

    def remote_status(route: Route) -> None:
        route.fulfill(json={
            "agent_running": running, "pinned": False, "music_pinned": False,
            "viewer_collab_enabled": False, "is_owner": False,
            "is_orator": baton_state["baton_request"] is None, "is_contributor": True,
        })

    def accept_baton(route: Route) -> None:
        baton_state["active_orator"] = accounts[2]
        baton_state["baton_request"] = None
        database.get_deployment.return_value["active_orator_id"] = 2
        route.fulfill(json=baton_state)

    def start_agent(route: Route) -> None:
        nonlocal running
        response = client.post("/api/theaters/stage/agent/start")
        starts.append(response.status_code)
        running = response.status_code == 200
        route.fulfill(status=response.status_code, json=response.json())

    page.route("**/api/auth/me", lambda route: route.fulfill(json={"authenticated": True, "user": accounts[2]}))
    page.route("**/api/theaters/stage/remote", remote_status)
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json=baton_state))
    page.route("**/api/theaters/stage/baton/accept", accept_baton)
    page.route("**/api/theaters/stage/agent/start", start_agent)

    with (
        patch.object(object_registry, "db", database),
        patch.object(object_registry, "live_agent_manager", manager),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value=accounts[2])),
    ):
        page.reload()
        expect(page.locator("#baton-request-modal")).to_be_visible()
        page.locator("#accept-baton-btn").click()
        expect(page.locator("#controls")).to_be_enabled()
        page.get_by_role("tab", name="Inputs", exact=True).click()
        page.get_by_role("button", name="Turn agent on").click()
        expect(page.locator("#controls")).to_be_enabled()
        if owner_credits > 0:
            expect(page.get_by_role("button", name="Turn agent off")).to_be_visible()
            expect(page.get_by_role("button", name="Microphone")).to_be_enabled()
            manager.get_or_create_session.return_value.summon.assert_called_once_with()
            assert starts == [200]
        else:
            expect(page.locator("#toast-message")).to_contain_text("theater owner's credit balance")
            expect(page.get_by_role("button", name="Microphone")).to_be_disabled()
            manager.get_or_create_session.assert_not_called()
            assert starts == [402]

    database.get_user_by_id.assert_called_once_with(1)


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
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.get_by_text("Current scene", exact=True).wait_for()
    assert page.locator("#preview-image").is_visible()
    page.get_by_role("tab", name="Inputs", exact=True).click()
    page.get_by_role("button", name="Pin scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    assert len(images) == 1
    scene = "second"
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.wait_for_function("document.getElementById('preview-image').getAttribute('src') === '/scenes/second.svg' && !document.getElementById('preview-image').hidden")
    assert len(images) == 2
    assert page.locator("#preview-image").evaluate("image => getComputedStyle(image).objectFit") == "contain"

    def revoked(route: Route) -> None:
        route.fulfill(status=403, json={"detail": "Only the active orator can use this remote."})

    page.route("**/api/theaters/stage/remote", revoked)
    page.get_by_role("tab", name="Inputs", exact=True).click()
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("document.getElementById('controls').disabled && document.getElementById('connection-status').dataset.connected === 'false'")
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.get_by_text("Reconnect to see the canvas.").wait_for()
    assert page.locator("#preview-image").is_hidden()
    assert page.locator("#preview-image").get_attribute("src") is None


def test_preview_empty_state_keeps_controls_available(remote_page: Page) -> None:
    page = remote_page
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.get_by_text("No scene on the canvas yet.").wait_for()
    assert page.locator("#preview-image").is_hidden()
    page.get_by_role("tab", name="Inputs", exact=True).click()
    assert page.get_by_role("button", name="Next scene", exact=True).is_enabled()


@pytest.mark.parametrize("width", [320, 390, 700])
def test_preview_is_a_separate_view_with_back_navigation(remote_page: Page, width: int) -> None:
    page = remote_page
    page.set_viewport_size({"width": width, "height": 568})
    page.get_by_role("button", name="Keyboard").click()
    page.locator("#command").fill("Keep my direction draft")
    page.locator("#command-modal-close").click()
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.wait_for_url("**#preview")
    expect(page.locator("#remote-controls")).to_be_hidden()
    expect(page.locator("#canvas-preview")).to_be_visible()
    assert page.locator("#preview-tab").evaluate("button => button === document.activeElement")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.go_back()
    expect(page.locator("#canvas-preview")).to_be_hidden()
    assert page.locator("#command").input_value() == "Keep my direction draft"
    assert page.locator("#inputs-tab").evaluate("button => button === document.activeElement")
    page.go_forward()
    expect(page.locator("#canvas-preview")).to_be_visible()
    page.reload()
    expect(page.locator("#canvas-preview")).to_be_visible()
    page.get_by_role("tab", name="Inputs", exact=True).click()
    expect(page.locator("#remote-controls")).to_be_visible()
    assert page.url == "http://remote.test/remote?theater_id=stage#inputs"


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
    page.get_by_role('tab', name='Watch', exact=True).click()
    swipe_preview(page, 140, 10)
    expect(page.get_by_role('tab', name='Inputs')).to_have_attribute('aria-selected', 'true')
    swipe_panel(page, 140, 10)
    expect(page.get_by_role('tab', name='Assets')).to_have_attribute('aria-selected', 'true')
    swipe_panel(page, -140, 10)
    expect(page.get_by_role('tab', name='Inputs')).to_have_attribute('aria-selected', 'true')
    assert page.locator('#command').input_value() == 'Keep my draft'
    swipe_panel(page, -140, 10)
    expect(page.get_by_role('tab', name='Watch', exact=True)).to_have_attribute('aria-selected', 'true')
    swipe_panel(page, -140, 10)
    expect(page.get_by_role('tab', name='Party', exact=True)).to_have_attribute('aria-selected', 'true')


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
    page.get_by_role('tab', name='Watch', exact=True).click()
    swipe_preview(page, dx, dy, cancel)
    expect(page.get_by_role('tab', name='Watch', exact=True)).to_have_attribute('aria-selected', 'true')


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
    page.get_by_role("tab", name="Watch", exact=True).click()
    page.get_by_text("Scene could not load. Retrying…").wait_for()
    page.get_by_role("tab", name="Inputs", exact=True).click()
    assert page.get_by_role("button", name="Next scene", exact=True).is_enabled()
    failed = False
    page.get_by_role("button", name="Next scene", exact=True).click()
    page.wait_for_function("!document.getElementById('controls').disabled")
    page.get_by_role("tab", name="Watch", exact=True).click()
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
    assert page.locator("header #theater-key").inner_text() == "secret"
    assert page.locator("header #theater-key").is_visible()


def test_remote_header_branding_and_key(remote_page: Page) -> None:
    page = remote_page
    page.goto("http://remote.test/remote?theater_id=stage&join_key=mykey123")
    brand = page.locator("header a[aria-label='Narratron home']")
    assert "Narratron Remote" in brand.inner_text()
    avatar_el = page.locator("header a img.brand-avatar")
    assert avatar_el.is_visible()
    brand_text_el = page.locator("header a .brand-text")
    assert brand_text_el.inner_text() == "Narratron Remote"
    gradient = brand_text_el.evaluate("el => getComputedStyle(el).backgroundImage")
    assert "linear-gradient" in gradient
    assert page.locator("header .badge").count() == 0
    theater_id_el = page.locator("header #theater-id")
    theater_key_el = page.locator("header #theater-key")
    assert theater_id_el.inner_text() == "stage"
    assert theater_key_el.inner_text() == "mykey123"
    assert theater_key_el.is_visible()
    id_box = theater_id_el.bounding_box()
    key_box = theater_key_el.bounding_box()
    assert id_box is not None and key_box is not None
    assert key_box["y"] >= id_box["y"]
    id_font_size = float(theater_id_el.evaluate("el => parseFloat(getComputedStyle(el).fontSize)"))
    key_font_size = float(theater_key_el.evaluate("el => parseFloat(getComputedStyle(el).fontSize)"))
    assert key_font_size < id_font_size


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
    assert page.locator("#inputs-tab").get_attribute("aria-selected") == "true"
    assert page.get_by_role("tablist", name="Remote screens", exact=True).bounding_box()["width"] <= width
    assert page.locator(".desk-heading").count() == 0
    assert page.locator('[aria-labelledby="agent-title"] #connection-status').count() == 1
    assert page.locator("header #theater-id").inner_text() == "stage"
    for power in page.locator(".power-button:visible").all():
        box = power.bounding_box()
        assert box is not None
        assert box["width"] >= 60
        assert box["height"] >= 52
    collab_power = page.locator("#collaboration")
    expect(collab_power).to_be_visible()
    collab_box = collab_power.bounding_box()
    assert collab_box is not None
    assert collab_box["width"] >= 60
    assert collab_box["height"] >= 52
    expect(page.locator("#controls")).to_be_visible()
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
    page.get_by_role('tab', name='Assets').click()
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
    expect(page.get_by_role('tab', name='Assets')).to_have_attribute('aria-selected', 'true')


def test_asset_carousels_do_not_flicker_during_polling(remote_page: Page) -> None:
    page = remote_page
    page.route('**/api/theaters/stage/scenes', lambda route: route.fulfill(json=[
        {'id': 'forest', 'name': 'Forest', 'description': 'A misty wood', 'url': '/forest.svg'},
    ]))
    page.route('**/forest.svg', lambda route: route.fulfill(
        body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"/>', content_type='image/svg+xml'))
    page.route('**/api/theaters/stage/playlists', lambda route: route.fulfill(json=[
        {'id': 'playlist:rain', 'name': 'Rain', 'tracks': []},
    ]))
    page.get_by_role('tab', name='Assets').click()
    forest_card = page.locator('.asset-card').filter(has=page.get_by_role('heading', name='Forest', exact=True))
    expect(forest_card).to_be_visible()

    # Mark the DOM node to verify it is preserved across polling intervals without being destroyed
    page.evaluate("() => { document.querySelector('.asset-card').setAttribute('data-preserved-marker', 'active'); }")

    # Wait for the 2-second background status and asset poll to cycle
    page.wait_for_timeout(2500)

    # Ensure the same DOM card is retained without flickering or recreating
    expect(page.locator('.asset-card[data-preserved-marker="active"]')).to_be_visible()
    expect(page.locator('#asset-status')).not_to_have_text('Loading assets…')


def test_asset_loading_failure_can_be_retried(remote_page: Page) -> None:
    page = remote_page
    page.route('**/api/theaters/stage/scenes', lambda route: route.fulfill(status=503, json={'detail': 'Try again later.'}))
    page.get_by_role('tab', name='Assets').click()
    expect(page.locator('#asset-status')).to_have_text('Try again later.')
    page.route('**/api/theaters/stage/scenes', lambda route: route.fulfill(json=[]))
    page.get_by_role('button', name='Retry loading assets').click()
    expect(page.locator('#asset-status')).to_have_text('No scenes available in this theater yet.')


def test_tabs_support_keyboard_navigation(remote_page: Page) -> None:
    page = remote_page
    expect(page.get_by_role('tab', name='Inputs')).to_have_attribute('aria-selected', 'true')
    assert page.locator('#open-preview').count() == 0
    page.get_by_role('tab', name='Inputs').focus()
    page.keyboard.press('ArrowLeft')
    expect(page.get_by_role('tab', name='Assets')).to_be_focused()
    page.keyboard.press('End')
    expect(page.get_by_role('tab', name='Party', exact=True)).to_be_focused()


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
    page.get_by_role("tab", name="Assets").click()

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


@pytest.mark.parametrize("story_preview", [False, True])
def test_microphone_button_streams_audio_and_stops_on_release(remote_page: Page, story_preview: bool) -> None:
    page = remote_page
    if story_preview:
        page.get_by_role("tab", name="Watch", exact=True).click()
        page.get_by_role("tab", name="Story log", exact=True).click()
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


def test_party_tab_renders_baton_and_contributors(remote_page: Page) -> None:
    page = remote_page
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json={
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 1, "username": "alice"},
        "contributors": [{"id": 2, "username": "bob"}],
        "active_viewers": [{"id": 3, "username": "charlie"}],
        "baton_request": None,
    }))
    page.get_by_role("tab", name="Party", exact=True).click()
    expect(page.locator("#remote-baton")).to_be_visible()
    expect(page.locator("#baton-holder-display")).to_have_text("👑 Orator: @alice")
    expect(page.locator("#menu-take-back-baton-btn")).to_be_hidden()
    expect(page.locator("#active-viewers-list")).to_contain_text("@charlie")
    expect(page.locator("#contributors-list")).to_contain_text("@bob")
    expect(page.get_by_role("button", name="+ Contributor")).to_be_visible()
    expect(page.get_by_role("button", name="Pass Baton 🎙️")).to_be_visible()


def test_party_tab_take_back_baton_visible_when_owner_not_orator(remote_page: Page) -> None:
    page = remote_page
    page.route("**/api/auth/me", lambda route: route.fulfill(json={
        "authenticated": True, "user": {"id": 1, "username": "alice"}
    }))
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json={
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 2, "username": "bob"},
        "contributors": [{"id": 2, "username": "bob"}],
        "active_viewers": [],
        "baton_request": None,
    }))
    page.get_by_role("tab", name="Party", exact=True).click()
    take_back_btn = page.locator("#menu-take-back-baton-btn")
    expect(take_back_btn).to_be_visible()
    expect(take_back_btn).to_have_text("Take back baton")

    with page.expect_request("**/api/theaters/stage/baton/takeback") as req:
        take_back_btn.click()
    assert req.value.method == "POST"


def test_party_tab_add_contributor_and_pass_baton(remote_page: Page) -> None:
    page = remote_page
    page.route("**/api/auth/me", lambda route: route.fulfill(json={
        "authenticated": True, "user": {"id": 1, "username": "alice"}
    }))
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json={
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 1, "username": "alice"},
        "contributors": [{"id": 2, "username": "bob"}],
        "active_viewers": [{"id": 3, "username": "charlie"}],
        "baton_request": None,
    }))
    page.get_by_role("tab", name="Party", exact=True).click()

    with page.expect_request("**/api/theaters/stage/baton/contributors") as add_req:
        page.get_by_role("button", name="+ Contributor").click()
    assert add_req.value.method == "POST"
    assert add_req.value.post_data_json == {"target_user_id": 3}

    with page.expect_request("**/api/theaters/stage/baton/request") as pass_req:
        page.get_by_role("button", name="Pass Baton 🎙️").click()
    assert pass_req.value.method == "POST"
    assert pass_req.value.post_data_json == {"target_user_id": 2, "timeout_seconds": 30}


def test_baton_pass_modal_appears_and_can_be_accepted_on_phone(remote_page: Page) -> None:
    page = remote_page
    page.route("**/api/auth/me", lambda route: route.fulfill(json={
        "authenticated": True, "user": {"id": 2, "username": "bob"}
    }))
    state = {
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 1, "username": "alice"},
        "contributors": [{"id": 2, "username": "bob"}],
        "active_viewers": [],
        "baton_request": {
            "target_user_id": 2,
            "target_username": "bob",
            "timeout_seconds": 30,
        },
    }

    def baton_route(route: Route) -> None:
        route.fulfill(json=state)

    def accept_route(route: Route) -> None:
        state["active_orator"] = {"id": 2, "username": "bob"}
        state["baton_request"] = None
        route.fulfill(json=state)

    page.route("**/api/theaters/stage/baton", baton_route)
    page.route("**/api/theaters/stage/baton/accept", accept_route)
    page.reload()

    modal = page.locator("#baton-request-modal")
    expect(modal).to_be_visible()
    expect(page.locator("#baton-request-modal-title")).to_have_text("Baton Pass Request!")
    expect(page.locator("#baton-timer-bar")).to_be_visible()

    with page.expect_request("**/api/theaters/stage/baton/accept") as accept_req:
        page.locator("#accept-baton-btn").click()
    assert accept_req.value.method == "POST"
    expect(modal).to_be_hidden()


def test_baton_pass_modal_can_be_declined_on_phone(remote_page: Page) -> None:
    page = remote_page
    page.route("**/api/auth/me", lambda route: route.fulfill(json={
        "authenticated": True, "user": {"id": 2, "username": "bob"}
    }))
    state = {
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 1, "username": "alice"},
        "contributors": [{"id": 2, "username": "bob"}],
        "active_viewers": [],
        "baton_request": {
            "target_user_id": 2,
            "target_username": "bob",
            "timeout_seconds": 30,
        },
    }

    def baton_route(route: Route) -> None:
        route.fulfill(json=state)

    def decline_route(route: Route) -> None:
        state["baton_request"] = None
        route.fulfill(json=state)

    page.route("**/api/theaters/stage/baton", baton_route)
    page.route("**/api/theaters/stage/baton/decline", decline_route)
    page.reload()

    modal = page.locator("#baton-request-modal")
    expect(modal).to_be_visible()

    with page.expect_request("**/api/theaters/stage/baton/decline") as decline_req:
        page.locator("#decline-baton-btn").click()
    assert decline_req.value.method == "POST"
    expect(modal).to_be_hidden()


def test_pure_viewer_only_sees_preview_tab(remote_page: Page) -> None:
    page = remote_page
    page.route("**/api/auth/me", lambda route: route.fulfill(json={
        "authenticated": True, "user": {"id": 5, "username": "viewer5"}
    }))
    page.route("**/api/theaters/stage/remote", lambda route: route.fulfill(json={
        "theater_id": "stage",
        "agent_running": True,
        "pinned": False,
        "music_pinned": False,
        "viewer_collab_enabled": False,
        "preview_url": "/theaters/stage/output/scene.webp",
        "is_contributor": False,
    }))
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json={
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 1, "username": "alice"},
        "contributors": [{"id": 2, "username": "bob"}],
        "active_viewers": [{"id": 5, "username": "viewer5"}],
        "baton_request": None,
    }))
    page.reload()

    # The preview panel should be visible
    expect(page.locator("#canvas-preview")).to_be_visible()
    expect(page.locator("#preview-tab")).to_be_visible()
    expect(page.locator("#preview-tab")).to_have_attribute("aria-selected", "true")

    # All other tabs and panels must be hidden
    expect(page.locator("#assets-tab")).to_be_hidden()
    expect(page.locator("#inputs-tab")).to_be_hidden()
    expect(page.locator("#party-tab")).to_be_hidden()
    expect(page.locator("#asset-manager")).to_be_hidden()
    expect(page.locator("#remote-controls")).to_be_hidden()
    expect(page.locator("#remote-baton")).to_be_hidden()

    # Hash navigation attempt to #inputs must not escape preview
    page.evaluate("location.hash = 'inputs'")
    expect(page.locator("#canvas-preview")).to_be_visible()
    expect(page.locator("#inputs-tab")).to_be_hidden()
    expect(page.locator("#remote-controls")).to_be_hidden()


def test_contributor_without_baton_only_sees_preview_tab_and_with_baton_sees_controls(remote_page: Page) -> None:
    page = remote_page
    status_state = {
        "theater_id": "stage",
        "agent_running": True,
        "pinned": False,
        "music_pinned": False,
        "viewer_collab_enabled": False,
        "preview_url": "/theaters/stage/output/scene.webp",
        "is_contributor": False,
        "is_owner": False,
        "is_orator": False,
    }
    baton_state = {
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 1, "username": "alice"},
        "contributors": [],
        "active_viewers": [{"id": 5, "username": "viewer5"}],
        "baton_request": None,
    }

    page.route("**/api/auth/me", lambda route: route.fulfill(json={
        "authenticated": True, "user": {"id": 5, "username": "viewer5"}
    }))
    page.route("**/api/theaters/stage/remote", lambda route: route.fulfill(json=status_state))
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json=baton_state))
    page.reload()

    # Initially pure viewer only sees preview
    expect(page.locator("#preview-tab")).to_be_visible()
    expect(page.locator("#assets-tab")).to_be_hidden()
    expect(page.locator("#inputs-tab")).to_be_hidden()
    expect(page.locator("#party-tab")).to_be_hidden()

    # Grant contributor permission, but alice is still the orator
    status_state["is_contributor"] = True
    baton_state["contributors"] = [{"id": 5, "username": "viewer5"}]
    page.reload()

    # Contributor who is not orator still ONLY sees preview tab
    expect(page.locator("#preview-tab")).to_be_visible()
    expect(page.locator("#assets-tab")).to_be_hidden()
    expect(page.locator("#inputs-tab")).to_be_hidden()
    expect(page.locator("#party-tab")).to_be_hidden()

    # Pass baton to contributor (viewer5 is now active orator)
    status_state["is_orator"] = True
    baton_state["active_orator"] = {"id": 5, "username": "viewer5"}
    page.reload()

    # Now contributor holds the baton: assets, inputs, preview are visible
    expect(page.locator("#assets-tab")).to_be_visible()
    expect(page.locator("#inputs-tab")).to_be_visible()
    expect(page.locator("#preview-tab")).to_be_visible()
    # But Party tab remains hidden for non-owner contributor
    expect(page.locator("#party-tab")).to_be_hidden()


def test_owner_always_sees_party_tab(remote_page: Page) -> None:
    page = remote_page
    status_state = {
        "theater_id": "stage",
        "agent_running": True,
        "pinned": False,
        "music_pinned": False,
        "viewer_collab_enabled": False,
        "preview_url": "/theaters/stage/output/scene.webp",
        "is_contributor": True,
        "is_owner": True,
        "is_orator": False,  # owner passed baton to someone else
    }
    baton_state = {
        "owner": {"id": 1, "username": "alice"},
        "active_orator": {"id": 2, "username": "bob"},
        "contributors": [{"id": 2, "username": "bob"}],
        "active_viewers": [],
        "baton_request": None,
    }
    page.route("**/api/auth/me", lambda route: route.fulfill(json={
        "authenticated": True, "user": {"id": 1, "username": "alice"}
    }))
    page.route("**/api/theaters/stage/remote", lambda route: route.fulfill(json=status_state))
    page.route("**/api/theaters/stage/baton", lambda route: route.fulfill(json=baton_state))
    page.reload()

    # Owner when idle sees Party and Watch tabs, but not Assets/Inputs
    expect(page.locator("#party-tab")).to_be_visible()
    expect(page.locator("#party-tab")).to_have_text("Party")
    expect(page.locator("#preview-tab")).to_be_visible()
    expect(page.locator("#assets-tab")).to_be_hidden()
    expect(page.locator("#inputs-tab")).to_be_hidden()
    expect(page.locator("#menu-take-back-baton-btn")).to_be_visible()


def test_collab_button_in_inputs_controls(remote_page: Page) -> None:
    page = remote_page
    expect(page.locator("#remote-controls #collaboration")).to_be_visible()
    with page.expect_request("**/collab") as req:
        page.locator("#remote-controls #collaboration").click()
    assert req.value.method == "POST"


def test_party_tab_has_copy_theater_key_button(remote_page: Page) -> None:
    page = remote_page
    page.goto("http://remote.test/remote?theater_id=stage&join_key=baton_secret_key")
    page.locator("#party-tab").click()
    expect(page.locator("#remote-baton")).to_be_visible()
    key_display = page.locator("#baton-theater-key")
    expect(key_display).to_have_text("baton_secret_key")
    copy_btn = page.locator("#copy-theater-key-btn")
    expect(copy_btn).to_be_visible()
    copy_btn.click()
    expect(copy_btn).to_contain_text("Copied!")
    expect(page.locator("#toast-message")).to_have_text("Theater key copied to clipboard!")





