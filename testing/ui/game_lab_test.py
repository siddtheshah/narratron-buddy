"""Play the actual lab page against in-process APIs with an offline AI provider."""
from pathlib import Path
from unittest.mock import create_autospec

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import Dialog, Route, expect, sync_playwright
from pytest import MonkeyPatch

from providers.text_response_provider import TextResponseProvider
from testlab import game_lab
from testlab.game_harness_test import response


def test_manual_lab_plays_checkers_and_resets(
    monkeypatch: MonkeyPatch, tmp_path: Path,
) -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.return_value = response("narratron_move", {"source": 17, "target": 24})

    def get_provider(provider_id: str) -> TextResponseProvider:
        return provider

    monkeypatch.setattr(game_lab, "get_text_response_provider", get_provider)
    monkeypatch.setattr(game_lab, "_sessions", {})
    app = FastAPI()
    app.include_router(game_lab.router)
    client = TestClient(app)

    def route_request(route: Route) -> None:
        path = route.request.url.split("http://game-lab.test", 1)[-1]
        result = client.request(route.request.method, path, content=route.request.post_data, headers={"Content-Type": "application/json"})
        route.fulfill(status=result.status_code, content_type=result.headers.get("content-type", "application/json"), body=result.content)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 1000})
            page.route("http://game-lab.test/**", route_request)
            page.goto("http://game-lab.test/game-lab")
            page.get_by_role("button", name="Load fresh game").click()
            frame = page.frame_locator("iframe.game-html-panel")
            expect(frame.locator(".board button")).to_have_count(32)
            expect(page.locator("#game-preview")).to_be_visible()
            expect(frame.locator("#square-1")).to_be_disabled()
            frame.locator('#square-40').click()
            expect(frame.locator("#square-40 .selection")).to_be_visible()
            expect(frame.locator('#square-33')).to_be_enabled()
            provider.generate.assert_not_called()
            frame.locator('#square-33').click()
            expect(frame.locator('#status')).to_have_text("Red to move")
            expect(page.get_by_role('button', name='Submit', exact=True)).to_have_count(0)
            expect(page.locator("#turn-summary")).to_contain_text("1 AI actions")
            expect(page.locator("#replies")).to_contain_text("Narratron: I respond to the move.")
            expect(frame.locator("#square-24 .piece.ivory")).to_be_visible()
            provider.generate.assert_called_once()
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(tmp_path / "game-lab-mobile.png"), full_page=True)
            bounds = page.locator(".game-panel").bounding_box()
            assert bounds is not None and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
            page.once("dialog", accept_dialog)
            frame.get_by_role("button", name="New game", exact=True).click()
            expect(frame.locator("#square-40 .piece.red")).to_be_visible()
            expect(frame.locator("#square-24 .piece.ivory")).to_be_hidden()
            page.get_by_role("button", name="Close session").click()
            expect(page.locator("#placeholder")).to_be_visible()
            assert not game_lab._sessions
            browser.close()
    finally:
        for session in game_lab._sessions.values():
            session.directory.cleanup()
        client.close()


def accept_dialog(dialog: Dialog) -> None:
    dialog.accept()
