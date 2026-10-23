"""Live game tools preserve the package's rules and model-facing contract."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from components.canvas.game_runtime import GameAction
from testing.game_fixtures import exchange_definition, make_game
from tools.game_tool import GameTool
from tools.tool_bundle import ToolBundle


def test_reads_projected_state_instructions_and_available_actions(tmp_path: Path) -> None:
    game = make_game(tmp_path, exchange_definition())
    tool = GameTool(game)
    assert json.loads(tool.view_game_instructions())["instructions"] == exchange_definition().instructions
    snapshot = json.loads(tool.view_game_state())
    assert snapshot["revision"] == 0
    assert snapshot["state"]["stock"] == ["green"]
    assert "narratron_inventory" not in snapshot["state"]
    actions = json.loads(tool.get_available_actions())["actions"]
    assert set(actions) == {"narratron_place"}
    assert actions["narratron_place"]["parameters"]["required"] == ["item"]


def test_action_commits_updates_actions_and_does_not_trigger_player_callback(tmp_path: Path) -> None:
    game = make_game(tmp_path, exchange_definition())
    callback = Mock()
    game.on_player_action = callback
    result = json.loads(GameTool(game).take_game_action("narratron_place", '{"item":"green"}', 0))
    assert result["revision"] == 1
    assert game.state["narratron_offer"] == ["green"]
    assert set(result["actions"]) == {"narratron_finalize"}
    callback.assert_not_called()
    assert "Trades: 0" in json.dumps(game.payload())


@pytest.mark.parametrize(("name", "arguments", "revision"), [
    ("narratron_place", '{"item":"green"}', 3),
    ("player_place", '{"item":"red"}', 0),
    ("missing", '{}', 0),
    ("narratron_place", '{"item":"red"}', 0),
    ("narratron_place", '{}', 0),
    ("narratron_place", '[]', 0),
    ("narratron_place", 'invalid json', 0),
])
def test_invalid_actions_leave_state_unchanged(
    tmp_path: Path, name: str, arguments: str, revision: int,
) -> None:
    game = make_game(tmp_path, exchange_definition())
    before = game.story_snapshot("narratron")
    assert GameTool(game).take_game_action(name, arguments, revision).startswith("Error:")
    assert game.story_snapshot("narratron") == before


def test_stale_action_can_be_reconsidered_after_state_read(tmp_path: Path) -> None:
    game = make_game(tmp_path, exchange_definition())
    tool = GameTool(game)
    game.act(GameAction(name="player_place", arguments={"item": "red"}, revision=0))
    assert "game changed" in tool.take_game_action("narratron_place", '{"item":"green"}', 0)
    revision = json.loads(tool.view_game_state())["revision"]
    assert json.loads(tool.take_game_action("narratron_place", '{"item":"green"}', revision))["revision"] == 2


def test_game_tool_declarations_support_live_providers(tmp_path: Path) -> None:
    tool = GameTool(make_game(tmp_path, exchange_definition()))
    bundle = ToolBundle([
        tool.view_game_instructions, tool.view_game_state,
        tool.get_available_actions, tool.take_game_action,
    ])
    declarations = bundle.get_declarations()
    assert {entry.name for entry in declarations} == {
        "view_game_instructions", "view_game_state", "get_available_actions", "take_game_action",
    }
    action = next(entry for entry in declarations if entry.name == "take_game_action")
    assert set(action.parameters_json_schema["required"]) == {"name", "arguments_json", "revision"}
