"""Play the adventure game panel through the lab's in-process routes."""
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
import pytest

from testing.game_fixtures import counter_definition
from testlab import adventure_runner, server
from components.story.story_handoffs import GameControlDecision
from components.story.story_session import StoryRole, StorySession
from providers.text_response_provider import TextResponseRequest, TextResponseResult


def test_adventure_lab_game_moves_and_reset(
    monkeypatch: MonkeyPatch, tmp_path: Path, offline_lab_text_providers: pytest.MonkeyPatch,
) -> None:
    adventures = tmp_path / "adventures"
    adventure = adventures / "counter"
    (adventure / "game").mkdir(parents=True)
    (adventure / "game/game.json").write_text(counter_definition().model_dump_json(), encoding="utf-8")
    (adventure / "theater.yaml").write_text(
        "story_planning:\n  adventure_mode: true\n", encoding="utf-8",
    )
    (adventure / "planning.yaml").write_text(
        "Current Scene:\n  owner: recorder\n  initial: Counter room\n", encoding="utf-8",
    )
    monkeypatch.setattr(adventure_runner, "ADVENTURES_DIR", adventures)
    monkeypatch.setattr(adventure_runner, "THEATERS_DIR", tmp_path / "theaters")
    monkeypatch.setattr(server, "_adventure_runner_sessions", {})

    def generate(request: TextResponseRequest) -> TextResponseResult:
        schema_props = (
            request.response_json_schema.get("properties")
            if request.response_json_schema is not None
            else None
        )
        if request.response_schema is GameControlDecision or (
            type(schema_props) is dict and "game_actions" in schema_props
        ):
            return TextResponseResult(text=GameControlDecision().model_dump_json(), provider="test", model="test")
        return TextResponseResult(
            text="", provider="test", model="test",
            parsed={
                "primary_lore_document": "none",
                "secondary_lore_document": "none",
                "dice_roll": "none",
                "should_take_game_action": False,
            },
        )

    offline_lab_text_providers.generate.side_effect = generate

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

    try:
        list_resp = client.get("/api/adventure-runner/adventures")
        assert list_resp.status_code == 200
        adventures_payload = list_resp.json()
        assert [adv["id"] for adv in adventures_payload["adventures"]] == ["counter"]

        create_resp = client.post(
            "/api/adventure-runner/sessions",
            json={"adventure_id": "counter"},
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        session_id = created["id"]
        assert created["state"]["game"]["revision"] == 0
        assert created["state"]["game"]["view"][0]["children"][0]["props"]["text"] == "Count: 0"

        action_resp = client.post(
            f"/api/adventure-runner/sessions/{session_id}/game/action",
            json={"name": "add", "arguments": {"amount": 1}, "revision": 0},
        )
        assert action_resp.status_code == 200
        action_payload = action_resp.json()
        assert action_payload["revision"] == 1
        assert action_payload["view"][0]["children"][0]["props"]["text"] == "Count: 1"

        session_resp = client.get(f"/api/adventure-runner/sessions/{session_id}")
        assert session_resp.status_code == 200
        session_payload = session_resp.json()
        assert session_payload["state"]["game"]["view"][0]["children"][0]["props"]["text"] == "Count: 1"
        assert "Game action: add" in session_payload["history"][0]["user_message"]

        reset_resp = client.post(f"/api/adventure-runner/sessions/{session_id}/reset")
        assert reset_resp.status_code == 200
        reset_payload = reset_resp.json()
        assert reset_payload["state"]["game"]["revision"] == 0
        assert reset_payload["state"]["game"]["view"][0]["children"][0]["props"]["text"] == "Count: 0"
        assert reset_payload["history"] == []
    finally:
        for session in server._adventure_runner_sessions.values():
            session.cleanup()
        client.close()
