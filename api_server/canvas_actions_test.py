from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from api_server import canvas


@pytest.fixture
def action_services() -> Iterator[tuple[MagicMock, MagicMock]]:
    state = MagicMock()
    state.visual.pinned = False
    state.visual.orator_cursor = 0
    state.audio.pinned = False
    session = MagicMock(is_alive=True)
    session.send_user_content.return_value = True
    session.send_notification.return_value = True
    manager = MagicMock()
    manager.get_session.return_value = session
    with patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "db"), \
            patch.object(canvas, "get_current_user"), \
            patch.object(canvas, "can_control_agent_websocket", return_value=True), \
            patch.object(canvas, "_state", return_value=state), \
            patch.object(canvas, "live_agent_manager", manager):
        yield state, session


@pytest.mark.parametrize("action", ["new_image", "new_music"])
def test_force_action_requests_scoped_bypass(
    action_services: tuple[MagicMock, MagicMock], action: str
) -> None:
    state, session = action_services
    canvas.post_orator_action("stage", canvas.OratorAction(action=action), Request({"type": "http"}))
    suite = session.image_tools if action == "new_image" else session.music_tools
    suite.request_orator_bypass.assert_called_once_with(
        {"create_image"} if action == "new_image" else {"create_music", "play_music"}
    )
    session.send_notification.assert_called_once()
    assert session.send_notification.call_args[0][0].role == "system"
    state.persist.assert_called_once()
    if action == "new_image":
        state.visual.set_pinned.assert_called_once_with(False)
        state.visual.request_immediate_image.assert_called_once()
    else:
        state.audio.set_pinned.assert_called_once_with(False)


@pytest.mark.parametrize("action", ["new_image", "new_music"])
def test_request_new_media_unpins_first_when_pinned(
    action_services: tuple[MagicMock, MagicMock], action: str
) -> None:
    state, session = action_services
    state.visual.pinned = True
    state.audio.pinned = True
    result = canvas.post_orator_action("stage", canvas.OratorAction(action=action), Request({"type": "http"}))
    if action == "new_image":
        state.visual.set_pinned.assert_called_once_with(False)
    else:
        state.audio.set_pinned.assert_called_once_with(False)
    session.send_content.assert_called_once()
    call_content = session.send_content.call_args[0][0]
    assert call_content.role == "system"
    assert "was unpinned" in call_content.parts[0].text
    assert result["status"] == "accepted"


@pytest.mark.parametrize("action", ["toggle_canvas_pin", "toggle_music_pin"])
def test_pin_action_toggles_without_live_agent(
    action_services: tuple[MagicMock, MagicMock], action: str
) -> None:
    state, session = action_services
    session.is_alive = False
    canvas.post_orator_action("stage", canvas.OratorAction(action=action), Request({"type": "http"}))
    target = state.visual if action == "toggle_canvas_pin" else state.audio
    target.set_pinned.assert_called_once_with(True)
    session.send_notification.assert_not_called()


def test_actions_reject_viewers(action_services: tuple[MagicMock, MagicMock]) -> None:
    state, session = action_services
    with patch.object(canvas, "can_control_agent_websocket", return_value=False), pytest.raises(HTTPException) as error:
        canvas.post_orator_action("stage", canvas.OratorAction(action="new_image"), Request({"type": "http"}))
    assert error.value.status_code == 403
    session.send_notification.assert_not_called()
    state.persist.assert_not_called()


def test_rejected_generation_cancels_bypass(action_services: tuple[MagicMock, MagicMock]) -> None:
    state, session = action_services
    session.send_notification.return_value = False
    with pytest.raises(HTTPException) as error:
        canvas.post_orator_action("stage", canvas.OratorAction(action="new_image"), Request({"type": "http"}))
    assert error.value.status_code == 409
    session.image_tools.cancel_orator_bypass.assert_called_once()
    state.persist.assert_not_called()


def test_invalid_action_action_is_rejected() -> None:
    with pytest.raises(ValidationError):
        canvas.OratorAction(action="diagonal")


def test_previous_image_action_success_and_failure(action_services: tuple[MagicMock, MagicMock]) -> None:
    state, session = action_services
    state.visual.previous_image.return_value = True
    response = canvas.post_orator_action("stage", canvas.OratorAction(action="previous_image"), Request({"type": "http"}))
    assert response["status"] == "accepted"
    state.visual.previous_image.assert_called_once()
    session.send_content.assert_called_once()
    assert session.send_content.call_args[0][0].role == "system"
    state.persist.assert_called_once()

    state.visual.previous_image.return_value = False
    with pytest.raises(HTTPException) as error:
        canvas.post_orator_action("stage", canvas.OratorAction(action="previous_image"), Request({"type": "http"}))
    assert error.value.status_code == 400


def test_next_image_action_success_and_failure(action_services: tuple[MagicMock, MagicMock]) -> None:
    state, session = action_services
    state.visual.next_image.return_value = True
    response = canvas.post_orator_action("stage", canvas.OratorAction(action="next_image"), Request({"type": "http"}))
    assert response["status"] == "accepted"
    state.visual.next_image.assert_called_once()
    session.send_content.assert_called_once()
    assert session.send_content.call_args[0][0].role == "system"
    state.persist.assert_called_once()

    state.visual.next_image.return_value = False
    with pytest.raises(HTTPException) as error:
        canvas.post_orator_action("stage", canvas.OratorAction(action="next_image"), Request({"type": "http"}))
    assert error.value.status_code == 400


def test_previous_music_action_success_and_failure(action_services: tuple[MagicMock, MagicMock]) -> None:
    state, session = action_services
    state.audio.previous_music.return_value = True
    response = canvas.post_orator_action("stage", canvas.OratorAction(action="previous_music"), Request({"type": "http"}))
    assert response["status"] == "accepted"
    state.audio.previous_music.assert_called_once()
    session.send_content.assert_called_once()
    assert session.send_content.call_args[0][0].role == "system"
    state.persist.assert_called_once()

    state.audio.previous_music.return_value = False
    with pytest.raises(HTTPException) as error:
        canvas.post_orator_action("stage", canvas.OratorAction(action="previous_music"), Request({"type": "http"}))
    assert error.value.status_code == 400
