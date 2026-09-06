from components.canvas.tool_response_state import ToolResponseState


def test_tool_response_state_exposes_activity_payload() -> None:
    domains: list[str] = []
    state = ToolResponseState(domains.append)
    state.set_activity("image", True)
    assert state.activity_payload()["image_generating"] is True
    assert domains == ["latest"]


def test_tool_response_state_initial_payloads() -> None:
    state = ToolResponseState(lambda *_: None)
    payload = state.activity_payload()
    assert payload == {
        "image_generating": False,
        "animation_generating": False,
        "user_action_processing": False,
        "live_ready": False,
        "dice_rolling": False,
        "dice_result": None,
    }
    thought = state.agent_thought_payload()
    assert thought == {"text": "", "time": 0.0}


def test_set_activity_animation_and_user_action() -> None:
    events = []
    state = ToolResponseState(events.append)

    state.set_activity("animation", True)
    assert state.animation_generation_active is True
    assert state.activity_payload()["animation_generating"] is True

    state.set_activity("user_action", True)
    assert state.user_action_processing_active is True
    assert state.activity_payload()["user_action_processing"] is True

    state.set_activity("animation", False)
    assert state.animation_generation_active is False
    assert state.activity_payload()["animation_generating"] is False

    assert events == ["latest", "latest", "latest"]


def test_set_activity_live_connection_and_expiration() -> None:
    state = ToolResponseState(lambda *_: None)

    # Active live connection
    state.set_activity("live", active=True, recent_seconds=10.0)
    assert state.live_connection_active is True
    assert state.activity_payload()["live_ready"] is True

    # When inactive but recent_seconds window hasn't expired yet
    state.live_connection_active = False
    state.live_connection_until = 9999999999.0
    assert state.activity_payload()["live_ready"] is True

    # When inactive and window has expired
    state.live_connection_until = 1.0
    assert state.activity_payload()["live_ready"] is False

    # Setting inactive resets live_connection_until to 0.0
    state.set_activity("live", active=False)
    assert state.live_connection_until == 0.0
    assert state.activity_payload()["live_ready"] is False


def test_set_activity_dice_rolling_and_result() -> None:
    state = ToolResponseState(lambda *_: None)

    roll_data = {"d20": 18, "modifier": 3, "total": 21}
    state.set_activity("dice", active=True, recent_seconds=10.0, result=roll_data)
    assert state.dice_roll_result == roll_data

    payload = state.activity_payload()
    assert payload["dice_rolling"] is True
    assert payload["dice_result"] == roll_data

    # Expired roll
    state.dice_roll_until = 1.0
    expired_payload = state.activity_payload()
    assert expired_payload["dice_rolling"] is False
    assert expired_payload["dice_result"] is None

    # Deactivating clears until time and result
    state.set_activity("dice", active=False)
    assert state.dice_roll_until == 0.0
    assert state.dice_roll_result is None


def test_set_activity_unknown_tool_notifies_gracefully() -> None:
    events = []
    state = ToolResponseState(events.append)
    state.set_activity("custom_nonexistent_tool", True)
    assert events == ["latest"]


def test_set_agent_thought_normal_and_empty() -> None:
    events = []
    state = ToolResponseState(events.append)

    state.set_agent_thought("  Analyzing tactical map...  ")
    assert state.current_agent_thought == "Analyzing tactical map..."
    assert state.current_agent_thought_time > 0.0
    assert state.agent_thought_payload() == {
        "text": "Analyzing tactical map...",
        "time": state.current_agent_thought_time,
    }

    # Setting empty thought resets timestamp to 0.0
    state.set_agent_thought("   ")
    assert state.current_agent_thought == ""
    assert state.current_agent_thought_time == 0.0
    assert state.agent_thought_payload() == {"text": "", "time": 0.0}


def test_set_agent_thought_truncation_with_ellipsis() -> None:
    state = ToolResponseState(lambda *_: None)

    short_text = "x" * 50
    state.set_agent_thought(short_text, maximum_length=50)
    assert state.current_agent_thought == short_text

    long_text = "The quick brown fox jumps over the lazy dog"
    state.set_agent_thought(long_text, maximum_length=20)
    # Truncates to maximum_length - 1 (19 chars), rstrips, and appends ellipsis '…'
    assert state.current_agent_thought.endswith("…")
    assert len(state.current_agent_thought) <= 20

