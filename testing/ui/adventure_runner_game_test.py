"""Play the adventure game panel through the lab's in-process routes."""
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.sync_api import Dialog, Route, expect, sync_playwright
from pytest import MonkeyPatch
import pytest

from testing.game_fixtures import counter_definition
from testlab import adventure_runner, server
from components.story.story_handoffs import GameControlDecision
from components.story.story_session import StoryRole, StorySession


@pytest.mark.usefixtures("offline_lab_text_providers")
def test_adventure_lab_game_moves_and_reset(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    adventures = tmp_path / "adventures"
    adventure = adventures / "counter"
    (adventure / "game").mkdir(parents=True)
    (adventure / "game/game.json").write_text(counter_definition().model_dump_json(), encoding="utf-8")
    (adventure / "theater.yaml").write_text(
        "story_planning:\n  adventure_mode: true\n  deep_planning:\n    enabled: false\n", encoding="utf-8",
    )
    (adventure / "planning.yaml").write_text(
        "Current Scene:\n  owner: recorder\n  initial: Counter room\n", encoding="utf-8",
    )
    monkeypatch.setattr(adventure_runner, "ADVENTURES_DIR", adventures)
    monkeypatch.setattr(adventure_runner, "THEATERS_DIR", tmp_path / "theaters")
    monkeypatch.setattr(server, "_adventure_runner_sessions", {})

    def offline_story_role(
        session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "",
    ) -> str:
        """A minor game move: record it, skip narration, and plan nothing."""
        if role == "recorder":
            return GameControlDecision().model_dump_json()
        if role == "planner":
            return "No planning changes."
        raise AssertionError("A routine counter move must not invoke the responder.")

    monkeypatch.setattr(StorySession, "run", offline_story_role)
    client = TestClient(server.app)

    def route_request(route: Route) -> None:
        path = route.request.url.split("http://adventure.test", 1)[-1]
        response = client.request(route.request.method, path, content=route.request.post_data,
                                  headers={"Content-Type": "application/json"})
        route.fulfill(status=response.status_code, content_type=response.headers.get("content-type", "application/json"), body=response.content)

    def accept_dialog(dialog: Dialog) -> None:
        dialog.accept()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.route("http://adventure.test/**", route_request)
            page.goto("http://adventure.test/adventure-runner")
            expect(page.locator("#adv-select")).to_have_value("counter")
            page.get_by_role("button", name="Start Session", exact=True).click()
            page.get_by_role("button", name="Game", exact=True).click()
            expect(page.locator('[data-game-id="counter"]')).to_have_text("Count: 0")
            page.get_by_role("button", name="Add one", exact=True).click()
            expect(page.locator('[data-game-id="counter"]')).to_have_text("Count: 1")
            expect(page.locator("#chat-stream")).to_contain_text("Game action: add")
            page.once("dialog", accept_dialog)
            page.get_by_role("button", name="Reset", exact=True).click()
            expect(page.locator('[data-game-id="counter"]')).to_have_text("Count: 0")
            expect(page.get_by_role("button", name="Add one", exact=True)).to_be_enabled()
            browser.close()
    finally:
        for session in server._adventure_runner_sessions.values():
            session.cleanup()
        client.close()
