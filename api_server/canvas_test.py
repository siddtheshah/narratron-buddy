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
    service.get.assert_called_once_with("stage")
    service.get.return_value.chat.add_message.assert_called_once_with({"author": "Ada", "text": "hello"})


def test_post_chat_uses_verified_identity_for_profile_link():
    service = MagicMock()
    with patch.object(object_registry, "canvas_states", service), patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "get_current_user", return_value={"id": 3, "username": "Ada", "profile_color": "#f97316"}):
        result = canvas.post_chat(canvas.ChatMessage(author="Imposter", text="hello"), request(), "stage")
    assert result == {"status": "ok", "type": "chat"}
    service.get.assert_called_once_with("stage")
    service.get.return_value.chat.add_message.assert_called_once_with(
        {"author": "Ada", "text": "hello", "profile_username": "Ada", "profile_color": "#f97316"}
    )


def test_suggestion_requires_text_and_does_not_call_registry():
    service = MagicMock()
    with patch.object(object_registry, "canvas_states", service), patch.object(canvas, "_require_canvas_access"), pytest.raises(HTTPException) as error:
        canvas.post_chat(canvas.ChatMessage(author="Ada", text="/suggest"), request(), "stage")
    assert error.value.status_code == 400
    service.add_suggestion.assert_not_called()


def test_suggestion_converts_service_validation_error_to_bad_request():
    service = MagicMock()
    service.get.return_value.chat.add_suggestion.side_effect = ValueError("already suggested")
    with patch.object(object_registry, "canvas_states", service), patch.object(canvas, "_require_canvas_access"), pytest.raises(HTTPException) as error:
        canvas.post_chat(canvas.ChatMessage(author="Ada", text="/suggest rain"), request(), "stage")
    assert error.value.status_code == 400
    assert error.value.detail == "already suggested"


def test_upvote_reports_missing_suggestion():
    service = MagicMock()
    service.get.return_value.chat.upvote_suggestion.return_value = False
    with patch.object(object_registry, "canvas_states", service), pytest.raises(HTTPException) as error:
        canvas.upvote_suggestion(canvas.SuggestionVote(voter="Ada", target_author="Lin"), request(), "stage")
    assert error.value.status_code == 404


def test_get_sticky_notes_uses_session_or_canvas_state():
    mock_agent_mgr = MagicMock()
    mock_notepad = MagicMock()
    mock_notepad.get_present_sticky_notes.return_value = [{"topic": "Clue", "info": "Old map"}]
    mock_session = SimpleNamespace(notepad_tool=SimpleNamespace(notepad=mock_notepad))
    mock_agent_mgr.get_session.return_value = mock_session

    with patch.object(canvas, "_require_canvas_access"), patch.object(object_registry, "live_agent_manager", mock_agent_mgr):
        result = canvas.get_sticky_notes(request(), "stage")
    assert result == {"sticky_notes": [{"topic": "Clue", "info": "Old map"}], "hidden_stickies": [], "count": 1}

    # Test fallback to canvas_states
    mock_agent_mgr.get_session.return_value = None
    mock_canvas_states = MagicMock()
    mock_canvas_states.get.return_value.story.sticky_notes.return_value = [{"topic": "Fallback", "info": "Cached note"}]
    with patch.object(canvas, "_require_canvas_access"), patch.object(object_registry, "live_agent_manager", mock_agent_mgr), patch.object(object_registry, "canvas_states", mock_canvas_states):
        result = canvas.get_sticky_notes(request(), "stage")
    assert result == {"sticky_notes": [{"topic": "Fallback", "info": "Cached note"}], "hidden_stickies": [], "count": 1}


def test_get_sticky_notes_returns_configured_hidden_stickies():
    mock_agent_mgr = MagicMock()
    mock_notepad = MagicMock()
    mock_notepad.get_present_sticky_notes.return_value = [
        {"topic": "Visible", "info": "Public"},
        {"topic": "Secret", "info": "Hidden detail"},
    ]
    mock_session = SimpleNamespace(story_planning_tools=SimpleNamespace(notepad=mock_notepad))
    mock_agent_mgr.get_session.return_value = mock_session

    mock_tm = MagicMock()
    mock_tm.get_theater_config.return_value = {
        "story_planning": {
            "hidden_stickies": ["Secret"],
            "stickies": {"Planner Audit": {"hidden": True}},
        }
    }

    with patch.object(canvas, "_require_canvas_access"), \
         patch.object(object_registry, "live_agent_manager", mock_agent_mgr), \
         patch.object(canvas, "theater_manager", mock_tm):
        result = canvas.get_sticky_notes(request(), "stage")

    assert result["hidden_stickies"] == ["Secret", "Planner Audit"]
    assert len(result["sticky_notes"]) == 2



