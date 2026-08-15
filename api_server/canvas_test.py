"""Unit coverage for canvas routes with object-registry service mocks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import object_registry
from api_server import canvas


def request():
    return SimpleNamespace()


def test_post_chat_uses_registry_canvas_service_for_regular_message():
    service = MagicMock()
    with patch.object(object_registry, "canvas_states", service), patch.object(canvas, "_require_canvas_access"):
        result = canvas.post_chat(canvas.ChatMessage(author="Ada", text="hello"), request(), "stage")
    assert result == {"status": "ok", "type": "chat"}
    service.add_chat_message.assert_called_once_with("hello", author="Ada", theater_id="stage")


def test_post_chat_uses_verified_identity_for_profile_link():
    service = MagicMock()
    with patch.object(object_registry, "canvas_states", service), patch.object(canvas, "get_current_user", return_value={"id": 3, "username": "Ada", "profile_color": "#f97316"}):
        result = canvas.post_chat(canvas.ChatMessage(author="Imposter", text="hello"), request())
    assert result == {"status": "ok", "type": "chat"}
    service.add_chat_message.assert_called_once_with(
        "hello", author="Ada", theater_id=None, profile_username="Ada", profile_color="#f97316"
    )


def test_suggestion_requires_text_and_does_not_call_registry():
    service = MagicMock()
    with patch.object(object_registry, "canvas_states", service), pytest.raises(HTTPException) as error:
        canvas.post_chat(canvas.ChatMessage(author="Ada", text="/suggest"), request())
    assert error.value.status_code == 400
    service.add_suggestion.assert_not_called()


def test_suggestion_converts_service_validation_error_to_bad_request():
    service = MagicMock()
    service.add_suggestion.side_effect = ValueError("already suggested")
    with patch.object(object_registry, "canvas_states", service), pytest.raises(HTTPException) as error:
        canvas.post_chat(canvas.ChatMessage(author="Ada", text="/suggest rain"), request())
    assert error.value.status_code == 400
    assert error.value.detail == "already suggested"


def test_upvote_reports_missing_suggestion():
    service = MagicMock()
    service.upvote_suggestion.return_value = False
    with patch.object(object_registry, "canvas_states", service), pytest.raises(HTTPException) as error:
        canvas.upvote_suggestion(canvas.SuggestionVote(voter="Ada", target_author="Lin"), request(), "stage")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_toggle_microphone_requires_owner_then_calls_registry_service():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3}
    service = MagicMock()
    service.toggle_microphone = AsyncMock(return_value=2)
    with patch.object(canvas, "db", registry_db), patch.object(canvas, "canvas_states", service), patch.object(canvas, "get_current_user_async", AsyncMock(return_value={"id": 3})):
        result = await canvas.trigger_orator_mic_toggle(request(), "stage")
    assert result == {"status": "ok", "broadcasted_to": 2}
    service.toggle_microphone.assert_awaited_once_with("stage")


def test_collaboration_mode_checks_owner_before_updating_registry_state():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3}
    service = MagicMock()
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(canvas, "get_current_user", return_value={"id": 4}), pytest.raises(HTTPException) as error:
        canvas.set_viewer_collab_mode("stage", canvas.ViewerCollabRequest(enabled=True), request())
    assert error.value.status_code == 403
    service.set_viewer_collab_enabled.assert_not_called()


def test_collaboration_mode_requests_agent_observability_update():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3}
    service = MagicMock()
    session = MagicMock()
    manager = MagicMock()
    manager.get_session.return_value = session
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(object_registry, "agent_manager", manager), patch.object(canvas, "get_current_user", return_value={"id": 3}):
        result = canvas.set_viewer_collab_mode("stage", canvas.ViewerCollabRequest(enabled=True), request())

    assert result == {"theater_id": "stage", "viewer_collab_enabled": True}
    service.set_viewer_collab_enabled.assert_called_once_with(True, "stage")
    session.send_collaboration_toggle_observability.assert_called_once_with()
