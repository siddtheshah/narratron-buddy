from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api_server import canvas


@pytest.fixture
def action_services() -> Iterator[tuple[MagicMock, MagicMock]]:
    state = MagicMock()
    state.visual.pinned = False
    state.visual.orator_cursor = 0
    state.audio.pinned = False
    state.audio.orator_cursor = 0
    state.ui.viewer_collab_enabled = False
    session = MagicMock(is_alive=True)
    session.config = {}
    session.observability_tools = None
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


@pytest.mark.parametrize("action,intent", [("update_story", "notepad and character manager"), ("update_ui", "interactive canvas UI")])
@pytest.mark.parametrize("collaboration", [True, False])
def test_secondary_action_sends_intent_and_collaboration_capture(
    action_services: tuple[MagicMock, MagicMock], action: str, intent: str, collaboration: bool
) -> None:
    state, session = action_services
    state.ui.viewer_collab_enabled = collaboration
    session.send_agent_requested_observability.return_value = True
    result = canvas.post_orator_action("stage", canvas.OratorAction(action=action), Request({"type": "http"}))
    assert result["status"] == "accepted"
    if collaboration:
        session.send_agent_requested_observability.assert_called_once_with(force=True)
        assert session.method_calls[0][0] == "send_agent_requested_observability"
    else:
        session.send_agent_requested_observability.assert_not_called()
    session.send_notification.assert_called_once()
    assert intent in session.send_notification.call_args[0][0].parts[0].text
    state.visual.set_pinned.assert_not_called()
    state.audio.set_pinned.assert_not_called()
    session.image_tools.request_orator_bypass.assert_not_called()
    session.music_tools.request_orator_bypass.assert_not_called()


def test_adventure_story_update_is_a_nudge_for_next_action_and_visuals(
    action_services: tuple[MagicMock, MagicMock]
) -> None:
    _, session = action_services
    session.config = {"story_planning": {"adventure_mode": True}}
    canvas.post_orator_action("stage", canvas.OratorAction(action="update_story"), Request({"type": "http"}))
    instruction = session.send_notification.call_args[0][0].parts[0].text
    assert "process_user_action nudge parameter" in instruction
    assert "upcoming visuals" in instruction
    assert "do not invent a player action" in instruction
    assert "update_sticky_note" not in instruction


@pytest.mark.parametrize("action", ["update_story", "update_ui"])
@pytest.mark.parametrize("failure", ["disconnected", "capture", "notification"])
def test_secondary_action_reports_delivery_failure(
    action_services: tuple[MagicMock, MagicMock], action: str, failure: str
) -> None:
    state, session = action_services
    state.ui.viewer_collab_enabled = True
    session.is_alive = failure != "disconnected"
    session.send_agent_requested_observability.return_value = failure != "capture"
    session.send_notification.return_value = failure != "notification"
    with pytest.raises(HTTPException) as error:
        canvas.post_orator_action("stage", canvas.OratorAction(action=action), Request({"type": "http"}))
    assert error.value.status_code == 409
    state.persist.assert_not_called()
    if failure != "notification":
        session.send_notification.assert_not_called()


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



def test_orator_action_endpoint_response_validation_with_integer_cursor(
    action_services: tuple[MagicMock, MagicMock]
) -> None:
    state, _ = action_services
    state.visual.orator_cursor = 7
    state.audio.orator_cursor = 3
    client = TestClient(canvas.app)
    response = client.post("/api/theaters/stage/orator-action", json={"action": "toggle_canvas_pin"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["orator_cursor"] == 7
    assert data["music_orator_cursor"] == 3


def test_new_image_triggers_observability_when_collab_enabled(
    action_services: tuple[MagicMock, MagicMock]
) -> None:
    state, session = action_services
    state.ui.viewer_collab_enabled = True
    session.observability_tools = None

    with patch.object(canvas.theater_manager, "get_theater_config", return_value={}):
        canvas.post_orator_action(
            "stage", canvas.OratorAction(action="new_image"), Request({"type": "http"})
        )

    session.send_agent_requested_observability.assert_called_once_with(force=True)
    session.send_notification.assert_called_once()
    notification_content = session.send_notification.call_args[0][0]
    notification_text = notification_content.parts[0].text
    assert "The observability tool is available" not in notification_text
    assert "create_image with display=True" in notification_text


def test_new_image_does_not_trigger_observability_when_collab_disabled_even_if_tool_enabled_in_config(
    action_services: tuple[MagicMock, MagicMock]
) -> None:
    state, session = action_services
    state.ui.viewer_collab_enabled = False
    session.observability_tools = MagicMock()

    with patch.object(
        canvas.theater_manager,
        "get_theater_config",
        return_value={"observability_tool": {"enabled": True}},
    ):
        canvas.post_orator_action(
            "stage", canvas.OratorAction(action="new_image"), Request({"type": "http"})
        )

    session.send_agent_requested_observability.assert_not_called()
    session.send_notification.assert_called_once()
    notification_content = session.send_notification.call_args[0][0]
    notification_text = notification_content.parts[0].text
    assert "The observability tool is available" not in notification_text
    assert "create_image with display=True" in notification_text


def test_new_image_triggers_observability_even_if_tool_disabled_in_config(
    action_services: tuple[MagicMock, MagicMock]
) -> None:
    state, session = action_services
    state.ui.viewer_collab_enabled = True
    session.observability_tools = None

    with patch.object(
        canvas.theater_manager,
        "get_theater_config",
        return_value={"observability_tool": {"enabled": False}},
    ):
        canvas.post_orator_action(
            "stage", canvas.OratorAction(action="new_image"), Request({"type": "http"})
        )

    session.send_agent_requested_observability.assert_called_once_with(force=True)
    session.send_notification.assert_called_once()
    notification_text = session.send_notification.call_args[0][0].parts[0].text
    assert "The observability tool is available" not in notification_text
    assert "create_image with display=True" in notification_text
