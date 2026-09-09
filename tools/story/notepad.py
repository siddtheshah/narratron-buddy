"""Durable sticky-note state shared by the adventure story modules."""

from __future__ import annotations

from collections import OrderedDict
import logging
from pathlib import Path
import re
from threading import RLock
from typing import Any, Callable, Dict, Optional

from jinja2 import Template
from pydantic import BaseModel, Field, create_model

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater


logger = logging.getLogger(__name__)

DEFAULT_MAX_STICKY_NOTES = 5
MAX_STICKY_NOTE_TOPIC_CHARS = 100
MAX_STICKY_NOTE_INFO_CHARS = 500
MAX_STICKY_NOTES = 10


class StickyNoteItem(BaseModel):
    topic: str = Field(description="Unique topic or concept for this sticky note.")
    info: str = Field(description="Clear, concise, up-to-date summary of the topic state.")


class DeepPlanUpdate(BaseModel):
    sticky_notes: list[StickyNoteItem] = Field(default_factory=list)


def _safe_model_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", value).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"Field_{cleaned}"
    return cleaned


def build_deep_plan_update_model(definitions: Dict[str, Any]) -> type[BaseModel]:
    if not definitions:
        return DeepPlanUpdate

    sticky_fields: Dict[str, Any] = {}
    for topic, definition in definitions.items():
        field_name = _safe_model_name(topic)
        fields = definition.get("fields")
        if fields:
            submodel_fields: Dict[str, Any] = {}
            for sub_key, sub_desc in fields.items():
                submodel_fields[_safe_model_name(sub_key)] = (
                    str,
                    Field(default=..., description=str(sub_desc or sub_key), alias=sub_key),
                )
            annotation: Any = create_model(f"Sticky_{field_name}", **submodel_fields)
        else:
            annotation = str
        sticky_fields[field_name] = (
            annotation,
            Field(
                default=...,
                alias=topic,
                description=definition.get("description") or topic,
            ),
        )

    structured_stickies = create_model("StructuredStickyNotes", **sticky_fields)
    return create_model(
        "StructuredDeepPlanUpdate",
        sticky_notes=(structured_stickies, Field(default=...)),
    )


def parse_planning_schema(schema: Dict[str, Any]) -> OrderedDict[str, Dict[str, Any]]:
    if not isinstance(schema, dict) or not schema:
        return OrderedDict()
    raw_definitions = (
        schema.get("stickies")
        if isinstance(schema.get("stickies"), dict)
        else schema
    )
    parsed: OrderedDict[str, Dict[str, Any]] = OrderedDict()
    for raw_topic, raw_entry in raw_definitions.items():
        topic = str(raw_topic).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
        if not topic:
            continue
        entry = raw_entry if isinstance(raw_entry, dict) else {"description": str(raw_entry)}
        raw_fields = entry.get("fields")
        fields: Optional[Dict[str, str]] = None
        if isinstance(raw_fields, dict) and raw_fields:
            fields = {str(key).strip(): str(value).strip() for key, value in raw_fields.items() if str(key).strip()}
        elif isinstance(raw_fields, (list, tuple, set)):
            fields = {str(key).strip(): str(key).strip() for key in raw_fields if str(key).strip()}
        initial = entry.get("initial")
        if isinstance(initial, dict):
            initial = {str(key): str(value) for key, value in initial.items()}
        elif initial is not None:
            initial = str(initial)
        elif fields:
            initial = {key: "" for key in fields}
        else:
            initial = ""
        parsed[topic] = {
            "topic": topic,
            "description": str(entry.get("description", "")).strip(),
            "required": bool(entry.get("required", True)),
            "render": str(entry.get("render", entry.get("display_template", entry.get("template", "")))).strip(),
            "fields": fields,
            "initial": initial,
        }
    return parsed


parse_sticky_definitions = parse_planning_schema


