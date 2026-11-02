"""The example's checkers victory writes a real, durable canvas stamp."""
from pathlib import Path
from typing import Literal
from unittest.mock import Mock, patch

import pytest

from components.canvas.canvas_state_manager import CanvasStateManager
from game_engine.game_runtime import GameAction
from game_engine.game_state import StaleGameAction
from game_engine.go_cel import find_go_worker_executable
from testing.game_fixtures import make_example_canvas


def position(canvas: CanvasStateManager, pieces: dict[int, int], *, turn: int = 1, quiet: int = 0) -> None:
    game = canvas.game
    assert game is not None and game.program is not None
    game.load({
        "definition_hash": game.definition_hash, "revision": game.revision,
        "state": {**game.program.definition.initial, "board": [pieces.get(i, 0) for i in range(64)], "turn": turn, "quiet": quiet},
    })


def move(canvas: CanvasStateManager, source: int, target: int, *, narratron: bool = False) -> GameAction:
    game = canvas.game
    assert game is not None
    action = GameAction(
        name="narratron_move" if narratron else "move", arguments={"source": source, "target": target},
        revision=game.revision, canvas_revision=canvas.canvas_observation().revision,
    )
    game.act(action, actor="narratron" if narratron else "player")
    return action


@pytest.mark.parametrize("pieces,source,target", [({42: 1, 35: -1}, 42, 28), ({17: 1, 62: -1}, 17, 8)])
def test_red_win_stamps_the_current_page_and_survives_reload(tmp_path: Path, pieces: dict[int, int], source: int, target: int) -> None:
    canvas = make_example_canvas(tmp_path)
    position(canvas, pieces)
    before = canvas.canvas_observation()
    move(canvas, source, target)
    assert canvas.game.state["winner"] == 1
    after = canvas.canvas_observation()
    assert after.page == before.page and after.revision == before.revision + 1
    stamp, = after.stamps.values()
    assert (stamp.asset_id, stamp.game_key, stamp.managed_by, stamp.draggable) == ("stamp:crown", "checkers-victory-crown", "game", False)
    assert stamp.center.model_dump() == {"x": 8500, "y": 1800}
    assert canvas.doodles.resolve_stamp_image(canvas.doodles.stamp_annotations()[0]) is not None
    restored = CanvasStateManager(canvas.theater)
    assert restored.game.state["winner"] == 1
    assert restored.canvas_observation() == after
    restored.game.act(GameAction(name="reset", revision=restored.game.revision))
    assert restored.canvas_observation().stamps == {}
    assert restored.game.state["winner"] == 0


@pytest.mark.parametrize("pieces,source,target,quiet", [
    ({42: 1, 1: -1}, 42, 33, 0),
    ({17: 1, 10: -1, 12: -1}, 17, 3, 0),
    ({42: 2, 1: -2}, 42, 33, 79),
])
def test_nonwinning_moves_do_not_stamp(tmp_path: Path, pieces: dict[int, int], source: int, target: int, quiet: int) -> None:
    canvas = make_example_canvas(tmp_path)
    position(canvas, pieces, quiet=quiet)
    move(canvas, source, target)
    assert canvas.game.state["winner"] == 0
    assert canvas.canvas_observation().stamps == {}


def test_ivory_win_does_not_award_the_player_a_crown(tmp_path: Path) -> None:
    canvas = make_example_canvas(tmp_path)
    position(canvas, {26: -1, 35: 1}, turn=-1)
    move(canvas, 26, 44, narratron=True)
    assert canvas.game.state["winner"] == -1
    assert canvas.canvas_observation().stamps == {}


