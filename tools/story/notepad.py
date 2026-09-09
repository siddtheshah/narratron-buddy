"""Durable sticky-note state shared by the adventure story modules."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any, Callable, Dict, Optional


DEFAULT_MAX_STICKY_NOTES = 5
MAX_STICKY_NOTE_TOPIC_CHARS = 100
MAX_STICKY_NOTE_INFO_CHARS = 500
MAX_STICKY_NOTES = 10


class Notepad:
    """Own the sticky-note state and its persistence-friendly representation.

    Planning and response modules may read or update this object, but neither
    owns a separate note collection.  The composition root creates the shared
    instance for adventure sessions; ``NotepadTool`` does the same for normal
    narration sessions.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        sticky_definitions: Optional[OrderedDict[str, Dict[str, Any]]] = None,
        update_model: Optional[Any] = None,
        render_structured: Optional[Callable[[Dict[str, Any], Any], str]] = None,
        on_change: Optional[Callable[[], None]] = None,
        canvas_manager: Optional[Any] = None,
    ) -> None:
        self.config = config if isinstance(config, dict) else {}
        self._sticky_definitions = sticky_definitions or OrderedDict()
        self._update_model = update_model
        self._render_structured = render_structured
        self.on_change = on_change
        self.canvas_manager = canvas_manager
        self._sticky_notes: OrderedDict[str, str] = OrderedDict()
        self._structured_sticky_notes: OrderedDict[str, Any] = OrderedDict()
        self._required_stickies: OrderedDict[str, str] = OrderedDict()
        self._sticky_notes_lock = RLock()

        self._configure_required_stickies()
        self.max_sticky_notes = max(
            len(self._required_stickies),
            len(self._sticky_definitions),
            max(
                1,
                min(
                    int(self.config.get("max_sticky_notes", self.config.get("max_named_elements", DEFAULT_MAX_STICKY_NOTES))),
                    MAX_STICKY_NOTES,
                ),
            ),
        )
        self._load_initial_stickies()

    def configure_schema(
        self,
        sticky_definitions: OrderedDict[str, Dict[str, Any]],
        update_model: Any,
        render_structured: Callable[[Dict[str, Any], Any], str],
    ) -> None:
        """Apply the adventure schema before either story module uses the pad."""
        with self._sticky_notes_lock:
            self._sticky_definitions = sticky_definitions
            self._update_model = update_model
            self._render_structured = render_structured
            self._sticky_notes.clear()
            self._structured_sticky_notes.clear()
            self._required_stickies.clear()
            self._configure_required_stickies()
            self.max_sticky_notes = max(
                len(self._required_stickies),
                len(self._sticky_definitions),
                max(
                    1,
                    min(
                        int(self.config.get("max_sticky_notes", self.config.get("max_named_elements", DEFAULT_MAX_STICKY_NOTES))),
                        MAX_STICKY_NOTES,
                    ),
                ),
            )
            self._load_initial_stickies()

    def _configure_required_stickies(self) -> None:
        if self._sticky_definitions:
            for topic, definition in self._sticky_definitions.items():
                if definition.get("required"):
                    self._required_stickies[topic] = ""
            return

        required = self.config.get(
            "required_stickies",
            self.config.get("required_sticky_notes", self.config.get("required_elements", [])),
        )
        if isinstance(required, (list, tuple, set)):
            for value in required:
                if isinstance(value, dict):
                    topic = str(value.get("topic", value.get("name", ""))).strip()
                    info = str(value.get("info", value.get("content", ""))).strip()
                else:
                    topic, info = str(value).strip(), ""
                if topic:
                    self._required_stickies[topic[:MAX_STICKY_NOTE_TOPIC_CHARS]] = info[:MAX_STICKY_NOTE_INFO_CHARS]
        elif isinstance(required, dict):
            for topic, info in required.items():
                clean_topic = str(topic).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                if clean_topic:
                    self._required_stickies[clean_topic] = str(info).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
        elif isinstance(required, str):
            for topic in required.split(","):
                clean_topic = topic.strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                if clean_topic:
                    self._required_stickies[clean_topic] = ""

    def _load_initial_stickies(self) -> None:
        if self._sticky_definitions and self._update_model is not None:
            initial = {
                topic: definition["initial"]
                for topic, definition in self._sticky_definitions.items()
                if definition.get("initial") is not None
            }
            validated = self._update_model.model_validate({"sticky_notes": initial}).model_dump(mode="json", by_alias=True)["sticky_notes"]
            for topic, value in validated.items():
                self._structured_sticky_notes[topic] = value
                self._sticky_notes[topic] = self._render(topic, value)
                if topic in self._required_stickies:
                    self._required_stickies[topic] = self._sticky_notes[topic]
        else:
            initial = self.config.get("initial_sticky_notes", self.config.get("initial_elements", {}))
            if isinstance(initial, dict):
                source = initial.items()
            elif isinstance(initial, list):
                source = (
                    (item.get("topic", item.get("name", "")), item.get("info", item.get("content", "")))
                    for item in initial if isinstance(item, dict)
                )
            else:
                source = ()
            for topic, info in source:
                clean_topic = str(topic).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                if clean_topic:
                    clean_info = str(info).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
                    self._sticky_notes[clean_topic] = clean_info
                    if clean_topic in self._required_stickies and not self._required_stickies[clean_topic]:
                        self._required_stickies[clean_topic] = clean_info
        self._restore_required_and_enforce_limit()

    def _render(self, topic: str, value: Any) -> str:
        if self._render_structured is None:
            return str(value).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
        return self._render_structured(self._sticky_definitions[topic], value)

    def _restore_required_and_enforce_limit(self) -> None:
        for topic, info in self._required_stickies.items():
            self._sticky_notes.setdefault(topic, info)
        while len(self._sticky_notes) > self.max_sticky_notes:
            evict = next((topic for topic in self._sticky_notes if topic not in self._required_stickies), None)
            if evict is None:
                break
            del self._sticky_notes[evict]

    @property
    def max_named_elements(self) -> int:
        return self.max_sticky_notes

    def update_sticky_note(self, topic: str, info: str) -> str:
        clean_topic, clean_info = str(topic or "").strip(), str(info or "").strip()
        if not clean_topic:
            return "Error: Sticky note topic cannot be empty."
        if not clean_info:
            return "Error: Sticky note info cannot be empty."
        if len(clean_topic) > MAX_STICKY_NOTE_TOPIC_CHARS:
            return f"Error: Sticky note topic must be {MAX_STICKY_NOTE_TOPIC_CHARS} characters or fewer."
        if len(clean_info) > MAX_STICKY_NOTE_INFO_CHARS:
            return f"Error: Sticky note info must be {MAX_STICKY_NOTE_INFO_CHARS} characters or fewer."
        dropped_topic = None
        with self._sticky_notes_lock:
            is_update = clean_topic in self._sticky_notes
            if is_update:
                existing = self._sticky_notes[clean_topic]
                if clean_info.count("|") != existing.count("|"):
                    return f"Error: Sticky note divider count mismatch for '{clean_topic}'. Expected valid update for '{existing}'."
                self._sticky_notes[clean_topic] = clean_info
                self._sticky_notes.move_to_end(clean_topic)
            else:
                if len(self._sticky_notes) >= self.max_sticky_notes:
                    dropped_topic = next((key for key in self._sticky_notes if key not in self._required_stickies), None)
                    if dropped_topic is not None:
                        del self._sticky_notes[dropped_topic]
                self._sticky_notes[clean_topic] = clean_info
            count = len(self._sticky_notes)
        self._changed()
        action = "Updated" if is_update else "Added"
        if dropped_topic:
            warning = f" Warning: Maximum limit of {self.max_sticky_notes} sticky notes reached. Oldest sticky note '{dropped_topic}' was dropped to make room."
        elif count >= self.max_sticky_notes and not is_update:
            warning = f" Note: Sticky note limit of {self.max_sticky_notes} reached. Adding another new note will drop the oldest non-required one."
        else:
            warning = ""
        return f"{action} sticky note '{clean_topic}'.{warning}"

    def get_present_sticky_notes(self) -> list[dict[str, str]]:
        with self._sticky_notes_lock:
            return [
                {"topic": topic, "info": info, "name": topic, "content": info}
                for topic, info in self._sticky_notes.items()
            ][-self.max_sticky_notes:]

    def get_present_elements(self) -> list[dict[str, str]]:
        return self.get_present_sticky_notes()

    def get_present_structured_sticky_notes(self) -> Dict[str, Any]:
        with self._sticky_notes_lock:
            return {topic: dict(value) if isinstance(value, dict) else value for topic, value in self._structured_sticky_notes.items()}

    def get_required_sticky_notes(self) -> list[str]:
        return list(self._required_stickies)

    def replace_from_deep_update(self, update: Any) -> None:
        raw_notes = update.sticky_notes
        with self._sticky_notes_lock:
            if self._sticky_definitions:
                structured = update.model_dump(mode="json", by_alias=True)["sticky_notes"]
                self._structured_sticky_notes = OrderedDict(structured)
                self._sticky_notes = OrderedDict((topic, self._render(topic, value)) for topic, value in structured.items())
            elif raw_notes:
                previous = dict(self._sticky_notes)
                notes: OrderedDict[str, str] = OrderedDict()
                for item in raw_notes:
                    topic = item.get("topic", item.get("name", "")) if isinstance(item, dict) else getattr(item, "topic", "")
                    info = item.get("info", item.get("content", "")) if isinstance(item, dict) else getattr(item, "info", "")
                    topic, info = str(topic).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS], str(info).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
                    if topic and info:
                        notes[topic] = info
                for topic, default in self._required_stickies.items():
                    notes.setdefault(topic, previous.get(topic, default))
                self._sticky_notes = notes
                self._restore_required_and_enforce_limit()
        self.sync_story_state()

    def sync_story_state(self) -> None:
        """Publish every note; the canvas controls whether topics are hidden."""
        story_state = getattr(self.canvas_manager, "story", None)
        setter = getattr(story_state, "set_sticky_notes", None)
        if callable(setter):
            setter(self.get_present_sticky_notes())

    def export_state(self) -> Dict[str, Any]:
        with self._sticky_notes_lock:
            all_notes = self.get_present_sticky_notes()
            return {
                "sticky_notes": [{"topic": note["topic"], "info": note["info"]} for note in all_notes],
                "all_sticky_notes": [{"topic": note["topic"], "info": note["info"]} for note in all_notes],
                "structured_sticky_notes": self.get_present_structured_sticky_notes(),
                "named_elements": [{"name": note["name"], "content": note["content"]} for note in all_notes],
            }

    def import_state(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        with self._sticky_notes_lock:
            self._sticky_notes.clear()
            self._structured_sticky_notes.clear()
            if self._sticky_definitions:
                structured = state.get("structured_sticky_notes")
                if not isinstance(structured, dict) and isinstance(state.get("deep_plan"), dict):
                    structured = state["deep_plan"].get("sticky_notes")
                if isinstance(structured, dict) and self._update_model is not None:
                    try:
                        structured = self._update_model.model_validate({"sticky_notes": structured}).model_dump(mode="json", by_alias=True)["sticky_notes"]
                        self._structured_sticky_notes = OrderedDict(structured)
                    except Exception:
                        structured = {}
                for topic, value in self._structured_sticky_notes.items():
                    self._sticky_notes[topic] = self._render(topic, value)
            else:
                notes = state.get("all_sticky_notes", state.get("sticky_notes", state.get("named_elements", [])))
                source = notes.items() if isinstance(notes, dict) else (
                    ((item.get("topic", item.get("name", "")), item.get("info", item.get("content", ""))) for item in notes if isinstance(item, dict))
                    if isinstance(notes, list) else ()
                )
                for topic, info in source:
                    topic, info = str(topic).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS], str(info).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
                    if topic and info:
                        self._sticky_notes[topic] = info
            self._restore_required_and_enforce_limit()

    def _changed(self) -> None:
        self.sync_story_state()
        if self.on_change:
            self.on_change()
