"""Exercise remote baton/credit backend session rules against FastAPI routes."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

import importlib
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
                "/static/generated/remote/remote.js": (
                    "static/generated/remote/remote.js",
                    "text/javascript",
                ),
                "/static/generated/remote/remote-game.js": (
                    "static/generated/remote/remote-game.js",
                    "text/javascript",
                ),
                "/static/generated/game/game-renderer.js": (
                    "static/generated/game/game-renderer.js",
                    "text/javascript",
                ),
                "/static/generated/game/game-runtime.js": (
                    "static/generated/game/game-runtime.js",
                    "text/javascript",
                ),
                "/static/generated/game/game-panel.js": (
                    "static/generated/game/game-panel.js",
                    "text/javascript",
                ),
                "/static/generated/game/game-panel-bridge.js": (
                    "static/generated/game/game-panel-bridge.js",
                    "text/javascript",
                ),
                "/static/generated/audio/pcm-worklet-processor.js": (
                    "static/generated/audio/pcm-worklet-processor.js",
                    "text/javascript",
                ),
                "/static/css/game-renderer.css": ("static/css/game-renderer.css", "text/css"),
                "/static/generated/remote/remote-audio.js": (
                    "static/generated/remote/remote-audio.js",
                    "text/javascript",
                ),
                "/static/generated/shared/story-log-inspector.js": (
                    "static/generated/shared/story-log-inspector.js",
                    "text/javascript",
                ),
                "/static/generated/shared/chat-controller.js": (
                    "static/generated/shared/chat-controller.js",
                    "text/javascript",
                ),
                "/static/generated/shared/chat-emotes.js": (
                    "static/generated/shared/chat-emotes.js",
                    "text/javascript",
                ),
                "/static/generated/shared/device-navigation.js": (
                    "static/generated/shared/device-navigation.js",
                    "text/javascript",
                ),
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
                                    "is_contributor": True, "is_adventure_mode": True})
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
        page.get_by_role("tab", name="Remote", exact=True).click()
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
