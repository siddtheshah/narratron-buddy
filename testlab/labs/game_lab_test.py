from pathlib import Path
from collections.abc import Iterator
from unittest.mock import create_autospec

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from playwright.sync_api import Dialog, Route, expect, sync_playwright
from pytest import MonkeyPatch, fixture

from providers.text_response_provider import TextResponseProvider
from testlab.labs import game_lab
from testlab.labs.game_harness_test import response, scenario
from testing.game_fixtures import panel_definition


def test_manual_session_copies_html_panel_and_serves_bridge(tmp_path: Path) -> None:
    path = tmp_path / "game.json"
    path.write_text(panel_definition().model_dump_json(), encoding="utf-8")
    (tmp_path / "panel.html").write_text('<p data-panel-text="count"></p>', encoding="utf-8")
    provider = create_autospec(TextResponseProvider, instance=True)
    session = game_lab.GameSession(path, provider, game_lab.GameSessionRequest(game_path=str(path)))
    try:
        payload = session.game.payload()
        assert payload is not None
        assert payload["panel_html"] == '<p data-panel-text="count"></p>'
        assert Path(game_lab.game_renderer_asset("js", "game-panel.js").path).is_file()
        assert Path(game_lab.game_renderer_asset("js", "game-panel-bridge.js").path).is_file()
    finally:
        session.directory.cleanup()


