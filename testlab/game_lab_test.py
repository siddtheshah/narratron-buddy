from pathlib import Path
from collections.abc import Iterator
from unittest.mock import create_autospec

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch, fixture

from providers.text_response_provider import TextResponseProvider
from testlab import game_lab
from testlab.game_harness_test import response, scenario


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
    created = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.json"})
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
    created = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.json"})
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
    first = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.json"}).json()["session_id"]
    second = manual_client.post("/api/game-lab/sessions", json={"game_path": "adventures/example_adventure/game/game.json"}).json()["session_id"]
    manual_client.post(f"/api/game-lab/sessions/{first}/action", json={"name": "select", "arguments": {"index": 40}, "revision": 0})
    assert manual_client.get(f"/api/game-lab/sessions/{first}").json()["game"]["revision"] == 1
    assert manual_client.get(f"/api/game-lab/sessions/{second}").json()["game"]["revision"] == 0


def test_renderer_assets_are_shared_and_paths_are_restricted(manual_client: TestClient) -> None:
    js_resp = manual_client.get("/game-lab-assets/js/game-renderer.js")
    assert "createGameRenderer" in js_resp.text
    assert "no-cache" in js_resp.headers.get("cache-control", "")
    css_resp = manual_client.get("/game-lab-assets/css/game-renderer.css")
    assert css_resp.status_code == 200
    assert "no-cache" in css_resp.headers.get("cache-control", "")
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
    assert "adventures/example_adventure/game/game.json" in catalog.json()["games"]
    result = client.post("/api/game-lab/run", json={
        "game_path": "adventures/example_adventure/game/game.json",
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
    result = client.post("/api/game-lab/run", json={"game_path": "adventures/example_adventure/game/game.json", "scenario": {"steps": []}})
    assert result.status_code == 422
