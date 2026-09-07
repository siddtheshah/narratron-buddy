from unittest.mock import MagicMock
from unittest.mock import MagicMock

from tools.observability_tool import ObservabilityTools


def test_observability_tool_requests_update_and_enforces_cooldown():
    theater = MagicMock(theater_id="stage")
    theater.config = MagicMock(return_value={"cooldown_duration": 60})
    tool = ObservabilityTools(theater, MagicMock())
    tool.on_observability_requested = MagicMock(return_value=True)

    assert "Current canvas state sent" in tool.request_canvas_observability()
    assert "on cooldown" in tool.request_canvas_observability()
    tool.on_observability_requested.assert_called_once_with()
