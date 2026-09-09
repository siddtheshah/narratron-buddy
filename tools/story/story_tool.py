"""Composition root and public facade for story behavior."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
import logging
import math
from typing import Any, Callable, Dict, Optional

from google.adk.sessions import InMemorySessionService
from pydantic import BaseModel, Field

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import TextResponseProvider
from tools.base_tool import BaseTools, with_cooldown
from tools.story.character_manager import CharacterManager
from tools.story.lore_library import LoreLibrary
from tools.story.notepad import Notepad
from tools.story.story_planning_module import StoryPlanningModule
from tools.story.story_response_module import StoryResponseModule


logger = logging.getLogger(__name__)
STORY_LOG_CONTEXT_LINES = 200


class StoryResponseOutput(BaseModel):
    narration: str = ""
    dialogue: list[Dict[str, Any]] = Field(default_factory=list)
    die_rolls: list[Dict[str, Any]] = Field(default_factory=list)


class StoryLogEntry(BaseModel):
    type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action: Optional[str] = None
    output: Optional[StoryResponseOutput] = None


class StoryTool(BaseTools):
    """Public story tool that owns and wires the story subsystem.

    ``StoryResponseModule`` and ``StoryPlanningModule`` deliberately receive
    their dependencies instead of constructing one another. This class is the
    single composition root for the character provider, shared lore library,
    character manager, and ADK session context.
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
        super().__init__(theater, canvas_manager)
        self.text_response_provider = text_response_provider

        raw_config = theater.config() or {}
        subconfig = (
            raw_config.get("story_planning", raw_config)
            if "story_planning" in raw_config
            else raw_config
        )
        self.config: Dict[str, Any] = subconfig if isinstance(subconfig, dict) else {}
        self.action_cooldown_base_seconds = max(
            0.0, float(self.config.get("action_cooldown_base_seconds", self.cooldown_duration or 10.0))
        )
        self.action_cooldown_words_per_second = max(1.0, float(self.config.get("action_cooldown_words_per_second", 5.0)))
        self.action_cooldown_max_seconds = max(self.action_cooldown_base_seconds, float(self.config.get("action_cooldown_max_seconds", 30.0)))
        # This is the single note collection for both story modules.
        self.notepad = Notepad(
            theater,
            canvas_manager=canvas_manager,
            # Adventure planning defaults to strict per-sticky contracts, but
            # theater authors may deliberately opt out.
            enforce_structured=bool(self.config.get("enforce_structured", True)),
        )
        self._story_log: list[Dict[str, Any]] = []
        self._pending_actions: list[str] = []
        self._load_story_log()

        self.lore_library = LoreLibrary(theater=theater)
        self.character_manager = CharacterManager(
            text_response_provider=text_response_provider,
            notepad=self.notepad,
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
            lore_library=self.lore_library,
            character_manager=self.character_manager,
            session_service=self.planning_session_service,
            session_id=self.planning_session_id,
            notepad=self.notepad,
            story_log_context_fn=self.format_recent_story_log,
        )
        self.response_module = StoryResponseModule(
            theater=theater,
            canvas_manager=canvas_manager,
            lore_library=self.lore_library,
            character_manager=self.character_manager,
            notepad=self.notepad,
            session_service=self.response_session_service,
            session_id=self.response_session_id,
        )

        # Cross-module communication stays explicit and is wired only after
        # both modules have been initialized.
        self.notepad.on_change = self.response_module.save_to_session_state
        self.response_module.on_scene_reaction = self._handle_scene_reaction
        self.notepad.sync_story_state()

    def _load_story_log(self) -> None:
        """Restore the theater log into the shared Notepad store."""
        if not getattr(self.theater, "theater_id", ""):
            return
        try:
            if hasattr(self.theater, "read_output_file_lines"):
                lines = self.theater.read_output_file_lines("story_log.jsonl")
            elif hasattr(self.theater, "output_dir"):
                path = self.theater.output_dir() / "story_log.jsonl"
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
            else:
                lines = []
            entries = []
            for line in lines[-STORY_LOG_CONTEXT_LINES:]:
                try:
                    entries.append(StoryLogEntry.model_validate_json(line).model_dump(mode="json"))
                except Exception:
                    logger.warning("[StoryTool] Ignoring invalid story log line.")
            self._story_log = entries
        except Exception as exc:
            logger.warning("[StoryTool] Failed to read theater story log: %s", exc)

    def append_story_log_entry(self, entry: Dict[str, Any]) -> None:
        """Validate, persist, and publish a responder event through Notepad."""
        try:
            parsed = StoryLogEntry.model_validate(entry)
        except Exception as exc:
            logger.warning("[StoryTool] Ignoring invalid story log entry: %s", exc)
            return
        self._story_log.append(parsed.model_dump(mode="json"))
        self._story_log = self._story_log[-STORY_LOG_CONTEXT_LINES:]
        try:
            if hasattr(self.theater, "append_output_file"):
                self.theater.append_output_file("story_log.jsonl", parsed.model_dump_json() + "\n")
            elif hasattr(self.theater, "output_dir"):
                path = self.theater.output_dir() / "story_log.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as output:
                    output.write(parsed.model_dump_json() + "\n")
        except Exception:
            logger.exception("[StoryTool] Failed to append theater story log")

    def format_recent_story_log(self) -> str:
        """Render the shared Notepad log as compact responder context."""
        lines: list[str] = []
        for raw_entry in self._story_log:
            try:
                entry = StoryLogEntry.model_validate(raw_entry)
            except Exception:
                continue
            if entry.type == "user_action" and entry.action:
                lines.append(f"Player: {entry.action}")
            elif entry.output:
                for roll in entry.output.die_rolls:
                    lines.append(f"Roll: {roll.get('count', 1)}d{roll.get('sides', 20)} -> {roll.get('total', 0)}")
                if entry.output.narration:
                    lines.append(f"Narration: {entry.output.narration}")
                for dialogue in entry.output.dialogue:
                    if isinstance(dialogue, dict) and dialogue.get("text"):
                        lines.append(f"Dialogue — {dialogue.get('speaker', '')}: {dialogue['text']}")
        return "\n".join(lines)

    def _handle_scene_reaction(self, result: Dict[str, Any]) -> None:
        entry: Dict[str, Any] = {"type": "story_response"}
        if "error" not in result:
            entry["output"] = {
                "narration": str(result.get("narration") or "").strip(),
                "dialogue": result.get("dialogue") if isinstance(result.get("dialogue"), list) else [],
                "die_rolls": result.get("die_rolls") if isinstance(result.get("die_rolls"), list) else [],
            }
        self.append_story_log_entry(entry)
        if self._pending_actions:
            action = self._pending_actions.pop(0)
            result["deep_plan_revision_used"] = self.planning_module.get_deep_plan().get("revision", 0)
            self.planning_module.queue_deep_planning(result.get("turn_id", 0), action, result)
        if self._on_scene_reaction:
            self._on_scene_reaction(result)

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

    def get_user_action_cooldown_seconds(self) -> float:
        try:
            words = max(0, int(self.response_module._last_action_response_word_count))
        except (TypeError, ValueError):
            words = 0
        return min(self.action_cooldown_max_seconds, self.action_cooldown_base_seconds + math.ceil(words / self.action_cooldown_words_per_second))

    @with_cooldown(
        action_desc="resolving story update",
        duration=lambda tools: tools.get_user_action_cooldown_seconds(),
        tool_name="process_user_action",
    )
    def process_user_action(self, user_action: str, nudge: str = "") -> Dict[str, Any]:
        """Apply the public cooldown before delegating story resolution."""
        self.append_story_log_entry({"type": "user_action", "action": str(user_action).strip()})
        self._pending_actions.append(str(user_action).strip())
        if nudge:
            return self.response_module.process_user_action(user_action, nudge=nudge)
        return self.response_module.process_user_action(user_action)

    def get_tools(self) -> list[Any]:
        return [self.process_user_action]

    @property
    def on_scene_reaction(self) -> Optional[Callable[[Dict[str, Any]], None]]:
        return self.__dict__.get("_on_scene_reaction")

    @on_scene_reaction.setter
    def on_scene_reaction(self, callback: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        self._on_scene_reaction = callback

    @property
    def on_story_response_completed(self) -> Optional[Callable[[], None]]:
        return self.response_module.on_story_response_completed

    @on_story_response_completed.setter
    def on_story_response_completed(self, callback: Optional[Callable[[], None]]) -> None:
        self.response_module.on_story_response_completed = callback

    @property
    def on_cooldown_expired(self) -> Optional[Callable[[str], None]]:
        return self.__dict__.get("_on_cooldown_expired")

    @on_cooldown_expired.setter
    def on_cooldown_expired(self, callback: Optional[Callable[[str], None]]) -> None:
        self._on_cooldown_expired = callback

    @property
    def on_after_tool_call(self) -> Optional[Callable[[str, Dict[str, Any]], None]]:
        return self.__dict__.get("_on_after_tool_call")

    @on_after_tool_call.setter
    def on_after_tool_call(
        self, callback: Optional[Callable[[str, Dict[str, Any]], None]]
    ) -> None:
        self._on_after_tool_call = callback

    @property
    def max_sticky_notes(self) -> int:
        return self.notepad.max_sticky_notes

    @max_sticky_notes.setter
    def max_sticky_notes(self, value: int) -> None:
        self.notepad.max_sticky_notes = max(1, int(value))

    @property
    def max_named_elements(self) -> int:
        return self.notepad.max_named_elements

    def update_sticky_note(self, topic: str, info: str) -> str:
        """Insert or replace one sticky note in the current scene."""
        return self.notepad.update_sticky_note(topic, info)

    def update_or_insert_named_element(self, name: str, content: str) -> str:
        return self.update_sticky_note(topic=name, info=content)

    def get_present_sticky_notes(self) -> list[dict[str, str]]:
        return self.notepad.get_present_sticky_notes()

    def get_present_elements(self) -> list[dict[str, str]]:
        return self.notepad.get_present_elements()

    def get_present_structured_sticky_notes(self) -> Dict[str, Any]:
        return self.notepad.get_present_structured_sticky_notes()

    def get_required_sticky_notes(self) -> list[str]:
        return self.notepad.get_required_sticky_notes()


# Compatibility names retained while callers migrate to ``StoryTool``.
StoryPlanningTools = StoryTool
StoryResponseTool = StoryTool


__all__ = [
    "StoryTool",
    "StoryPlanningTools",
    "StoryResponseTool",
    "StoryLogEntry",
    "StoryResponseOutput",
]