def test_failed_save_rolls_back_both_victory_and_stamp(tmp_path: Path) -> None:
    canvas = make_example_canvas(tmp_path)
    position(canvas, {42: 1, 35: -1})
    before = canvas.canvas_observation()
    canvas.persist()
    callback = Mock()
    canvas.game.on_player_action = callback
    with patch("utils.atomic_file.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(ValueError, match="undone"):
            move(canvas, 42, 28)
    assert canvas.game.state["winner"] == 0 and canvas.game.revision == 0
    assert canvas.canvas_observation() == before
    callback.assert_not_called()
    restored = CanvasStateManager(canvas.theater)
    assert restored.game.state["winner"] == 0 and restored.canvas_observation().stamps == {}
    move(canvas, 42, 28)
    assert len(canvas.canvas_observation().stamps) == 1


def test_stale_page_rejects_victory_without_changing_game(tmp_path: Path) -> None:
    canvas = make_example_canvas(tmp_path)
    position(canvas, {42: 1, 35: -1})
    revision = canvas.canvas_observation().revision
    canvas.doodles.save_text({"type": "text", "id": "note", "text": "A new note", "x": 0.1, "y": 0.1, "size": 32})
    with pytest.raises(StaleGameAction, match="canvas changed"):
        canvas.game.act(GameAction(name="move", arguments={"source": 42, "target": 28}, revision=0, canvas_revision=revision))
    assert canvas.game.state["winner"] == 0 and canvas.game.revision == 0
    assert canvas.canvas_observation().stamps == {}


def test_rejected_duplicate_does_not_duplicate_crown(tmp_path: Path) -> None:
    canvas = make_example_canvas(tmp_path)
    position(canvas, {42: 1, 35: -1})
    action = move(canvas, 42, 28)
    with pytest.raises(StaleGameAction):
        canvas.game.act(action)
    assert len(canvas.canvas_observation().stamps) == 1


@pytest.mark.parametrize("backend", ["python", "go"])
def test_narratron_can_concede_and_award_the_victory_crown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: Literal["python", "go"],
) -> None:
    if backend == "go" and find_go_worker_executable() is None:
        pytest.skip("Build the CEL-Go worker to verify concessions with Go.")
    monkeypatch.setenv("NARRATRON_CEL_BACKEND", backend)
    canvas = make_example_canvas(tmp_path)
    move(canvas, 40, 33)
    game = canvas.game
    assert game is not None
    assert "narratron_concede" in game.story_snapshot("narratron")["actions"]
    board = list(game.state["board"])
    before = canvas.canvas_observation()
    game.act(GameAction(name="narratron_concede", revision=game.revision), actor="narratron")
    assert game.state["winner"] == 1 and game.state["draw"] is False
    assert game.state["board"] == board
    assert game.state["forced"] == game.state["selected"] == -1
    assert game.state["receipt"] == "Ivory concedes · Red wins"
    assert game.revision == 2
    after = canvas.canvas_observation()
    assert after.page == before.page and after.revision == before.revision + 1
    stamp, = after.stamps.values()
    assert (stamp.asset_id, stamp.game_key) == ("stamp:crown", "checkers-victory-crown")
    assert game.story_snapshot("narratron")["actions"] == {}
    restored = CanvasStateManager(canvas.theater)
    assert restored.game.state == game.state and restored.canvas_observation() == after
    restored.game.act(GameAction(name="reset", revision=restored.game.revision))
    assert restored.game.state["winner"] == 0 and restored.canvas_observation().stamps == {}


@pytest.mark.parametrize("actor", ["player", "contributor"])
def test_players_cannot_submit_narratrons_concession(tmp_path: Path, actor: Literal["player", "contributor"]) -> None:
    canvas = make_example_canvas(tmp_path)
    position(canvas, {42: 1, 1: -1}, turn=-1)
    game = canvas.game
    assert game is not None
    before = game.serialize()
    with pytest.raises(ValueError, match="actor cannot"):
        game.act(GameAction(name="narratron_concede", revision=game.revision), actor=actor)
    assert game.serialize() == before and canvas.canvas_observation().stamps == {}


@pytest.mark.parametrize("turn,winner,draw", [(1, 0, False), (-1, 1, False), (-1, -1, False), (-1, 0, True)])
def test_concession_is_unavailable_outside_an_active_ivory_turn(tmp_path: Path, turn: int, winner: int, draw: bool) -> None:
    canvas = make_example_canvas(tmp_path)
    game = canvas.game
    assert game is not None and game.program is not None
    game.load({
        "definition_hash": game.definition_hash, "revision": 0,
        "state": {**game.program.definition.initial, "turn": turn, "winner": winner, "draw": draw},
    })
    assert "narratron_concede" not in game.story_snapshot("narratron")["actions"]
    before = game.serialize()
    with pytest.raises(ValueError):
        game.act(GameAction(name="narratron_concede", revision=0), actor="narratron")
    assert game.serialize() == before and canvas.canvas_observation().stamps == {}
