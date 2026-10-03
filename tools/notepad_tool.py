"""Sticky-note tool for ordinary, non-adventure narration sessions."""

from __future__ import annotations

from typing import Any, Dict, Optional

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from tools.base_tool import BaseTools, logged_tool_call
from tools.tool_metadata import terminal
from components.notepad import Notepad


class NotepadTool(BaseTools):
    """Expose a lightweight note pad without starting adventure planners."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        notepad: Optional[Notepad] = None,
    ) -> None:
        super().__init__(theater=theater, canvas_manager=canvas_manager)
        if notepad is not None:
            self.notepad = notepad
            self.notepad.on_change = self.save_to_session_state
        else:
            self.notepad = Notepad(
                theater,
                on_change=self.save_to_session_state,
                canvas_manager=canvas_manager,
                enforce_structured=False,
            )
        self.reload_from_session_state()
        self.notepad.sync_story_state()

    @terminal
    @logged_tool_call
    def update_sticky_note(self, topic: str, info: str) -> str:
        """Add or update one durable sticky note for the current narration."""
        return self.notepad.update_sticky_note(topic, info)

    def get_present_sticky_notes(self) -> list[dict[str, str]]:
        return self.notepad.get_present_sticky_notes()

    def get_present_elements(self) -> list[dict[str, str]]:
        return self.notepad.get_present_elements()

    def get_present_structured_sticky_notes(self) -> Dict[str, Any]:
        return self.notepad.get_present_structured_sticky_notes()

    def get_required_sticky_notes(self) -> list[str]:
        return self.notepad.get_required_sticky_notes()

    def save_to_session_state(self) -> None:
        try:
            if self.canvas_manager and hasattr(self.canvas_manager, "story"):
                self.canvas_manager.story.set_story_planning_state(self.notepad.export_state())
        except Exception:
            # A sticky-note update should still succeed if a transient canvas
            # persistence problem occurs.
            return

    def reload_from_session_state(self) -> None:
        try:
            if not self.canvas_manager or not hasattr(self.canvas_manager, "story"):
                return
            state = self.canvas_manager.story.get_story_planning_state()
            if state:
                self.notepad.import_state(state)
            else:
                saved_notes = self.canvas_manager.story.get_sticky_notes()
                if saved_notes:
                    self.notepad.import_state({"sticky_notes": saved_notes})
        except Exception:
            return


__all__ = ["NotepadTool"]
