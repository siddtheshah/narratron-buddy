import pytest
from unittest.mock import AsyncMock, MagicMock
from components.canvas.connection_state import ConnectionState


def test_connection_state_is_transient_and_empty() -> None:
    state = ConnectionState()
    assert state.active_ws_connections == []
    assert state.processed_doodle_message_ids == set()


def test_connection_state_broadcast_delegates_to_publish_scene_audio() -> None:
    state = ConnectionState()
    published = []
    state.broadcast = published.append
    msg = {"type": "scene_speech_ready", "speaker": "Narrator"}
    state.broadcast(msg)
    assert published == [msg]


@pytest.mark.asyncio
async def test_connection_state_publish_scene_audio_with_active_connections() -> None:
    state = ConnectionState()
    mock_ws = MagicMock()
    mock_ws.send_json = AsyncMock()
    state.register_state_websocket(mock_ws)

    msg = {"type": "scene_speech_ready", "speaker": "Alice"}
    state.broadcast(msg)

    # Allow event loop task to run
    import asyncio
    await asyncio.sleep(0.01)
    mock_ws.send_json.assert_awaited_once_with(msg)


def test_notify_increments_state_revision() -> None:
    state = ConnectionState()
    assert state.state_revision == 0
    state.notify("latest")
    assert state.state_revision == 1
    state.notify("audio", "visual")
    assert state.state_revision == 2


def test_register_and_unregister_websocket_with_users() -> None:
    state = ConnectionState()
    ws1, ws2 = MagicMock(), MagicMock()
    user1 = {"id": "u1", "username": "Alice"}

    state.register_websocket(ws1, user=user1)
    assert state.active_ws_connections == [ws1]
    assert state.active_user_connections[ws1] == user1

    # Idempotent registration
    state.register_websocket(ws1, user=user1)
    assert len(state.active_ws_connections) == 1

    state.register_websocket(ws2, user=None)
    assert len(state.active_ws_connections) == 2

    # Unregister ws1
    state.unregister_websocket(ws1)
    assert ws1 not in state.active_ws_connections
    assert ws1 not in state.active_user_connections
    assert ws2 in state.active_ws_connections

    # Unregistering non-registered websocket is a graceful no-op
    state.unregister_websocket(MagicMock())


def test_get_active_viewers_deduplicates_by_id_and_filters_unauthenticated() -> None:
    state = ConnectionState()
    ws1, ws2, ws3, ws4 = MagicMock(), MagicMock(), MagicMock(), MagicMock()

    # Two sockets for same user Alice
    state.register_websocket(ws1, user={"id": "user_1", "username": "Alice"})
    state.register_websocket(ws2, user={"id": "user_1", "username": "Alice"})
    # User Bob
    state.register_websocket(ws3, user={"id": "user_2", "username": "Bob"})
    # Anonymous socket without user / without id
    state.register_websocket(ws4, user=None)

    viewers = state.get_active_viewers()
    assert len(viewers) == 2
    assert {"id": "user_1", "username": "Alice"} in viewers
    assert {"id": "user_2", "username": "Bob"} in viewers


def test_unregister_state_websocket() -> None:
    state = ConnectionState()
    ws = MagicMock()
    state.active_state_ws_connections.append(ws)

    state.unregister_state_websocket(ws)
    assert state.active_state_ws_connections == []

    # Safe to call when not in list
    state.unregister_state_websocket(ws)


@pytest.mark.asyncio
async def test_broadcast_ws_message_skips_sender_and_unregisters_failing_socket() -> None:
    state = ConnectionState()
    sender_ws = MagicMock()
    sender_ws.send_json = AsyncMock()

    healthy_ws = MagicMock()
    healthy_ws.send_json = AsyncMock()

    broken_ws = MagicMock()
    broken_ws.send_json = AsyncMock(side_effect=RuntimeError("Socket closed"))

    state.register_websocket(sender_ws)
    state.register_websocket(healthy_ws)
    state.register_websocket(broken_ws)

    msg = {"action": "cursor_moved", "x": 10, "y": 20}
    await state.broadcast_ws_message(msg, sender=sender_ws)

    # Sender should be skipped
    sender_ws.send_json.assert_not_called()
    # Healthy socket should receive message
    healthy_ws.send_json.assert_awaited_once_with(msg)
    # Broken socket should have been unregistered
    assert broken_ws not in state.active_ws_connections
    assert healthy_ws in state.active_ws_connections


@pytest.mark.asyncio
async def test_broadcast_state_ws_message_unregisters_failing_socket() -> None:
    state = ConnectionState()

    good_ws = MagicMock()
    good_ws.send_json = AsyncMock()

    bad_ws = MagicMock()
    bad_ws.send_json = AsyncMock(side_effect=ConnectionResetError("Peer closed connection"))

    state.active_state_ws_connections = [good_ws, bad_ws]

    msg = {"type": "canvas_update"}
    await state.broadcast_state_ws_message(msg)

    good_ws.send_json.assert_awaited_once_with(msg)
    assert good_ws in state.active_state_ws_connections
    assert bad_ws not in state.active_state_ws_connections


def test_broadcast_noop_when_no_active_connections_or_closed_loop() -> None:
    state = ConnectionState()
    # No active state connections
    state.broadcast({"type": "noop"})

    # Active connection but loop is closed
    ws = MagicMock()
    state.active_state_ws_connections = [ws]
    import asyncio
    loop = asyncio.new_event_loop()
    loop.close()
    state.state_ws_loop = loop
    state.broadcast({"type": "noop"})
    ws.send_json.assert_not_called()