@pytest.mark.asyncio
async def test_toggle_microphone_requires_owner_then_notifies_canvas_connections():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3}
    service = MagicMock()
    first, second = MagicMock(), MagicMock()
    first.send_json = AsyncMock()
    second.send_json = AsyncMock()
    service.get.return_value.connections.active_ws_connections = [first, second]
    with patch.object(canvas, "db", registry_db), patch.object(canvas, "canvas_states", service), patch.object(canvas, "get_current_user_async", AsyncMock(return_value={"id": 3})):
        result = await canvas.trigger_orator_mic_toggle(request(), "stage")
    assert result == {"status": "ok", "broadcasted_to": 2}
    service.get.assert_called_once_with("stage")
    first.send_json.assert_awaited_once_with({"type": "toggle_mic"})
    second.send_json.assert_awaited_once_with({"type": "toggle_mic"})


def test_collaboration_mode_checks_active_orator_before_updating_registry_state() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3}
    service = MagicMock()
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(canvas, "get_current_user", return_value={"id": 4}), pytest.raises(HTTPException) as error:
        canvas.set_viewer_collab_mode("stage", canvas.ViewerCollabRequest(enabled=True), request())
    assert error.value.status_code == 403
    assert "Only the active orator can change collaboration mode." in str(error.value.detail)
    service.get.assert_not_called()


def test_collaboration_mode_requests_agent_observability_update() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3}
    service = MagicMock()
    session = MagicMock()
    manager = MagicMock()
    manager.get_session.return_value = session
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(object_registry, "live_agent_manager", manager), patch.object(canvas, "get_current_user", return_value={"id": 3}):
        result = canvas.set_viewer_collab_mode("stage", canvas.ViewerCollabRequest(enabled=True), request())

    assert result == {"theater_id": "stage", "viewer_collab_enabled": True}
    service.get.assert_called_once_with("stage")
    service.get.return_value.ui.set_viewer_collab_enabled.assert_called_once_with(True)
    session.send_collaboration_toggle_observability.assert_called_once_with()


def test_collaboration_mode_allows_active_orator_when_baton_is_handed_over() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 7}
    service = MagicMock()
    session = MagicMock()
    manager = MagicMock()
    manager.get_session.return_value = session
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(object_registry, "live_agent_manager", manager), patch.object(canvas, "get_current_user", return_value={"id": 7}):
        result = canvas.set_viewer_collab_mode("stage", canvas.ViewerCollabRequest(enabled=True), request())

    assert result == {"theater_id": "stage", "viewer_collab_enabled": True}
    service.get.assert_called_once_with("stage")
    service.get.return_value.ui.set_viewer_collab_enabled.assert_called_once_with(True)
    session.send_collaboration_toggle_observability.assert_called_once_with()


def test_collaboration_mode_rejects_owner_when_baton_handed_to_another_orator() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 7}
    service = MagicMock()
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(canvas, "get_current_user", return_value={"id": 3}), pytest.raises(HTTPException) as error:
        canvas.set_viewer_collab_mode("stage", canvas.ViewerCollabRequest(enabled=True), request())
    assert error.value.status_code == 403
    assert "Only the active orator can change collaboration mode." in str(error.value.detail)
    service.get.assert_not_called()


def test_active_orator_can_pin_canvas_and_agent_is_notified():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    state = MagicMock()
    state.visual.pinned = True
    state.visual.set_pinned.return_value = True
    service = MagicMock()
    service.get.return_value = state
    session = MagicMock(is_alive=True)
    manager = MagicMock()
    manager.get_session.return_value = session

    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(object_registry, "live_agent_manager", manager), patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "get_current_user", return_value={"id": 3}):
        result = canvas.set_canvas_pin("stage", canvas.CanvasPinRequest(pinned=True), request())

    assert result == {"theater_id": "stage", "pinned": True}
    state.visual.set_pinned.assert_called_once_with(True)
    state.persist.assert_called_once_with()
    notification = session.send_content.call_args.args[0].parts[0].text
    assert "orator has pinned the canvas" in notification
    assert "tools are now blocked" in notification


