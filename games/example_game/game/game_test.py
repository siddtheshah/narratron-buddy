"""Unit tests for the Tic-Tac-Toe example game package."""
from pathlib import Path
from shutil import copytree
from typing import Literal
from unittest.mock import Mock, patch

import pytest

from components.canvas.canvas_state_manager import CanvasStateManager
from components.theater_manager import TheaterManager
from game_engine.game_runtime import GameAction
from game_engine.game_state import StaleGameAction
from game_engine.go_cel import find_go_worker_executable


def make_tictactoe_canvas(tmp_path: Path) -> CanvasStateManager:
    theater = TheaterManager(tmp_path).theater("tictactoe")
    copytree(Path("games/example_game"), theater.directory(), dirs_exist_ok=True)
    canvas = CanvasStateManager(theater)
    canvas.visual.show_image(str(theater.directory() / "stamps/crown.png"))
    return canvas


def position(
    canvas: CanvasStateManager,
    board: list[int],
    *,
    turn: int = 1,
    winner: int = 0,
    draw: bool = False,
) -> None:
    game = canvas.game
    assert game is not None and game.program is not None
    game.load({
        "definition_hash": game.definition_hash,
        "revision": game.revision,
        "state": {
            **game.program.definition.initial,
            "board": board,
            "turn": turn,
            "winner": winner,
            "draw": draw,
        },
    })


def play_move(
    canvas: CanvasStateManager,
    index: int,
    *,
    narratron: bool = False,
) -> GameAction:
    game = canvas.game
    assert game is not None
    action = GameAction(
        name="narratron_move" if narratron else "move",
        arguments={"index": index},
        revision=game.revision,
        canvas_revision=canvas.canvas_observation().revision,
    )
    game.act(action, actor="narratron" if narratron else "player")
    return action


