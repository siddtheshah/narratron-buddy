"""Tool-owned continuation policy, preserved when callables are wrapped by ADK."""

from collections.abc import Callable
from typing import ParamSpec, TypeVar

from google.adk.tools import FunctionTool

Parameters = ParamSpec("Parameters")
Result = TypeVar("Result")


def terminal(func: Callable[Parameters, Result]) -> Callable[Parameters, Result]:
    """Mark an action whose non-error result needs no further model decision."""
    func.__dict__["terminal"] = True
    return func


def annotated_function_tool(func: Callable[..., Result]) -> FunctionTool:
    """Carry callable metadata into ADK's explicit custom-metadata contract."""
    tool = FunctionTool(func)
    tool.custom_metadata = {"terminal": func.__dict__.get("terminal") is True}
    return tool