def test_non_orator_cannot_pin_canvas():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    service = MagicMock()
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "canvas_states", service), patch.object(canvas, "_require_canvas_access"), patch.object(canvas, "get_current_user", return_value={"id": 4}), pytest.raises(HTTPException) as error:
        canvas.set_canvas_pin("stage", canvas.CanvasPinRequest(pinned=True), request())

    assert error.value.status_code == 403
    service.get.assert_not_called()


def _text_annotation_state(sender: MagicMock, *, collab_enabled: bool) -> MagicMock:
    state = MagicMock()
    state.connections.processed_doodle_message_ids = set()
    state.connections.active_ws_connections = [sender]
    state.connections.active_user_connections = {sender: {"id": 9}}
    state.ui.viewer_collab_enabled = collab_enabled
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("annotation_id", [None, "existing-label"])
async def test_text_annotation_rejects_non_orator_when_collaboration_is_disabled(annotation_id: str | None) -> None:
    sender = MagicMock()
    sender.state.theater_id = "stage"
    sender.send_json = AsyncMock()
    state = _text_annotation_state(sender, collab_enabled=False)
    deployment = {"theater_id": "stage", "user_id": 3, "active_orator_id": 3}
    message = {
        "type": "text", "x": 0.2, "y": 0.3, "text": "Nope", "size": 32,
        "font": "Outfit", "color": "#ffffff", "client_message_id": "text-1",
        "id": annotation_id,
    }

    with patch.object(canvas.db, "get_deployment", return_value=deployment):
        await canvas._apply_doodle_message(state, message, sender)

    state.doodles.add.assert_not_called()
    state.doodles.save_text.assert_not_called()
    sender.send_json.assert_any_await(
        {"type": "text_annotation_rejected", "client_message_id": "text-1"}
    )
    sender.send_json.assert_any_await({"type": "doodle_ack", "client_message_id": "text-1"})


@pytest.mark.asyncio
@pytest.mark.parametrize("collab_enabled,user_id", [(True, 9), (False, 3)])
async def test_text_annotation_accepts_collaborating_viewer_or_active_orator(collab_enabled: bool, user_id: int) -> None:
    sender = MagicMock()
    sender.state.theater_id = "stage"
    sender.send_json = AsyncMock()
    state = _text_annotation_state(sender, collab_enabled=collab_enabled)
    state.connections.active_user_connections[sender] = {"id": user_id}
    deployment = {"theater_id": "stage", "user_id": 3, "active_orator_id": 3}
    message = {
        "type": "text", "x": 0.2, "y": 0.3, "text": "  A clue  ", "size": 36,
        "font": "Cinzel", "color": "#ffffff", "client_message_id": "text-2",
        "id": "annotation-2",
    }

    with patch.object(canvas.db, "get_deployment", return_value=deployment):
        await canvas._apply_doodle_message(state, message, sender)

    state.doodles.save_text.assert_called_once_with({
        "id": "annotation-2",
        "type": "text", "x": 0.2, "y": 0.3, "text": "A clue",
        "color": "#ffffff", "size": 36.0, "font": "Cinzel",
    })
    sender.send_json.assert_awaited_once_with({"type": "doodle_ack", "client_message_id": "text-2"})


@pytest.mark.asyncio
async def test_stamp_message_rejects_non_contributor() -> None:
    sender = MagicMock()
    sender.state.theater_id = "stage"
    sender.send_json = AsyncMock()
    state = _text_annotation_state(sender, collab_enabled=False)
    state.connections.active_user_connections[sender] = {"id": 99}
    deployment = {"theater_id": "stage", "user_id": 3, "active_orator_id": 3, "contributors": "[]"}
    message = {
        "type": "stamp", "stamp_id": 5, "user_id": 99, "x": 0.4, "y": 0.6,
        "name": "Dragon", "url": "/api/stamps/5", "size": 80,
        "client_message_id": "stamp-1", "id": "stamp-uuid-1",
    }

    with patch.object(canvas.db, "get_deployment", return_value=deployment):
        await canvas._apply_doodle_message(state, message, sender)

    state.doodles.save_stamp.assert_not_called()
    sender.send_json.assert_any_await({"type": "stamp_rejected", "client_message_id": "stamp-1"})
    sender.send_json.assert_any_await({"type": "doodle_ack", "client_message_id": "stamp-1"})


