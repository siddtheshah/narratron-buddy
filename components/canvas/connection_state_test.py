from components.canvas.connection_state import ConnectionState


def test_connection_state_is_transient_and_empty() -> None:
    state = ConnectionState()
    assert state.active_ws_connections == []
    assert state.processed_doodle_message_ids == set()
