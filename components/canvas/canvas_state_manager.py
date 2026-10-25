"""Theater-scoped canvas coordinator.

State transitions live in sibling component classes.  This coordinator only
hydrates/persists the theater document and assembles the browser payload.
"""

from __future__ import annotations

import json
import logging
import asyncio
from pathlib import Path
from typing import Any, Optional

from components.canvas import AudioState, ChatManager, ConnectionState, DoodleState, StoryState, ToolResponseState, UIState, VisualState
from components.theater_manager import Theater
from components.canvas.game_state import GameState
from components.notepad import Notepad
from components.story.input_gathering_cycle import story_input_cycle_duration
from components.story.output_display_cycle import story_output_cycle_duration

logger = logging.getLogger("components.canvas_state")
MAX_AGENT_THOUGHT_LENGTH = 360


class CanvasStateManager:
    theater: Optional[Theater] = None
    theater_id: Optional[str] = None
    theater_manager: Optional[object] = None
    connections: Optional[ConnectionState] = None
    visual: Optional[VisualState] = None
    audio: Optional[AudioState] = None
    doodles: Optional[DoodleState] = None
    ui: Optional[UIState] = None
    tool_response: Optional[ToolResponseState] = None
    story: Optional[StoryState] = None
    chat: Optional[ChatManager] = None
    game: GameState | None = None
    notepad: Optional[Notepad] = None

    def __init__(self, theater: Theater) -> None:
        self.theater = theater
        self.theater_id = theater.theater_id
        self.theater_manager = getattr(theater, "manager", None)
        self.connections = ConnectionState()
        self._initialize_state()
        self.load_state_from_disk()
        self.visual.initialize_starting_image()

    def _initialize_state(self) -> None:
        theater = self.theater
        self.visual = VisualState(
            self.theater,
            notify_changed_fn=self.notify_changed,
            on_visual_changed_fn=self._on_visual_changed,
        )
        self.audio = AudioState(self.notify_changed)
        self.doodles = DoodleState(self.persist, theater=self.theater)
        self.ui = UIState(self.persist, self.notify_changed)
        self.tool_response = ToolResponseState(self.notify_changed)
        story_planning_config = theater.config().get("story_planning", {})
        adventure_mode = bool(story_planning_config.get("adventure_mode", False))
        self.story = StoryState(
            self.persist,
            self.notify_changed,
            publish_audio_fn=self.connections.broadcast,
            adventure_mode=adventure_mode,
            cycle_length=story_input_cycle_duration(story_planning_config),
            output_cycle_duration=story_output_cycle_duration(story_planning_config),
        )
        self.chat = ChatManager(self.theater)
        self.game = GameState(self.theater.directory(), self.persist, self.notify_changed)
        enforce_structured = bool(story_planning_config.get("enforce_structured", True)) if adventure_mode else False
        self.notepad = Notepad(
            self.theater,
            canvas_manager=self,
            enforce_structured=enforce_structured,
        )

    def reset(self) -> None:
        """Clear session state in place so connected clients cannot retain history."""
        self.story.output_cycle.close()
        self._initialize_state()
        self.visual.initialize_starting_image()
        self.visual.shown_images_history.clear()
        self.notify_changed()

    def _on_visual_changed(self, changed: bool = True) -> None:
        if changed:
            if not self.doodles.persistent:
                self.doodles.doodles.clear()
            self.ui.interactive_surfaces = {
                surface_id: surface for surface_id, surface in self.ui.interactive_surfaces.items()
                if bool(surface.get("persistent", False))
            }
        self.notify_changed("latest")

    def notify_changed(self, *domains: str) -> None:
        self.connections.notify(*domains)

    def persist(self) -> None:
        self.save_local_theater_data(self.theater.directory())

    def load_state_from_disk(self) -> None:
        directory = self.theater.directory()
        source = directory / "theater.json"
        if not source.exists(): source = directory / "theater_state.json"
        if not source.exists(): return
        try:
            data = json.loads(source.read_text(encoding="utf-8")); state = data.get("canvas_state", data)
            self.visual.load(state); self.audio.load(state); self.doodles.load(state)
            self.ui.load(state); self.story.load(state)
            self.game.load(state.get("game_state"))
            if self.notepad is not None:
                notepad_data = state.get("notepad")
                if isinstance(notepad_data, dict):
                    self.notepad.import_state(notepad_data)
                else:
                    sp_state = self.story.get_story_planning_state()
                    if sp_state:
                        self.notepad.import_state(sp_state)
            self.chat.messages = list(state.get("chat_messages", [])); self.chat.load_suggestions(state.get("suggestions", []))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to load canvas state for %s: %s", self.theater_id, exc)

    def serialized_state(self) -> dict[str, object]:
        audio_serialized = {k: v for k, v in self.audio.serialize().items() if k != "orator_cursor"}
        serialized = {
            **self.visual.serialize(), **audio_serialized, **self.doodles.serialize(),
            **self.ui.serialize(), **self.story.serialize(), **self.game.serialize(),
            "suggestions": self.chat.export_suggestions(), "chat_messages": self.chat.get_messages(),
        }
        if self.notepad is not None:
            serialized["notepad"] = self.notepad.export_state()
        return serialized

    def save_local_theater_data(self, theater_dir: Optional[Path] = None) -> tuple[dict[str, object], list[dict[str, object]]]:
        directory = theater_dir or self.theater.directory(); directory.mkdir(parents=True, exist_ok=True)
        target = directory / "theater.json"; document: dict[str, object] = {}
        if target.exists():
            try: document = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError): pass
        document.setdefault("theater_id", self.theater_id); document.setdefault("name", self.theater_id)
        document["canvas_state"] = self.serialized_state()
        target.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return document, []

    def get_latest_state(self) -> dict[str, object]:
        visual = self.visual.payload()
        sticky_notes = self.notepad.get_present_sticky_notes() if self.notepad is not None else []
        return {**visual, "music": self.audio.payload(), "music_pinned": self.audio.pinned,
                "music_orator_cursor": self.audio.payload().get("orator_cursor"),
                "doodles_enabled": self.doodles.enabled,
                "doodles_persistent": self.doodles.persistent,
                "viewer_collab_enabled": self.ui.viewer_collab_enabled,
                "tool_activity": self.tool_response.activity_payload(),
                "agent_thought": self.tool_response.agent_thought_payload(),
                **self.story.payload(), "sticky_notes": sticky_notes,
                "interactive_surfaces": list(self.ui.interactive_surfaces.values()),
                "game_state": self.game.payload()}