def render_structured_sticky(definition: Dict[str, Any], value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:MAX_STICKY_NOTE_INFO_CHARS]
    template = definition.get("render") or definition.get("display_template")
    if isinstance(value, dict):
        if template:
            try:
                try:
                    rendered = template.format(**value) if "{" in template else Template(template).render(**value)
                except (KeyError, IndexError, ValueError):
                    rendered = Template(template).render(**value)
                return rendered.strip()[:MAX_STICKY_NOTE_INFO_CHARS]
            except Exception:
                logger.warning("[Notepad] Failed to render template for '%s'", definition.get("topic"))
        return " | ".join(
            f"{key}: {item}" for key, item in value.items() if item is not None and str(item) != ""
        ).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
    return str(value).strip()[:MAX_STICKY_NOTE_INFO_CHARS]


class Notepad:
    """Own the sticky-note state and its persistence-friendly representation.

    Planning and response modules may read or update this object, but neither
    owns a separate note collection.  The composition root creates the shared
    instance for adventure sessions; ``NotepadTool`` does the same for normal
    narration sessions.
    """

    def __init__(
        self,
        theater: Theater,
        *,
        canvas_manager: CanvasStateManager,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        if theater is None:
            raise ValueError("theater is required.")
        if canvas_manager is None:
            raise ValueError("canvas_manager is required.")
        self.theater = theater
        raw_config = theater.config() or {}
        subconfig = raw_config.get("story_planning", raw_config) if "story_planning" in raw_config else raw_config
        self.config: Dict[str, Any] = subconfig if isinstance(subconfig, dict) else {}
        self._sticky_definitions: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._deep_plan_update_model: type[BaseModel] = DeepPlanUpdate
        self.on_change = on_change
        self.canvas_manager = canvas_manager
        self._sticky_notes: OrderedDict[str, str] = OrderedDict()
        self._structured_sticky_notes: OrderedDict[str, Any] = OrderedDict()
        self._required_stickies: OrderedDict[str, str] = OrderedDict()
        self._sticky_notes_lock = RLock()

        self.max_sticky_notes = DEFAULT_MAX_STICKY_NOTES
        self.configure_schema(self._read_planning_schema())

    @property
    def sticky_definitions(self) -> OrderedDict[str, Dict[str, Any]]:
        return self._sticky_definitions

    @property
    def deep_plan_update_model(self) -> type[BaseModel]:
        return self._deep_plan_update_model

    def _read_planning_schema(self) -> Any:
        schema_data = None
        if hasattr(self.theater, "read_planning_schema"):
            try:
                schema_data = self.theater.read_planning_schema()
            except Exception as exc:
                logger.warning("[Notepad] Failed to read Theater planning schema: %s", exc)
        if not schema_data and hasattr(self.theater, "directory"):
            theater_dir = self.theater.directory()
            if isinstance(theater_dir, Path):
                for name in ("planning.yaml", "planning.yml"):
                    path = theater_dir / name
                    if path.is_file():
                        try:
                            import yaml

                            loaded_schema = yaml.safe_load(path.read_text(encoding="utf-8"))
                            if isinstance(loaded_schema, dict):
                                schema_data = loaded_schema
                                break
                        except Exception as exc:
                            logger.warning("[Notepad] Failed to load planning schema from %s: %s", path, exc)
        if isinstance(schema_data, dict):
            return schema_data
        return self.config.get(
            "planning_schema",
            self.config.get("sticky_definitions", self.config.get("stickies")),
        )

    def configure_schema(self, schema: Any) -> None:
        """Apply the Theater's planning schema before either story module uses the pad."""
        with self._sticky_notes_lock:
            self._sticky_definitions = parse_planning_schema(schema)
            self._deep_plan_update_model = build_deep_plan_update_model(self._sticky_definitions)
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
        if self._sticky_definitions:
            initial = {
                topic: definition["initial"]
                for topic, definition in self._sticky_definitions.items()
                if definition.get("initial") is not None
            }
            validated = self._deep_plan_update_model.model_validate({"sticky_notes": initial}).model_dump(mode="json", by_alias=True)["sticky_notes"]
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
        return render_structured_sticky(self._sticky_definitions[topic], value)

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
                if isinstance(structured, dict):
                    try:
                        structured = self._deep_plan_update_model.model_validate({"sticky_notes": structured}).model_dump(mode="json", by_alias=True)["sticky_notes"]
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
