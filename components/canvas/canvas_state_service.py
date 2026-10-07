"""Lifecycle lookup for theater-scoped canvas state.

This deliberately contains no canvas behavior.  Callers select a theater and
then mutate the relevant component directly, e.g. ``get(id).ui.upsert_surface``.
"""

from components.canvas.canvas_state_manager import CanvasStateManager
from components.theater_manager import TheaterManager


class CanvasStateService:
    def __init__(self, theater_manager: TheaterManager) -> None:
        self.theater_manager = theater_manager
        self.states: dict[str, CanvasStateManager] = {}

    def get(self, theater_id: str) -> CanvasStateManager:
        if not theater_id or not theater_id.strip():
            raise ValueError("theater_id is required")
        resolved_id = theater_id.strip()
        if resolved_id not in self.states:
            self.states[resolved_id] = CanvasStateManager(self.theater_manager.theater(resolved_id))
        return self.states[resolved_id]
