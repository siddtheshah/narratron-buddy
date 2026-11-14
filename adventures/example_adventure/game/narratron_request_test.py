"""Player requests wake game control without mutating adventure game state."""

from copy import deepcopy
import json
from pathlib import Path
from shutil import copytree
from typing import Literal
from unittest.mock import Mock

import pytest

from game_engine.game_runtime import GameAction
from game_engine.game_state import GameState
from testing.game_fixtures import make_example_canvas


ADVENTURES = sorted(Path("adventures").glob("*/game/game.yaml"))


@pytest.mark.parametrize("source", ADVENTURES, ids=[path.parents[1].name for path in ADVENTURES])
@pytest.mark.parametrize("actor", ["player", "contributor"])
def test_request_wakes_game_control_without_gameplay_changes(
    tmp_path: Path, source: Path, actor: Literal["player", "contributor"],
) -> None:
    if source.parents[1].name == "example_adventure":
        canvas = make_example_canvas(tmp_path)
        game = canvas.game
        assert game is not None
        before_canvas = canvas.canvas_observation()
    else:
        copytree(source.parent, tmp_path / "game")
        game = GameState(tmp_path, Mock(), Mock())
    assert game.error == ""
    assert game.program is not None
    assert (source.parents[1] / "lore/GAME.md").is_file()
    before = deepcopy(game.state)
    callback = Mock()
    game.on_player_action = callback
    action = GameAction(name="request_narratron_action", revision=game.revision)

    # The rendered UI exposes the named request, with the same contributor permission.
    assert "request_narratron_action" in json.dumps(game.payload())
    if game.program.definition.panel is not None:
        assert 'data-panel-action="request_narratron_action"' in source.with_name("panel.html").read_text(encoding="utf-8")
    game.act(action, actor=actor)

    assert game.state == before
    assert game.revision == action.revision + 1
    callback.assert_called_once()
    committed_action, snapshot = callback.call_args.args
    assert committed_action == action
    assert snapshot["revision"] == game.revision
    assert snapshot["state"] == game.story_snapshot("narratron")["state"]
    if source.parents[1].name == "example_adventure":
        assert canvas.canvas_observation() == before_canvas