@fixture
def manual_client(monkeypatch: MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(game_lab, "_sessions", {})
    app = FastAPI()
    app.include_router(game_lab.router)
    with TestClient(app) as client:
        yield client
    for session in game_lab._sessions.values():
        session.directory.cleanup()


def test_manual_session_keeps_state_runs_ai_and_cleans_up(
    manual_client: TestClient, monkeypatch: MonkeyPatch,
) -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.return_value = response("narratron_move", {"source": 17, "target": 24})

    def get_provider(provider_id: str) -> TextResponseProvider:
        return provider

    monkeypatch.setattr(game_lab, "get_text_response_provider", get_provider)
    created = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.yaml"})
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    url = f"/api/game-lab/sessions/{session_id}"
    directory = Path(game_lab._sessions[session_id].directory.name)
    selected = manual_client.post(url + "/action", json={"name": "select", "arguments": {"index": 40}, "revision": 0})
    assert selected.json()["revision"] == 1
    provider.generate.assert_not_called()
    stale = manual_client.post(url + "/action", json={"name": "move", "arguments": {"source": 40, "target": 33}, "revision": 0})
    assert stale.status_code == 409
    moved = manual_client.post(url + "/action", json={"name": "move", "arguments": {"source": 40, "target": 33}, "revision": 1})
    assert moved.status_code == 200
    assert moved.json()["revision"] == 3
    snapshot = manual_client.get(url).json()
    assert snapshot["state"]["state"]["turn"] == 1
    assert len(snapshot["turns"]) == 2
    assert len(snapshot["turns"][1]["ai_actions"]) == 1
    assert snapshot["turns"][1]["errors"] == []
    reset = manual_client.post(url + "/action", json={"name": "reset", "arguments": {}, "revision": 3})
    assert reset.json()["revision"] == 4
    assert manual_client.get(url).json()["state"]["state"]["receipt"] == "Select a piece to begin."
    provider.generate.assert_called_once()
    assert manual_client.delete(url).status_code == 200
    assert not directory.exists()
    assert manual_client.get(url).status_code == 404


def test_manual_ai_failure_retains_player_move_and_reports_error(
    manual_client: TestClient, monkeypatch: MonkeyPatch,
) -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.side_effect = RuntimeError("AI unavailable")

    def get_provider(provider_id: str) -> TextResponseProvider:
        return provider

    monkeypatch.setattr(game_lab, "get_text_response_provider", get_provider)
    created = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.yaml"})
    url = f"/api/game-lab/sessions/{created.json()['session_id']}"
    moved = manual_client.post(url + "/action", json={"name": "move", "arguments": {"source": 40, "target": 33}, "revision": 0})
    assert moved.json()["revision"] == 1
    assert moved.json()["error"] == "AI unavailable"
    state = manual_client.get(url).json()
    assert state["turns"][0]["after"]["board"][33] == 1
    assert state["turns"][0]["errors"] == ["AI unavailable"]
    rejected = manual_client.post(url + "/action", json={"name": "narratron_move", "arguments": {"source": 17, "target": 24}, "revision": 1})
    assert rejected.status_code == 400
    assert manual_client.get(url).json()["game"]["revision"] == 1


def test_manual_games_do_not_share_state(manual_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    provider = create_autospec(TextResponseProvider, instance=True)

    def get_provider(provider_id: str) -> TextResponseProvider:
        return provider

    monkeypatch.setattr(game_lab, "get_text_response_provider", get_provider)
    first = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.yaml"}).json()["session_id"]
    second = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.yaml"}).json()["session_id"]
    manual_client.post(f"/api/game-lab/sessions/{first}/action", json={"name": "select", "arguments": {"index": 40}, "revision": 0})
    assert manual_client.get(f"/api/game-lab/sessions/{first}").json()["game"]["revision"] == 1
    assert manual_client.get(f"/api/game-lab/sessions/{second}").json()["game"]["revision"] == 0


def test_renderer_assets_are_shared_and_paths_are_restricted(manual_client: TestClient) -> None:
    js_resp = manual_client.get("/game-lab-assets/js/game-renderer.js")
    assert js_resp.status_code == 200
    assert js_resp.headers["content-type"].split(";")[0] in {"text/javascript", "application/javascript"}
    assert "no-cache" in js_resp.headers.get("cache-control", "")
    css_resp = manual_client.get("/game-lab-assets/css/game-renderer.css")
    assert css_resp.status_code == 200
    assert "no-cache" in css_resp.headers.get("cache-control", "")
    runtime_resp = manual_client.get("/game-lab-assets/js/game-runtime.js")
    assert runtime_resp.status_code == 200
    assert runtime_resp.headers["content-type"].split(";")[0] in {"text/javascript", "application/javascript"}
    assert manual_client.get("/game-lab-assets/js/other.js").status_code == 404


def test_testlab_server_exposes_game_lab() -> None:
    from testlab.server import app

    client = TestClient(app)
    assert 'href="/game-lab"' in client.get("/").text
    assert client.get("/game-lab").status_code == 200


def test_page_and_run_endpoint_use_game_harness(monkeypatch: MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(game_lab.router)
    client = TestClient(app)
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.return_value = response("narratron_move", {"source": 17, "target": 24})
    def get_provider(provider_id: str) -> TextResponseProvider:
        return provider

    monkeypatch.setattr(game_lab, "get_text_response_provider", get_provider)
    assert "Game Testing Lab" in client.get("/game-lab").text
    catalog = client.get("/api/game-lab/catalog")
    assert "adventures/example_adventure/game/game.yaml" in catalog.json()["games"]
    result = client.post("/api/game-lab/run", json={
        "game_path": "adventures/example_adventure/game/game.yaml",
        "scenario": scenario().model_dump(mode="json"),
    })
    assert result.status_code == 200
    assert result.json()["passed"]


def test_endpoint_rejects_outside_workspace_and_invalid_scenario(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(game_lab.router)
    client = TestClient(app)
    outside = tmp_path / "game.json"
    outside.write_text("{}", encoding="utf-8")
    result = client.post("/api/game-lab/run", json={"game_path": str(outside), "scenario": scenario().model_dump(mode="json")})
    assert result.status_code == 400
    result = client.post("/api/game-lab/run", json={"game_path": "adventures/example_adventure/game/game.yaml", "scenario": {"steps": []}})
    assert result.status_code == 422


@pytest.mark.usefixtures("offline_browser_contexts")
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
    app.mount(
        "/static/generated",
        StaticFiles(directory=game_lab.PROJECT_ROOT / "frontend" / "static" / "generated"),
        name="static-generated",
    )
    app.mount(
        "/testlab-static",
        StaticFiles(directory=game_lab.TESTLAB_ROOT / "frontend" / "static"),
        name="testlab-static",
    )
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
