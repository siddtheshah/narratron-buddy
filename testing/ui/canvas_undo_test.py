"""Backend state and WebSocket handler tests for canvas doodle undo/removal."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from components.canvas.doodle_state import DoodleState


def test_doodle_state_remove_draw_backend() -> None:
    persist_called = False

    def on_persist() -> None:
        nonlocal persist_called
        persist_called = True

    ds = DoodleState(persist=on_persist)
    ds.doodles = [
        {"type": "draw", "id": "s1", "stroke_id": "str1", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2},
        {"type": "draw", "id": "s2", "stroke_id": "str1", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3},
        {"type": "draw", "id": "s3", "stroke_id": "str2", "x0": 0.4, "y0": 0.4, "x1": 0.5, "y1": 0.5},
        {"type": "text", "id": "t1", "text": "Keep"},
    ]

    # Remove by stroke_id
    ds.remove_draw(stroke_id="str1")
    assert len(ds.doodles) == 2
    assert ds.doodles[0]["id"] == "s3"
    assert ds.doodles[1]["id"] == "t1"
    assert persist_called is True

    # Remove by id
    persist_called = False
    ds.remove_draw(ids=["s3"])
    assert len(ds.doodles) == 1
    assert ds.doodles[0]["id"] == "t1"
    assert persist_called is True


@pytest.mark.asyncio
async def test_api_server_remove_draw_websocket_message() -> None:
    from api_server import canvas

    state = MagicMock()
    state.connections = MagicMock()
    state.connections.processed_doodle_message_ids = set()
    state.connections.active_ws_connections = []
    state.doodles = MagicMock()
    state.doodles.page_id = ""

    sender = AsyncMock()
    sender.state = MagicMock()
    sender.state.theater_id = "test_stage"

    msg = {
        "type": "remove_draw",
        "stroke_id": "stroke-99",
        "ids": ["s1", "s2"],
        "client_message_id": "msg-123",
    }
    await canvas._apply_doodle_message(state, msg, sender)
    state.doodles.remove_draw.assert_called_once_with(stroke_id="stroke-99", ids=["s1", "s2"])
    sender.send_json.assert_awaited_once_with({"type": "doodle_ack", "client_message_id": "msg-123"})
