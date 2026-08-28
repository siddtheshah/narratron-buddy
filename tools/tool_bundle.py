import logging
from typing import Any, Callable, List, Sequence, Union

from google.adk.tools import BaseTool, FunctionTool
from google.genai import types

logger = logging.getLogger(__name__)


class ToolBundle:
    """Minimal bundle of ADK BaseTool instances.

    Takes a sequence of ADK BaseTool objects or callables, formats tool descriptions using
    native ADK FunctionDeclarations, builds google.genai.types.Tool objects, and provides
    callback methods for system re-injection.
    """

    def __init__(
        self,
        tools: Sequence[Union[BaseTool, Callable, Any]],
    ):
        if not isinstance(tools, (list, tuple, set)):
            raise TypeError(
                f"Parameter 'tools' must be a sequence of ADK BaseTool objects or callables, got {type(tools).__name__}."
            )
        self.tools: List[BaseTool] = []
        for item in tools:
            if isinstance(item, BaseTool):
                self.tools.append(item)
            elif callable(item):
                self.tools.append(FunctionTool(item))
            elif hasattr(item, "_get_declaration"):
                self.tools.append(item)
            else:
                raise TypeError(
                    f"Invalid tool item: {item}. Expected an ADK BaseTool instance or callable."
                )

    def get_declarations(self) -> List[types.FunctionDeclaration]:
        """Extracts google.genai.types.FunctionDeclaration for each tool via ADK's native _get_declaration()."""
        return [
            tool._get_declaration()
            for tool in self.tools
            if hasattr(tool, "_get_declaration") and callable(tool._get_declaration)
        ]

    def to_types_tool(self) -> types.Tool:
        """Builds a google.genai.types.Tool containing all tool FunctionDeclarations."""
        return types.Tool(function_declarations=self.get_declarations())

    def format_descriptions(self) -> str:
        """Formats concise text descriptions for all tools in the bundle reusing ADK FunctionDeclaration metadata."""
        declarations = self.get_declarations()
        lines = ["[System Notification - Re-injected Tool Definitions]"]
        for decl in declarations:
            name = getattr(decl, "name", "unknown")
            desc = getattr(decl, "description", "") or "No description available."
            desc_first_line = desc.strip().splitlines()[0] if desc else "No description"
            lines.append(f"* {name}: {desc_first_line}")
        return "\n".join(lines)
