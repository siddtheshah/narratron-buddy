import unittest

from google.adk.tools import FunctionTool
from google.adk.tools.load_artifacts_tool import LoadArtifactsTool
from google.genai import types

from testing.base import BaseTestCase
from tools.tool_bundle import ToolBundle


def sample_weather_tool(city: str, metric: str = "celsius") -> str:
    """Gets current weather for a city.

    Args:
        city: City name.
        metric: Metric system (celsius or fahrenheit).
    """
    return f"Weather in {city}: 25 {metric}"


class TestMinimalToolBundle(BaseTestCase):
    def test_init_with_tools_list_and_callables(self):
        load_art = LoadArtifactsTool()
        bundle = ToolBundle([sample_weather_tool, load_art])

        self.assertEqual(len(bundle.tools), 2)
        self.assertIsInstance(bundle.tools[0], FunctionTool)
        self.assertEqual(bundle.tools[1], load_art)

    def test_get_declarations_and_to_types_tool(self):
        bundle = ToolBundle([sample_weather_tool, LoadArtifactsTool()])

        declarations = bundle.get_declarations()
        self.assertEqual(len(declarations), 2)
        self.assertIsInstance(declarations[0], types.FunctionDeclaration)
        self.assertEqual(declarations[0].name, "sample_weather_tool")

        types_tool = bundle.to_types_tool()
        self.assertIsInstance(types_tool, types.Tool)
        self.assertEqual(len(types_tool.function_declarations), 2)

    def test_format_descriptions(self):
        bundle = ToolBundle([sample_weather_tool, LoadArtifactsTool()])

        formatted = bundle.format_descriptions()
        self.assertIn("[System Notification - Re-injected Tool Definitions]", formatted)
        self.assertIn("* sample_weather_tool: Gets current weather for a city.", formatted)
        self.assertIn("* load_artifacts: Loads artifacts into the session for this request.", formatted)

    def test_invalid_parameters_raise_error(self):
        with self.assertRaises(TypeError):
            ToolBundle("not_a_sequence")

        with self.assertRaises(TypeError):
            ToolBundle([12345])


if __name__ == "__main__":
    unittest.main()
