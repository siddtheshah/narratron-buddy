"""Public canvas-state API.

Canvas implementation belongs to ``components.canvas``.  This module is kept
as the stable import surface for routes and integrations.
"""

from components.canvas.canvas_state_manager import CanvasStateManager, MAX_AGENT_THOUGHT_LENGTH

__all__ = ["CanvasStateManager", "MAX_AGENT_THOUGHT_LENGTH"]