def test_initial_state(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    assert canvas.game is not None
    state = canvas.game.state
    assert state["board"] == [0] * 9
    assert state["turn"] == 1
    assert state["winner"] == 0
    assert state["draw"] is False
    assert state["receipt"] == "Select an empty square to place X."


@pytest.mark.parametrize(
    "initial_board,winning_index",
    [
        # Horizontal top row: 0, 1 -> 2
        ([1, 1, 0, -1, -1, 0, 0, 0, 0], 2),
        # Horizontal middle row: 3, 4 -> 5
        ([-1, -1, 0, 1, 1, 0, 0, 0, 0], 5),
        # Horizontal bottom row: 6, 7 -> 8
        ([-1, -1, 0, 0, 0, 0, 1, 1, 0], 8),
        # Vertical column: 0, 3 -> 6
        ([1, -1, 0, 1, -1, 0, 0, 0, 0], 6),
        # Diagonal: 0, 4 -> 8
        ([1, -1, 0, -1, 1, 0, 0, 0, 0], 8),
        # Anti-diagonal: 2, 4 -> 6
        ([0, -1, 1, -1, 1, 0, 0, 0, 0], 6),
    ],
)
def test_player_x_win_awards_crown_stamp_and_survives_reload(
    tmp_path: Path, initial_board: list[int], winning_index: int,
) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    position(canvas, initial_board, turn=1)
    before = canvas.canvas_observation()

    play_move(canvas, winning_index)

    assert canvas.game is not None
    assert canvas.game.state["winner"] == 1
    assert canvas.game.state["draw"] is False
    assert "X wins" in canvas.game.state["receipt"]

    after = canvas.canvas_observation()
    assert after.page == before.page and after.revision == before.revision + 1
    assert len(after.stamps) == 1
    stamp = next(iter(after.stamps.values()))
    assert stamp.asset_id == "stamp:crown"
    assert stamp.game_key == "tictactoe-victory-crown"
    assert stamp.managed_by == "game"
    assert stamp.draggable is True
    assert stamp.center.model_dump() == {"x": 8500, "y": 1800}

    # Verify canvas reload retains game state and stamps
    restored = CanvasStateManager(canvas.theater)
    assert restored.game is not None
    assert restored.game.state["winner"] == 1
    assert restored.canvas_observation() == after

    # Verify reset clears board, state, and victory stamp
    restored.game.act(GameAction(name="reset", revision=restored.game.revision))
    assert restored.game.state["board"] == [0] * 9
    assert restored.game.state["winner"] == 0
    assert restored.canvas_observation().stamps == {}


def test_narratron_o_win_does_not_award_crown_stamp(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    # O has 0 and 1, plays 2 to win
    position(canvas, [-1, -1, 0, 1, 1, 0, 0, 0, 0], turn=-1)
    play_move(canvas, 2, narratron=True)

    assert canvas.game is not None
    assert canvas.game.state["winner"] == -1
    assert canvas.game.state["draw"] is False
    assert "O wins" in canvas.game.state["receipt"]
    assert canvas.canvas_observation().stamps == {}


def test_draw_game_detected(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    # Board with 8 moves played, 1 empty spot at index 8:
    # X O X
    # X O O
    # O X . -> index 8 completes board as draw
    board = [
        1, -1, 1,
        1, -1, -1,
        -1, 1, 0,
    ]
    position(canvas, board, turn=1)
    play_move(canvas, 8)

    assert canvas.game is not None
    assert canvas.game.state["winner"] == 0
    assert canvas.game.state["draw"] is True
    assert "Draw" in canvas.game.state["receipt"]
    assert canvas.canvas_observation().stamps == {}


def test_cannot_play_in_occupied_cell(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    play_move(canvas, 4)
    assert canvas.game is not None
    assert canvas.game.state["board"][4] == 1

    # Attempt to play in same cell as O on turn -1
    with pytest.raises(ValueError):
        play_move(canvas, 4, narratron=True)


def test_cannot_play_out_of_turn(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    assert canvas.game is not None
    assert canvas.game.state["turn"] == 1

    # Narratron cannot move when turn is 1
    with pytest.raises(ValueError):
        play_move(canvas, 0, narratron=True)

    play_move(canvas, 0)
    assert canvas.game.state["turn"] == -1

    # Player cannot move when turn is -1
    with pytest.raises(ValueError):
        play_move(canvas, 1)


@pytest.mark.parametrize("backend", ["python", "go"])
def test_narratron_concession(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: Literal["python", "go"],
) -> None:
    if backend == "go" and find_go_worker_executable() is None:
        pytest.skip("Build the CEL-Go worker to verify concessions with Go.")
    monkeypatch.setenv("NARRATRON_CEL_BACKEND", backend)

    canvas = make_tictactoe_canvas(tmp_path)
    play_move(canvas, 4)
    game = canvas.game
    assert game is not None and game.program is not None
    assert "narratron_concede" in game.story_snapshot("narratron")["actions"]

    game.act(GameAction(name="narratron_concede", revision=game.revision), actor="narratron")
    assert game.state["winner"] == 1
    assert game.state["draw"] is False
    assert "O concedes" in game.state["receipt"]

    after = canvas.canvas_observation()
    stamp = next(iter(after.stamps.values()))
    assert stamp.asset_id == "stamp:crown"
    assert stamp.game_key == "tictactoe-victory-crown"


@pytest.mark.parametrize("actor", ["player", "contributor"])
def test_players_cannot_submit_narratron_concede(
    tmp_path: Path, actor: Literal["player", "contributor"],
) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    position(canvas, [1, 0, 0, 0, 0, 0, 0, 0, 0], turn=-1)
    game = canvas.game
    assert game is not None
    before = game.serialize()
    with pytest.raises(ValueError, match="actor cannot"):
        game.act(GameAction(name="narratron_concede", revision=game.revision), actor=actor)
    assert game.serialize() == before
    assert canvas.canvas_observation().stamps == {}


def test_failed_save_rolls_back_victory_and_stamp(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    position(canvas, [1, 1, 0, -1, -1, 0, 0, 0, 0], turn=1)
    before = canvas.canvas_observation()
    canvas.persist()
    callback = Mock()
    assert canvas.game is not None
    canvas.game.on_player_action = callback

    with patch("utils.atomic_file.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(ValueError, match="undone"):
            play_move(canvas, 2)

    assert canvas.game.state["winner"] == 0
    assert canvas.canvas_observation() == before
    callback.assert_not_called()

    # Move now succeeds after disk is restored
    play_move(canvas, 2)
    assert len(canvas.canvas_observation().stamps) == 1


def test_stale_canvas_revision_rejects_action(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    revision = canvas.canvas_observation().revision
    assert canvas.doodles is not None
    canvas.doodles.save_text({
        "type": "text",
        "id": "note",
        "text": "Canvas update",
        "x": 0.1,
        "y": 0.1,
        "size": 32,
    })
    assert canvas.game is not None
    with pytest.raises(StaleGameAction, match="canvas changed"):
        canvas.game.act(GameAction(
            name="move",
            arguments={"index": 4},
            revision=0,
            canvas_revision=revision,
        ))
    assert canvas.game.state["board"] == [0] * 9


def test_model_state_projection(tmp_path: Path) -> None:
    canvas = make_tictactoe_canvas(tmp_path)
    assert canvas.game is not None
    snapshot = canvas.game.story_snapshot("narratron")
    assert snapshot is not None
    model_state = snapshot["state"]
    assert model_state["board"] == [0] * 9
    assert model_state["turn"] == 1
    assert model_state["turn_symbol"] == "X"
    assert model_state["available_cells"] == list(range(9))
