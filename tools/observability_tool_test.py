from unittest.mock import MagicMock

from tools.observability_tool import ObservabilityTools


def test_observability_tool_requests_update_and_enforces_cooldown():
    tool = ObservabilityTools({"cooldown_duration": 60}, theater_id="stage")
    tool.on_observability_requested = MagicMock(return_value=True)

    assert "Current canvas state sent" in tool.request_canvas_observability()
    assert "on cooldown" in tool.request_canvas_observability()
    tool.on_observability_requested.assert_called_once_with()
