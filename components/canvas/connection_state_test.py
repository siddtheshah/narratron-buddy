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

