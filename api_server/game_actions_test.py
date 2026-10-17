from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException, Request

from api_server import canvas
from components.canvas.game_state import GameAction, GameState


@pytest.fixture
def game_route() -> Iterator[GameState]:
    game = GameState(Path("adventures/example_adventure"), Mock(), Mock())
    state = Mock(game=game)
    with patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "db"), \
            patch.object(canvas, "get_current_user"), \
            patch.object(canvas, "can_control_agent_websocket", return_value=True), \
            patch.object(canvas, "_state", return_value=state):
        yield game


def test_game_action_without_agent_and_stale_conflict(game_route: GameState) -> None:
    request = Request({"type": "http"})
    action = GameAction(name="move", arguments={"source": 40, "target": 33}, revision=0)
    result = canvas.post_game_action(action, request, "checkers")
    assert result["revision"] == 1
    with pytest.raises(HTTPException) as error:
        canvas.post_game_action(action, request, "checkers")
    assert error.value.status_code == 409


def test_game_action_rejects_viewers_and_illegal_moves(game_route: GameState) -> None:
    request = Request({"type": "http"})
    action = GameAction(name="move", arguments={"source": 40, "target": 42}, revision=0)
    with pytest.raises(HTTPException) as error:
        canvas.post_game_action(action, request, "checkers")
    assert error.value.status_code == 400
    with patch.object(canvas, "can_control_agent_websocket", return_value=False), pytest.raises(HTTPException) as error:
        canvas.post_game_action(GameAction(name="reset", revision=0), request, "checkers")
    assert error.value.status_code == 403
    assert game_route.revision == 0


def test_game_action_checks_theater_access(game_route: GameState) -> None:
    with patch.object(canvas, "_require_canvas_access", side_effect=HTTPException(403)), pytest.raises(HTTPException):
        canvas.post_game_action(GameAction(name="reset", revision=0), Request({"type": "http"}), "private")
    assert game_route.revision == 0


def test_unexpected_game_failure_is_a_controlled_http_error(game_route: GameState) -> None:
    action = GameAction(name="reset", revision=0)
    request = Request({"type": "http"})
    with patch.object(game_route, "act", side_effect=RuntimeError("internal details")), pytest.raises(HTTPException) as error:
        canvas.post_game_action(action, request, "checkers")
    assert error.value.status_code == 500
    assert "internal details" not in error.value.detail
    assert 'unexpected error' in game_route.payload()['error']
    assert canvas.post_game_action(action, request, "checkers")["revision"] == 1
