"""Lifecycle lookup for theater-scoped canvas state.

This deliberately contains no canvas behavior.  Callers select a theater and
then mutate the relevant component directly, e.g. ``get(id).ui.upsert_surface``.
"""

from typing import Optional

from components.canvas.canvas_state_manager import CanvasStateManager
from components.theater_manager import TheaterManager


class CanvasStateService:
    def __init__(self, theater_manager: TheaterManager) -> None:
        self.theater_manager = theater_manager
        self.states: dict[str, CanvasStateManager] = {}

    def get(self, theater_id: Optional[str] = None) -> CanvasStateManager:
        resolved_id = theater_id or self._default_theater_id()
        if resolved_id not in self.states:
            self.states[resolved_id] = CanvasStateManager(resolved_id, self.theater_manager)
        return self.states[resolved_id]

    def _default_theater_id(self) -> str:
        deployed = next((theater.theater_id for theater in self.theater_manager.list_theaters()
                         if theater.status == "deployed"), None)
        return deployed or next((theater_id for theater_id in self.states if theater_id != "default"), "default")
