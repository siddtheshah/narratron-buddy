"""Composition root and public facade for story behavior."""

from __future__ import annotations

import secrets
from typing import Any, Callable, Dict, Optional

from google.adk.sessions import InMemorySessionService

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import TextResponseProvider
from tools.story.character_manager import CharacterManager
from tools.story.lore_library import LoreLibrary
from tools.story.notepad import Notepad
from tools.story.story_planning_module import StoryPlanningModule
from tools.story.story_response_module import StoryResponseModule


class StoryTool:
    """Public story tool that owns and wires the story subsystem.

    ``StoryResponseModule`` and ``StoryPlanningModule`` deliberately receive
    their dependencies instead of constructing one another. This class is the
    single composition root for the shared provider, lore library, character
    manager, and ADK session context.
    """

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        text_response_provider: TextResponseProvider,
    ) -> None:
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")

        self.theater = theater
        self.canvas_manager = canvas_manager
        self.text_response_provider = text_response_provider

        raw_config = theater.config() or {}
        subconfig = (
            raw_config.get("story_planning", raw_config)
            if "story_planning" in raw_config
            else raw_config
        )
        self.config: Dict[str, Any] = subconfig if isinstance(subconfig, dict) else {}
        # This is the single note collection for both story modules.
        self.notepad = Notepad(self.config, canvas_manager=canvas_manager)

        self.lore_library = LoreLibrary(theater=theater)
        self.character_manager = CharacterManager(
            text_response_provider=text_response_provider,
            config=self.config,
        )
        configured_session_id = str(self.config.get("session_id") or "").strip()
        theater_id = getattr(theater, "theater_id", "")
        self.planning_session_service = InMemorySessionService()
        self.planning_session_id = (
            f"{configured_session_id}_planning"
            if configured_session_id
            else f"planner_{theater_id}_{secrets.token_hex(8)}"
        )
        self.response_session_service = InMemorySessionService()
        self.response_session_id = (
            f"{configured_session_id}_response"
            if configured_session_id
            else f"responder_{theater_id}_{secrets.token_hex(8)}"
        )

        self.planning_module = StoryPlanningModule(
            theater=theater,
            canvas_manager=canvas_manager,
            text_response_provider=text_response_provider,
            lore_library=self.lore_library,
            character_manager=self.character_manager,
            session_service=self.planning_session_service,
            session_id=self.planning_session_id,
            notepad=self.notepad,
        )
        self.response_module = StoryResponseModule(
            theater=theater,
            canvas_manager=canvas_manager,
            text_response_provider=text_response_provider,
            lore_library=self.lore_library,
            character_manager=self.character_manager,
            planning_module=self.planning_module,
            notepad=self.notepad,
            session_service=self.response_session_service,
            session_id=self.response_session_id,
        )

        # Cross-module communication stays explicit and is wired only after
        # both modules have been initialized.
        self.character_manager.elements_provider = self.planning_module.get_present_elements
        self.character_manager.on_change = self.response_module.save_to_session_state
        self.planning_module.recent_story_log_fn = self.response_module._format_recent_story_log
        self.planning_module.on_save_state = self.response_module.save_to_session_state
        self.notepad.on_change = self.response_module.save_to_session_state
        self.notepad.sync_story_state()

    def __getattr__(self, name: str) -> Any:
        """Expose the response module's existing public tool surface."""
        response_module = self.__dict__.get("response_module")
        if response_module is not None:
            try:
                return getattr(response_module, name)
            except AttributeError:
                pass
        planning_module = self.__dict__.get("planning_module")
        if planning_module is not None:
            try:
                return getattr(planning_module, name)
            except AttributeError:
                pass
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    @property
    def on_scene_reaction(self) -> Optional[Callable[[Dict[str, Any]], None]]:
        return self.response_module.on_scene_reaction

    @on_scene_reaction.setter
    def on_scene_reaction(self, callback: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        self.response_module.on_scene_reaction = callback

    @property
    def on_story_response_completed(self) -> Optional[Callable[[], None]]:
        return self.response_module.on_story_response_completed

    @on_story_response_completed.setter
    def on_story_response_completed(self, callback: Optional[Callable[[], None]]) -> None:
        self.response_module.on_story_response_completed = callback

    @property
    def on_cooldown_expired(self) -> Optional[Callable[[str], None]]:
        return self.response_module.on_cooldown_expired

    @on_cooldown_expired.setter
    def on_cooldown_expired(self, callback: Optional[Callable[[str], None]]) -> None:
        self.response_module.on_cooldown_expired = callback

    @property
    def on_after_tool_call(self) -> Optional[Callable[[str, Dict[str, Any]], None]]:
        return self.response_module.on_after_tool_call

    @on_after_tool_call.setter
    def on_after_tool_call(
        self, callback: Optional[Callable[[str, Dict[str, Any]], None]]
    ) -> None:
        self.response_module.on_after_tool_call = callback

    @property
    def max_sticky_notes(self) -> int:
        return self.planning_module.max_sticky_notes

    @max_sticky_notes.setter
    def max_sticky_notes(self, value: int) -> None:
        self.planning_module.max_sticky_notes = value

# Compatibility names retained while callers migrate to ``StoryTool``.
StoryPlanningTools = StoryTool
StoryResponseTool = StoryTool


__all__ = ["StoryTool", "StoryPlanningTools", "StoryResponseTool"]
