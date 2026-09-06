"""Theater-scoped canvas coordinator.

State transitions live in sibling component classes.  This coordinator only
hydrates/persists the theater document and assembles the browser payload.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from components.canvas import AudioState, ChatManager, ConnectionState, DoodleState, StoryState, ToolResponseState, UIState, VisualState
from components.theater_manager import TheaterManager

logger = logging.getLogger("components.canvas_state")
MAX_AGENT_THOUGHT_LENGTH = 360


class CanvasStateManager:
    def __init__(self, theater_id: str, theater_manager: TheaterManager, text_beautifier: Optional[Any] = None) -> None:
        self.theater_id, self.theater_manager = theater_id, theater_manager
        self.theater = theater_manager.theater(theater_id)
        self.connections = ConnectionState()
        self.visual = VisualState()
        self.audio = AudioState(self.notify_changed)
        self.doodles = DoodleState(self.persist)
        self.ui = UIState(self.persist, self.notify_changed)
        self.tool_response = ToolResponseState(self.notify_changed)
        self.story = StoryState(self.persist, self.notify_changed)
        self.chat = ChatManager(output_dir=str(self.theater.output_dir() / "chats"))
        self.text_beautifier = text_beautifier
        self.load_state_from_disk()
        self.visual.initialize_starting_image(self.theater_id, self.theater_manager, self.theater)

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
            self.chat.messages = list(state.get("chat_messages", [])); self.chat.load_suggestions(state.get("suggestions", []))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to load canvas state for %s: %s", self.theater_id, exc)

    def serialized_state(self) -> dict[str, object]:
        return {**self.visual.serialize(), **self.audio.serialize(), **self.doodles.serialize(),
                **self.ui.serialize(), **self.story.serialize(),
                "suggestions": self.chat.export_suggestions(), "chat_messages": self.chat.get_messages()}

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

    export_theater_data = save_local_theater_data

    def get_latest_state(self) -> dict[str, object]:
        visual = self.visual.payload(self.theater)
        return {**visual, "music": self.audio.payload(), "doodles_enabled": self.doodles.enabled,
                "viewer_collab_enabled": self.ui.viewer_collab_enabled,
                "tool_activity": self.tool_response.activity_payload(),
                "agent_thought": self.tool_response.agent_thought_payload(),
                **self.story.payload(), "sticky_notes": self.story.sticky_notes(),
                "interactive_surfaces": list(self.ui.interactive_surfaces.values())}