@pytest.mark.asyncio
@pytest.mark.parametrize("rotation", [0, 90, -45, 450])
async def test_stamp_message_accepts_contributor(rotation: float) -> None:
    sender = MagicMock()
    sender.state.theater_id = "stage"
    sender.send_json = AsyncMock()
    state = _text_annotation_state(sender, collab_enabled=False)
    state.connections.active_user_connections[sender] = {"id": 12}
    # User 12 is in contributors list
    deployment = {"theater_id": "stage", "user_id": 3, "active_orator_id": 3, "contributors": "[12]"}
    message = {
        "type": "stamp", "stamp_id": 5, "user_id": 12, "x": 0.4, "y": 0.6,
        "name": "Dragon", "url": "/api/stamps/5", "size": 80,
        "client_message_id": "stamp-2", "id": "stamp-uuid-2",
    }
    if rotation:
        message["rotation"] = rotation
    other_client = MagicMock()
    other_client.send_json = AsyncMock()
    state.connections.active_ws_connections = [sender, other_client]

    with patch.object(canvas.db, "get_deployment", return_value=deployment):
        await canvas._apply_doodle_message(state, message, sender)

    state.doodles.save_stamp.assert_called_once_with({
        "type": "stamp",
        "id": "stamp-uuid-2",
        "stamp_id": 5,
        "user_id": 12,
        "url": "/api/stamps/5",
        "name": "Dragon",
        "x": 0.4,
        "y": 0.6,
        "size": 80.0,
        "rotation": rotation % 360,
    })
    other_client.send_json.assert_awaited_once_with(state.doodles.save_stamp.call_args.args[0])
    sender.send_json.assert_awaited_once_with({"type": "doodle_ack", "client_message_id": "stamp-2"})


@pytest.mark.asyncio
@pytest.mark.parametrize("rotation", ["invalid", "nan", "inf", "-inf", None])
async def test_stamp_message_rejects_invalid_rotation(rotation: str | None) -> None:
    sender = MagicMock()
    sender.state.theater_id = None
    sender.send_json = AsyncMock()
    state = _text_annotation_state(sender, collab_enabled=False)
    await canvas._apply_doodle_message(state, {
        "type": "stamp", "id": "stamp-rotation", "stamp_id": 5,
        "x": 0.5, "y": 0.5, "size": 80, "rotation": rotation,
    }, sender)
    state.doodles.save_stamp.assert_not_called()


@pytest.mark.asyncio
async def test_select_stamp_message_accepts_contributor_and_broadcasts() -> None:
    sender = MagicMock()
    sender.state.theater_id = "stage"
    sender.send_json = AsyncMock()
    other_client = MagicMock()
    other_client.send_json = AsyncMock()
    state = _text_annotation_state(sender, collab_enabled=False)
    state.connections.active_ws_connections = [sender, other_client]
    state.connections.active_user_connections[sender] = {"id": 12}
    state.doodles.select_stamp.return_value = True
    deployment = {"theater_id": "stage", "user_id": 3, "active_orator_id": 3, "contributors": "[12]"}
    message = {
        "type": "select_stamp",
        "id": "stamp-uuid-2",
        "client_message_id": "select-1",
    }

    with patch.object(canvas.db, "get_deployment", return_value=deployment):
        await canvas._apply_doodle_message(state, message, sender)

    state.doodles.select_stamp.assert_called_once_with("stamp-uuid-2")
    other_client.send_json.assert_awaited_once_with({"type": "select_stamp", "id": "stamp-uuid-2"})
    sender.send_json.assert_awaited_once_with({"type": "doodle_ack", "client_message_id": "select-1"})


def test_a2ui_action_relays_authoritative_player_action_and_removes_surface():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    state = MagicMock()
    state.ui.interactive_surfaces = {"sword_card": {"messages": [{"createSurface": {"components": [{
        "id": "grab", "action": {"event": {"name": "grabSword", "context": {"playerAction": "I grab the sword."}}}
    }]}}]}}
    service = MagicMock()
    service.get.return_value = state
    session = MagicMock(websocket_connected=True)
    session.send_user_content.return_value = True
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
            patch.object(canvas, "live_agent_manager", manager), patch.object(canvas, "_require_canvas_access"), \
            patch.object(canvas, "get_current_user", return_value={"id": 3}):
        result = canvas.post_a2ui_action(payload, request(), "stage")

    assert result == {"status": "accepted", "surface_id": "sword_card"}
    sent_text = session.send_user_content.call_args.args[0].parts[0].text
    assert "I grab the sword." in sent_text
    assert "forged client text" not in sent_text
    state.ui.delete_surface.assert_called_once_with("sword_card")


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


