from components.canvas.tool_response_state import ToolResponseState


def test_tool_response_state_exposes_activity_payload() -> None:
    domains: list[str] = []
    state = ToolResponseState(domains.append)
    state.set_activity("image", True)
    assert state.activity_payload()["image_generating"] is True
    assert domains == ["latest"]
