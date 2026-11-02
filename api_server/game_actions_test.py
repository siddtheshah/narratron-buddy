from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from api_server import canvas, theaters
from game_engine.game_state import GameAction, GameState
from testing.game_fixtures import make_example_canvas, make_preview_checkers_game


@pytest.fixture
def game_route(tmp_path: Path) -> Iterator[GameState]:
    game = make_preview_checkers_game(tmp_path)
    ui = Mock(viewer_collab_enabled=False)
    state = Mock(game=game, ui=ui)
    with patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "db") as mock_db, \
            patch.object(canvas, "get_current_user"), \
            patch.object(canvas, "can_control_agent_websocket", return_value=True), \
            patch.object(canvas, "is_contributor", return_value=False), \
            patch.object(canvas, "_state", return_value=state):
        mock_db.get_deployment.return_value = {"id": "checkers"}
        yield game


def test_game_action_without_agent_and_stale_conflict(game_route: GameState) -> None:
    request = Request({"type": "http"})
    action = GameAction(name="move", arguments={"source": 40, "target": 33}, revision=0)
    result = canvas.post_game_action(action, request, "checkers")
    assert result is not None
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


def test_contributor_can_take_annotated_actions_when_collab_enabled(tmp_path: Path) -> None:
    game = make_preview_checkers_game(tmp_path)
    callback = Mock()
    game.on_player_action = callback
    ui = Mock(viewer_collab_enabled=True)
    state = Mock(game=game, ui=ui)
    request = Request({"type": "http"})

    with patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "db") as mock_db, \
            patch.object(canvas, "get_current_user"), \
            patch.object(canvas, "can_control_agent_websocket", return_value=False), \
            patch.object(canvas, "is_contributor", return_value=True), \
            patch.object(canvas, "_state", return_value=state):
        mock_db.get_deployment.return_value = {"id": "checkers"}

        # Contributor can select and move (both annotated with permission: "contributor")
        select_res = canvas.post_game_action(
            GameAction(name="select", arguments={"index": 40}, revision=0),
            request,
            "checkers",
        )
        assert select_res is not None
        assert select_res["revision"] == 1
        callback.assert_not_called()

        move_res = canvas.post_game_action(
            GameAction(name="move", arguments={"source": 40, "target": 33}, revision=1),
            request,
            "checkers",
        )
        assert move_res is not None
        assert move_res["revision"] == 2
        callback.assert_called_once()

        # Contributor cannot invoke orator-only 'reset'
        with pytest.raises(HTTPException) as exc_info:
            canvas.post_game_action(GameAction(name="reset", revision=2), request, "checkers")
        assert exc_info.value.status_code == 403
        assert game.revision == 2

        # When collaboration mode is disabled, contributor is blocked even on contributor actions
        ui.viewer_collab_enabled = False
        with pytest.raises(HTTPException) as exc_info_disabled:
            canvas.post_game_action(
                GameAction(name="select", arguments={"index": 17}, revision=2),
                request,
                "checkers",
            )
        assert exc_info_disabled.value.status_code == 403


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
    payload = game_route.payload()
    assert payload is not None
    assert 'unexpected error' in str(payload['error'])
    res = canvas.post_game_action(action, request, "checkers")
    assert res is not None
    assert res["revision"] == 1


def test_canvas_game_requires_fresh_canvas_revision_and_commits_victory_stamp(tmp_path: Path) -> None:
    state = make_example_canvas(tmp_path)
    game = state.game
    assert game is not None and game.program is not None
    game.load({"definition_hash": game.definition_hash, "revision": 0, "state": {
        **game.program.definition.initial, "board": [1 if i == 42 else -1 if i == 35 else 0 for i in range(64)],
    }})
    action = GameAction(name="move", arguments={"source": 42, "target": 28}, revision=0)
    request = Request({"type": "http"})
    with patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "db"), \
            patch.object(canvas, "get_current_user"), \
            patch.object(canvas, "can_control_agent_websocket", return_value=True), \
            patch.object(canvas, "_state", return_value=state):
        with pytest.raises(HTTPException) as error:
            canvas.post_game_action(action, request, "checkers")
        assert error.value.status_code == 400
        action.canvas_revision = state.canvas_observation().revision
        state.doodles.save_text({"type": "text", "id": "note", "text": "Updated page", "x": 0.1, "y": 0.1, "size": 32})
        with pytest.raises(HTTPException) as error:
            canvas.post_game_action(action, request, "checkers")
        assert error.value.status_code == 409
        assert game.revision == 0 and state.canvas_observation().stamps == {}
        action.canvas_revision = state.canvas_observation().revision
        result = canvas.post_game_action(action, request, "checkers")
        assert result is not None and result["revision"] == 1
        assert game.state["winner"] == 1
        assert len(state.canvas_observation().stamps) == 1
    stamp, = state.doodles.stamp_annotations()
    with patch.object(theaters, "_require_canvas_access_async", AsyncMock()), \
            patch.object(theaters, "theater_manager", state.theater.manager):
        response = TestClient(canvas.app).get(str(stamp["url"]))
    assert response.status_code == 200
    assert response.content == (state.theater.directory() / "stamps" / "crown.png").read_bytes()