def test_orator_command_relays_direct_input_without_creating_chat_message():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    session = MagicMock(websocket_connected=True)
    session.send_user_content.return_value = True
    manager = MagicMock()
    manager.get_session.return_value = session

    with patch.object(canvas, "db", registry_db), patch.object(canvas, "live_agent_manager", manager), \
            patch.object(canvas, "_require_canvas_access"), \
            patch.object(canvas, "get_current_user", return_value={"id": 3}):
        result = canvas.post_orator_command(
            canvas.OratorCommand(text="  Bring   in   a storm.  "), request(), "stage"
        )

    assert result == {"status": "accepted"}
    sent_text = session.send_user_content.call_args.args[0].parts[0].text
    assert "Bring in a storm." in sent_text
    assert "[Orator Command]" in sent_text


def test_orator_command_rejects_non_orator():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    with patch.object(canvas, "db", registry_db), patch.object(canvas, "_require_canvas_access"), \
            patch.object(canvas, "get_current_user", return_value={"id": 9}), pytest.raises(HTTPException) as error:
        canvas.post_orator_command(canvas.OratorCommand(text="Bring in a storm."), request(), "stage")
    assert error.value.status_code == 403


def test_active_orator_can_move_and_delete_a2ui_surface():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 3, "active_orator_id": 3}
    service = MagicMock()
    service.get.return_value.ui.move_surface.return_value = {"left_pct": 76.5, "top_pct": 20.0}
    service.get.return_value.ui.delete_surface.return_value = 1

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
    service.get.return_value.ui.move_surface.assert_called_once_with("health", 76.5, 20.0)
    service.get.return_value.ui.delete_surface.assert_called_once_with("health")


def test_get_latest_image_passes_join_key_to_require_canvas_access():
    theater = MagicMock()
    theater.directory.return_value.exists.return_value = True
    manager = MagicMock()
    manager.theater.return_value = theater
    req = request()
    with patch.object(canvas, "_require_canvas_access") as mock_require, \
         patch.object(canvas, "theater_manager", manager), \
         patch.object(canvas, "_state") as mock_state:
        mock_state.return_value.get_latest_state.return_value = {"latest": "img.png"}
        result = canvas.get_latest_image(req, theater_id="stage", join_key="KEY-123")

    mock_require.assert_called_once_with(req, "stage", join_key="KEY-123")
    assert result == {"latest": "img.png"}


def test_websocket_endpoints_pass_join_key_to_require_canvas_access_async():
    import asyncio
    ws = MagicMock()
    ws.close = AsyncMock()

    with patch.object(canvas, "_require_canvas_access_async", AsyncMock(side_effect=HTTPException(status_code=403))) as mock_require:
        asyncio.run(canvas.websocket_endpoint(ws, theater_id="stage", join_key="KEY-123"))
        mock_require.assert_called_once_with(ws, "stage", join_key="KEY-123")
        ws.close.assert_called_once_with(code=1008)

    ws.close.reset_mock()
    with patch.object(canvas, "_require_canvas_access_async", AsyncMock(side_effect=HTTPException(status_code=403))) as mock_require:
        asyncio.run(canvas.canvas_state_websocket_endpoint(ws, theater_id="stage", join_key="KEY-123"))
        mock_require.assert_called_once_with(ws, "stage", join_key="KEY-123")
        ws.close.assert_called_once_with(code=1008)



@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,persistent", [(True, False), (True, True), (False, False)])
async def test_doodle_mode_is_saved_and_broadcast(enabled: bool, persistent: bool) -> None:
    sender = MagicMock()
    sender.send_json = AsyncMock()
    viewer = MagicMock()
    viewer.send_json = AsyncMock()
    state = MagicMock()
    state.connections.processed_doodle_message_ids = set()
    state.connections.active_ws_connections = [sender, viewer]
    await canvas._apply_doodle_message(state, {
        "type": "toggle_doodles", "enabled": enabled, "persistent": persistent,
        "client_message_id": "mode-1",
    }, sender)
    assert state.doodles.enabled == enabled
    assert state.doodles.persistent == persistent
    state.persist.assert_called_once()
    viewer.send_json.assert_awaited_once_with({
        "type": "doodles_toggle", "enabled": enabled, "persistent": persistent,
    })
    sender.send_json.assert_any_await({
        "type": "doodles_toggle", "enabled": enabled, "persistent": persistent,
    })
    sender.send_json.assert_any_await({"type": "doodle_ack", "client_message_id": "mode-1"})
