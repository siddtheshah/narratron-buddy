from types import SimpleNamespace
import unittest

from google.adk.tools import FunctionTool

from services.live_stream_service import get_bound_tool_instance


class _ImageTools:
    def create_image(self, prompt: str) -> str:
        return prompt


class TestGetBoundToolInstance(unittest.TestCase):
    def test_returns_instance_owning_registered_tool(self):
        image_tools = _ImageTools()
        agent = SimpleNamespace(tools=[FunctionTool(image_tools.create_image)])

        self.assertIs(get_bound_tool_instance(agent, "create_image"), image_tools)


if __name__ == "__main__":
    unittest.main()
