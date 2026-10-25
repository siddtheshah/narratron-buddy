"""Live-agent adapter for the canvas-owned story cycle."""

from collections.abc import Callable
from typing import Optional

from pydantic import JsonValue

from components.canvas_state import CanvasStateManager
from components.lore_library import LoreLibrary
from components.reference_manager import ReferenceManager
from components.story.engine import StoryEngine, StoryLogEntry, StoryResponseOutput
from components.theater_manager import Theater
from providers import SpeechProvider, TextResponseProvider
from tools.base_tool import BaseTools
from tools.tool_metadata import terminal


class StoryTool(BaseTools):
    """Submit spoken actions; all story work belongs to ``StoryEngine``."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        text_response_provider: TextResponseProvider,
        reference_manager: ReferenceManager,
        speech_provider: SpeechProvider | None = None,
        lore_library: Optional[LoreLibrary] = None,
    ) -> None:
        super().__init__(theater, canvas_manager)
        self.engine = StoryEngine(
            theater, canvas_manager, text_response_provider, reference_manager,
            speech_provider=speech_provider, lore_library=lore_library,
        )

    @terminal
    def process_user_action(self, user_action: str, nudge: str = "") -> dict[str, JsonValue]:
        """Queue an action for combination with game updates in the next story cycle."""
        self.log_tool_call("process_user_action", {"user_action": user_action, "nudge": nudge})
        return self.engine.process_user_action(user_action, nudge)

    def get_tools(self) -> list[Callable]:
        return [self.process_user_action]

    @terminal
    def create_or_update_scene(self, name: str, description: str = "") -> str:
        return self.engine.create_or_update_scene(name, description)

    @terminal
    def update_sticky_note(self, topic: str, info: str) -> str:
        return self.engine.update_sticky_note(topic, info)

    def close(self) -> None:
        self.engine.close()

    @property
    def is_action_in_flight(self) -> bool:
        return self.engine.is_action_in_flight

    @property
    def on_scene_reaction(self) -> Callable[[dict[str, JsonValue]], None] | None:
        return self.engine.on_scene_reaction

    @on_scene_reaction.setter
    def on_scene_reaction(self, callback: Callable[[dict[str, JsonValue]], None] | None) -> None:
        self.engine.on_scene_reaction = callback

    @property
    def on_story_response_completed(self) -> Callable[[], None] | None:
        return self.engine.on_story_response_completed

    @on_story_response_completed.setter
    def on_story_response_completed(self, callback: Callable[[], None] | None) -> None:
        self.engine.on_story_response_completed = callback

    def __getattr__(self, name: str) -> Callable:
        """Preserve harness/reference APIs while their implementation moves to the engine."""
        engine = self.__dict__.get("engine")
        if engine is None:
            raise AttributeError(name)
        return getattr(engine, name)


__all__ = ["StoryTool", "StoryEngine", "StoryLogEntry", "StoryResponseOutput"]
