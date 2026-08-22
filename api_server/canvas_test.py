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


def test_get_sticky_notes_uses_session_or_canvas_state():
    mock_agent_mgr = MagicMock()
    mock_session = MagicMock()
    mock_tools = MagicMock()
    mock_tools.get_present_sticky_notes.return_value = [{"topic": "Clue", "info": "Old map"}]
    mock_session.story_planning_tools = mock_tools
    mock_agent_mgr.get_session.return_value = mock_session

    with patch.object(canvas, "_require_canvas_access"), patch.object(object_registry, "agent_manager", mock_agent_mgr):
        result = canvas.get_sticky_notes(request(), "stage")
    assert result == {"sticky_notes": [{"topic": "Clue", "info": "Old map"}], "count": 1}

    # Test fallback to canvas_states
    mock_agent_mgr.get_session.return_value = None
    mock_canvas_states = MagicMock()
    mock_canvas_states.get_sticky_notes.return_value = [{"topic": "Fallback", "info": "Cached note"}]
    with patch.object(canvas, "_require_canvas_access"), patch.object(object_registry, "agent_manager", mock_agent_mgr), patch.object(object_registry, "canvas_states", mock_canvas_states):
        result = canvas.get_sticky_notes(request(), "stage")
    assert result == {"sticky_notes": [{"topic": "Fallback", "info": "Cached note"}], "count": 1}



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


def test_a2ui_action_relays_authoritative_player_action_and_removes_surface():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    state = MagicMock()
    state.get_interactive_action.return_value = {
        "name": "grabSword",
        "context": {"playerAction": "I grab the sword."},
    }
    service = MagicMock()
    service.get.return_value = state
    session = MagicMock(websocket_connected=True)
    session.send_content.return_value = True
    manager = MagicMock()
    manager.get_session.return_value = session
    payload = canvas.A2UIActionEnvelope(
        version="v1.0",
        action=canvas.A2UIActionBody(
            name="grabSword",
            surfaceId="sword_card",
            sourceComponentId="grab",
            timestamp="2026-08-21T12:00:00Z",
            context={"playerAction": "forged client text"},
        ),
    )

    with patch.object(canvas, "db", registry_db), patch.object(canvas, "canvas_states", service), \
            patch.object(canvas, "agent_manager", manager), patch.object(canvas, "_require_canvas_access"), \
            patch.object(canvas, "get_current_user", return_value={"id": 3}):
        result = canvas.post_a2ui_action(payload, request(), "stage")

    assert result == {"status": "accepted", "surface_id": "sword_card"}
    sent_text = session.send_content.call_args.args[0].parts[0].text
    assert "I grab the sword." in sent_text
    assert "forged client text" not in sent_text
    state.delete_interactive_surface.assert_called_once_with("sword_card")


def test_a2ui_action_rejects_non_orator():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    payload = canvas.A2UIActionEnvelope(
        version="v1.0",
        action=canvas.A2UIActionBody(
            name="grabSword", surfaceId="sword_card", sourceComponentId="grab",
            timestamp="2026-08-21T12:00:00Z",
        ),
    )
    with patch.object(canvas, "db", registry_db), patch.object(canvas, "_require_canvas_access"), \
            patch.object(canvas, "get_current_user", return_value={"id": 9}), pytest.raises(HTTPException) as error:
        canvas.post_a2ui_action(payload, request(), "stage")
    assert error.value.status_code == 403


def test_active_orator_can_move_and_delete_a2ui_surface():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    service = MagicMock()
    service.move_interactive_surface.return_value = {"left_pct": 76.5, "top_pct": 20.0}
    service.delete_interactive_surface.return_value = 1

    with patch.object(canvas, "db", registry_db), patch.object(canvas, "canvas_states", service), \
            patch.object(canvas, "_require_canvas_access"), \
            patch.object(canvas, "get_current_user", return_value={"id": 3}):
        moved = canvas.move_a2ui_surface(
            "health",
            canvas.A2UISurfacePlacement(left_pct=76.5, top_pct=20),
            request(),
            "stage",
        )
        deleted = canvas.delete_a2ui_surface("health", request(), "stage")

    assert moved["placement"] == {"left_pct": 76.5, "top_pct": 20.0}
    assert deleted == {"status": "deleted", "surface_id": "health"}
    service.move_interactive_surface.assert_called_once_with("health", 76.5, 20.0, "stage")
    service.delete_interactive_surface.assert_called_once_with("health", "stage")
