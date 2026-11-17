"""Lifecycle lookup for theater-scoped canvas state.

This deliberately contains no canvas behavior.  Callers select a theater and
then mutate the relevant component directly, e.g. ``get(id).ui.upsert_surface``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from components.canvas.doodle_state import DoodleAction
from components.canvas.canvas_state_manager import CanvasStateManager
from components.theater_manager import TheaterManager

if TYPE_CHECKING:
    from services.text_beautifier import TextBeautifier


class CanvasStateService:
    def __init__(
        self, theater_manager: TheaterManager, *, text_beautifier: TextBeautifier | None = None,
        user_stamp_resolver: Callable[[DoodleAction], bytes | None] | None = None,
    ) -> None:
        """Share supplied services; absence supports unstyled text and local/inline stamps."""
        self.theater_manager = theater_manager
        self.text_beautifier = text_beautifier
        self.user_stamp_resolver = user_stamp_resolver
        self.states: dict[str, CanvasStateManager] = {}

    def get(self, theater_id: str) -> CanvasStateManager:
        if not theater_id or not theater_id.strip():
            raise ValueError("theater_id is required")
        resolved_id = theater_id.strip()
        if resolved_id not in self.states:
            self.states[resolved_id] = CanvasStateManager(
                self.theater_manager.theater(resolved_id), text_beautifier=self.text_beautifier,
                user_stamp_resolver=self.user_stamp_resolver,
            )
        return self.states[resolved_id]

    def reset(self, theater_id: str) -> None:
        state = self.states.get(theater_id)
        if state is not None:
            state.reset()

    def reload_package(self, theater_id: str) -> None:
        state = self.states.get(theater_id)
        if state is not None:
            state.reload_package()

    def persist(self, theater_id: str) -> None:
        """Flush an active canvas without starting a session for an unfinished package."""
        state = self.states.get(theater_id)
        if state is not None:
            state.persist()
