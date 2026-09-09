"""Story planning module: owns sticky notes and background deep planning."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import json
import logging
import os
import re
import threading
from threading import Lock
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from jinja2 import Template
from pydantic import BaseModel, Field, PrivateAttr, create_model
from google.adk.agents import Agent
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.models.google_llm import Gemini
from google.adk.runners import RunConfig, Runner
from google.adk.sessions import InMemorySessionService
from google import genai
from google.genai import types

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import TextResponseProvider
from tools.story.character_manager import CharacterManager
from tools.story.lore_library import LoreLibrary

logger = logging.getLogger(__name__)

DEFAULT_COMPACTION_TRIGGER_TOKENS = 12_000
DEFAULT_COMPACTION_TARGET_TOKENS = 6_000
DEFAULT_MAX_STICKY_NOTES = 5
DEFAULT_STORY_PLANNING_STYLE = "balanced, consequence-driven, and player-agency-first"
DEFAULT_DEEP_THINKING_BUDGET = 2048
DEFAULT_DEEP_MAX_OUTPUT_TOKENS = 6144
DEFAULT_DEEP_PLANNER_TIMEOUT_SECONDS = 45.0
DEFAULT_DEEP_HEARTBEAT_SECONDS = 1.0
DEFAULT_DEEP_MAX_EVENTS_PER_TURN = 4
DEFAULT_DEEP_IDLE_REFINEMENT_TURNS = 0
DEFAULT_DEEP_HEARTBEAT_FAILURE_RETRIES = 2
MAX_STICKY_NOTE_TOPIC_CHARS = 100
MAX_STICKY_NOTE_INFO_CHARS = 500
MAX_STICKY_NOTES = 10
STORY_LOG_CONTEXT_LINES = 200
MAX_DEEP_READ_LORE_CALLS_PER_RUN = 3
MAX_DEEP_SEARCH_LORE_CALLS_PER_RUN = 3


_DEEP_PLANNING_PROMPT_TEMPLATE = Template(
"""# Role & Mission
You are the deep planner for an interactive adventure. You do not narrate the current scene and you never choose actions, speech, thoughts, or feelings for the player. Take a broad view of the player's accumulated actions, established lore, unresolved setups, off-screen actors, deadlines, and world consequences.

You are the sole owner of Adventure Mode sticky notes. Produce the complete replacement sticky-note state after assimilating the committed turn below.
Populate `sticky_notes` first and keep all strategic prose concise. A response without the complete sticky object is invalid and will not advance the queue.

# Non-Negotiable Planning Rules
- Committed events are immutable facts. Replan around them; never retcon them to protect an outline.
- Preserve player agency. Never prescribe player actions.
- Maintain causal continuity across many turns. Advance factions and antagonists off-screen when their knowledge, resources, motives, and elapsed time justify it.
- Audit earlier player behavior when lore calls for counter-plotting. Opposition should investigate evidence the player plausibly left, but must not gain impossible knowledge.
- Use the author-defined sticky fields as the only durable planning vocabulary. Do not invent a parallel generic summary, thread tracker, consequence list, clock, or faction model.
- Think across short, medium, and long horizons, but publish only the configured sticky state.
- Do not reveal private plans merely because they are in planning state. Only sticky notes are supplied to the turn responder.
- Every sticky note must reflect established state or actionable pressure. Do not put a planned twist into a player-facing sticky before it becomes established, except in an explicitly configured hidden tracking sticky intended for the story system.

# Current Deep Plan
{% if deep_plan_json -%}
{{ deep_plan_json }}
{% else -%}
(No deep plan exists yet. Bootstrap one from the lore, initial stickies, and committed turn.)
{% endif -%}

# Sticky State Contract
{% if sticky_schema_json -%}
`sticky_notes` must be a JSON object keyed by the exact configured topics below. For topics with configured fields, return a JSON object of string values for each field. For single-value topics, return a string value. Return every configured topic and no others. Never encode this state as prose.
{{ sticky_schema_json }}

# Current Structured Sticky State
{{ structured_sticky_json }}
{% else -%}
# Current Sticky Notes
{% for note in current_notes -%}
- {{ note.topic }}: {{ note.info }}
{% else -%}
(No active sticky notes)
{% endfor -%}

# Required Sticky Notes
The following topics must all be present in the complete output. You may reorganize their prose or `|`-separated structure when that makes the state clearer:
{% for topic in required_stickies -%}
- {{ topic }}
{% else -%}
(None)
{% endfor -%}
{% endif -%}

# Heartbeat Work
This is one shallow, bounded planning heartbeat. Assimilate only the supplied queue batch, then return a useful complete plan without exhaustive deliberation. Later heartbeats can refine it.
{% if turn_events -%}
## Newly Committed Turn Queue
{% for event in turn_events -%}
### Turn {{ event.turn_id }}
- Player action: {{ event.user_action }}
- Immediate narration: {{ event.narration }}
{% if event.dialogue -%}
- NPC dialogue:
{% for line in event.dialogue -%}
  - {{ line.speaker }}: {{ line.text }}
{% endfor -%}
{% endif -%}
{% if event.planning_signals -%}
- Turn responder planning signals:
{% for signal in event.planning_signals -%}
  - {{ signal }}
{% endfor -%}
{% endif -%}
{% if event.characters -%}
- NPCs manifested or changed:
{% for character in event.characters -%}
  - {{ character.name }}: {{ character.description or character.personality or 'Active' }}
{% endfor -%}
{% endif -%}
{% if event.die_rolls -%}
- Resolved dice: {{ event.die_rolls }}
{% endif -%}
{% endfor -%}
{% else -%}
No new responder events are queued. Use this heartbeat for one conservative refinement of unresolved threads and causal world consequences. Do not advance in-world time or claim that a planned event occurred.
{% endif -%}

# Lore Tools
Use `deep_search_lore` and `deep_read_lore` only when the current queue batch exposes a concrete lore gap. This heartbeat has a small independent tool budget. Prefer established lore over invention. Do not roll dice; you are planning, not resolving an uncertain action.

# Output Requirements
- `sticky_notes`: {% if sticky_schema_json %}the complete typed JSON object conforming exactly to the sticky contract{% else %}the complete set, at most {{ max_sticky_notes }} notes, including all required topics{% endif %}.
"""
)


class StickyNoteItem(BaseModel):
    topic: str = Field(description="Unique topic or concept for this sticky note (e.g. 'inventory', 'quest', 'location', 'threat').")
    info: str = Field(description="Clear, concise, up-to-date summary of the topic state.")


class DeepPlanUpdate(BaseModel):
    sticky_notes: List[StickyNoteItem] = Field(default_factory=list)


def _safe_model_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", value).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"Field_{cleaned}"
    return cleaned


def build_deep_plan_update_model(
    definitions: Dict[str, Any],
) -> type[BaseModel]:
    if not definitions:
        return DeepPlanUpdate

    sticky_fields: Dict[str, Any] = {}
    for topic, definition in definitions.items():
        field_name = _safe_model_name(topic)
        topic_desc = definition.get("description") or topic
        fields = definition.get("fields")
        if fields:
            # Multi-field structured sticky: all subfields are strictly str
            sub_model_fields: Dict[str, Any] = {}
            for sub_key, sub_desc in fields.items():
                safe_sub_name = _safe_model_name(sub_key)
                sub_model_fields[safe_sub_name] = (
                    str,
                    Field(default=..., description=str(sub_desc or sub_key), alias=sub_key),
                )
            submodel = create_model(f"Sticky_{field_name}", **sub_model_fields)
            annotation = submodel
        else:
            # Single-string sticky note: strictly str
            annotation = str

        field_kwargs: Dict[str, Any] = {
            "alias": topic,
            "description": topic_desc,
        }
        sticky_fields[field_name] = (
            annotation,
            Field(default=..., **field_kwargs),
        )

    StructuredStickyNotes = create_model(
        "StructuredStickyNotes",
        **sticky_fields,
    )
    return create_model(
        "StructuredDeepPlanUpdate",
        sticky_notes=(StructuredStickyNotes, Field(default=...)),
    )


def parse_planning_schema(
    schema: Dict[str, Any],
) -> OrderedDict[str, Dict[str, Any]]:
    if not isinstance(schema, dict) or not schema:
        return OrderedDict()

    # Unwrap top-level "stickies" key if present
    raw_definitions = (
        schema.get("stickies")
        if "stickies" in schema and isinstance(schema.get("stickies"), dict)
        else schema
    )

    parsed: OrderedDict[str, Dict[str, Any]] = OrderedDict()
    for raw_topic, raw_entry in raw_definitions.items():
        topic = str(raw_topic).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
        if not topic:
            continue
        if not isinstance(raw_entry, dict):
            raw_entry = {"description": str(raw_entry)}

        description = str(raw_entry.get("description", "")).strip()
        required = bool(raw_entry.get("required", True))
        render = str(
            raw_entry.get(
                "render",
                raw_entry.get("display_template", raw_entry.get("template", "")),
            )
        ).strip()

        raw_fields = raw_entry.get("fields")
        fields: Optional[Dict[str, str]] = None
        if isinstance(raw_fields, dict) and raw_fields:
            fields = {
                str(k).strip(): str(v).strip()
                for k, v in raw_fields.items()
                if str(k).strip()
            }
        elif isinstance(raw_fields, (list, tuple, set)):
            fields = {
                str(k).strip(): str(k).strip() for k in raw_fields if str(k).strip()
            }

        initial = raw_entry.get("initial")
        if isinstance(initial, dict):
            initial = {str(k): str(v) for k, v in initial.items()}
        elif initial is not None:
            initial = str(initial)
        elif fields:
            initial = {k: "" for k in fields}
        else:
            initial = ""

        parsed[topic] = {
            "topic": topic,
            "description": description,
            "required": required,
            "render": render,
            "fields": fields,
            "initial": initial,
        }
    return parsed


# Backward-compatibility alias
parse_sticky_definitions = parse_planning_schema


def render_structured_sticky(
    definition: Dict[str, Any], value: Any
) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:MAX_STICKY_NOTE_INFO_CHARS]

    template = definition.get("render") or definition.get("display_template")
    if isinstance(value, dict):
        if template:
            try:
                if "{" in template:
                    try:
                        rendered = template.format(**value)
                    except (KeyError, IndexError, ValueError):
                        rendered = Template(template).render(**value)
                else:
                    rendered = Template(template).render(**value)
                return rendered.strip()[:MAX_STICKY_NOTE_INFO_CHARS]
            except Exception:
                logger.warning(
                    "[StoryPlanningModule] Failed to render template for '%s'",
                    definition.get("topic"),
                )
        rendered = " | ".join(
            f"{k}: {v}" for k, v in value.items() if v is not None and str(v) != ""
        )
        return rendered.strip()[:MAX_STICKY_NOTE_INFO_CHARS]

    return str(value).strip()[:MAX_STICKY_NOTE_INFO_CHARS]


class VertexGemini(Gemini):
    project_id: Optional[str] = None
    location: Optional[str] = None
    _client_cache: dict = PrivateAttr(default_factory=dict)

    @property
    def api_client(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop not in self._client_cache:
            kwargs = {}
            if self.project_id:
                kwargs["project"] = self.project_id
            if self.location:
                kwargs["location"] = self.location
            if kwargs:
                kwargs["vertexai"] = True
            self._client_cache[loop] = genai.Client(**kwargs)
        return self._client_cache[loop]


class StoryPlanningModule:
    """Sole owner of sticky notes and background deep planning."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        text_response_provider: TextResponseProvider,
        lore_library: LoreLibrary,
        character_manager: CharacterManager,
        session_service: InMemorySessionService,
        session_id: str,
        recent_story_log_fn: Optional[Callable[[], str]] = None,
        on_save_state: Optional[Callable[[], None]] = None,
    ):
        if theater is None:
            raise ValueError("theater is required.")
        if canvas_manager is None:
            raise ValueError("canvas_manager is required.")
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")
        if lore_library is None:
            raise ValueError("lore_library is required.")
        if character_manager is None:
            raise ValueError("character_manager is required.")
        if session_service is None:
            raise ValueError("session_service is required.")
        if not session_id:
            raise ValueError("session_id is required.")

        self.theater = theater
        self.theater_id = getattr(theater, "theater_id", "")
        self.canvas_manager = canvas_manager
        self.text_response_provider = text_response_provider
        self.lore_library = lore_library
        self.character_manager = character_manager
        self._deep_read_lore_calls_this_run = 0
        self._deep_search_lore_calls_this_run = 0
        self._deep_lore_calls_lock = Lock()
        self.session_service = session_service
        self.session_id = str(session_id)
        self.recent_story_log_fn = recent_story_log_fn
        self.on_save_state = on_save_state

        raw_config = theater.config() or {}
        subconfig = raw_config.get("story_planning", raw_config) if "story_planning" in raw_config else raw_config
        self.config: Dict[str, Any] = subconfig if isinstance(subconfig, dict) else {}

        # Load planning schema: check theater first, then config
        schema_data = None
        if self.theater and hasattr(self.theater, "read_planning_schema"):
            schema_data = self.theater.read_planning_schema()
        if not schema_data and self.theater and hasattr(self.theater, "directory"):
            theater_dir = self.theater.directory()
            for name in ("planning.yaml", "planning.yml"):
                p = theater_dir / name
                if p.is_file():
                    try:
                        import yaml

                        with open(p, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                            if isinstance(data, dict):
                                schema_data = data
                                break
                    except Exception as e:
                        logger.warning("Failed to load planning schema from %s: %s", p, e)
        if not schema_data:
            schema_data = self.config.get(
                "planning_schema",
                self.config.get("sticky_definitions", self.config.get("stickies")),
            )

        self._sticky_definitions = (
            parse_planning_schema(schema_data)
            if isinstance(schema_data, dict) and schema_data
            else OrderedDict()
        )
        self._deep_plan_update_model = build_deep_plan_update_model(self._sticky_definitions)
        self._sticky_notes: OrderedDict[str, str] = OrderedDict()
        self._structured_sticky_notes: OrderedDict[str, Any] = OrderedDict()
        self._required_stickies: OrderedDict[str, str] = OrderedDict()
        self._sticky_notes_lock = Lock()

        raw_hidden = self.config.get("hidden_stickies", [])
        self.hidden_stickies: set[str] = (
            {str(s).strip() for s in raw_hidden if str(s).strip()}
            if isinstance(raw_hidden, (list, tuple, set))
            else set()
        )

        if self._sticky_definitions:
            for topic, definition in self._sticky_definitions.items():
                if definition.get("required"):
                    self._required_stickies[topic] = ""
        else:
            req_keys = self.config.get(
                "required_stickies",
                self.config.get("required_sticky_notes", self.config.get("required_elements", [])),
            )
            if isinstance(req_keys, (list, tuple, set)):
                for k in req_keys:
                    if isinstance(k, dict):
                        topic = str(k.get("topic", k.get("name", ""))).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                        info = str(k.get("info", k.get("content", ""))).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
                    else:
                        topic = str(k).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                        info = ""
                    if topic:
                        self._required_stickies[topic] = info
            elif isinstance(req_keys, dict):
                for k, v in req_keys.items():
                    topic = str(k).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                    info = str(v).strip()[:MAX_STICKY_NOTE_INFO_CHARS]
                    if topic:
                        self._required_stickies[topic] = info
            elif isinstance(req_keys, str):
                for k in req_keys.split(","):
                    topic = str(k).strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                    if topic:
                        self._required_stickies[topic] = ""

        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        configured_style = self.config.get("style", DEFAULT_STORY_PLANNING_STYLE)
        self.style: str = str(configured_style).strip() or DEFAULT_STORY_PLANNING_STYLE

        self.max_sticky_notes: int = max(
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
        self.max_named_elements: int = self.max_sticky_notes

        # Deep plan state
        self._deep_plan: Dict[str, Any] = {}
        self._deep_plan_revision: int = 0
        self._deep_plan_through_turn_id: int = 0
        self._deep_plan_lock = Lock()
        self._deep_planning_queue: deque[Tuple[int, str, Dict[str, Any]]] = deque()
        self._deep_planning_queue_lock = Lock()
        self._deep_planning_worker: Optional[threading.Thread] = None

        # Configuration for deep planner
        raw_deep_config = self.config.get("deep_planning", {})
        self.deep_planning_config: Dict[str, Any] = (
            raw_deep_config if isinstance(raw_deep_config, dict) else {}
        )
        self.deep_planning_enabled: bool = bool(
            self.deep_planning_config.get("enabled", self.adventure_mode)
        )
        self.planner_model: str = str(self.config.get("planner_model") or "gemini-3.7-flash")
        self.deep_planner_model: str = str(
            self.deep_planning_config.get("model")
            or self.config.get("deep_planner_model")
            or self.planner_model
        )
        self.deep_planner_timeout_seconds: float = max(
            1.0,
            float(self.deep_planning_config.get("timeout_seconds", DEFAULT_DEEP_PLANNER_TIMEOUT_SECONDS)),
        )
        self.deep_heartbeat_seconds: float = max(
            0.05,
            float(self.deep_planning_config.get("heartbeat_seconds", DEFAULT_DEEP_HEARTBEAT_SECONDS)),
        )
        self.deep_max_events_per_turn: int = max(
            1,
            int(self.deep_planning_config.get("max_events_per_turn", DEFAULT_DEEP_MAX_EVENTS_PER_TURN)),
        )
        self.deep_idle_refinement_turns: int = max(
            0,
            int(self.deep_planning_config.get("idle_refinement_turns", DEFAULT_DEEP_IDLE_REFINEMENT_TURNS)),
        )
        self.deep_heartbeat_failure_retries: int = max(
            0,
            int(self.deep_planning_config.get("failure_retries", DEFAULT_DEEP_HEARTBEAT_FAILURE_RETRIES)),
        )
        raw_deep_budget = self.deep_planning_config.get("thinking_budget", DEFAULT_DEEP_THINKING_BUDGET)
        self.deep_thinking_budget: Optional[int] = (
            int(raw_deep_budget) if raw_deep_budget is not None else None
        )
        self.deep_max_output_tokens: int = max(
            1024,
            min(16384, int(self.deep_planning_config.get("max_output_tokens", DEFAULT_DEEP_MAX_OUTPUT_TOKENS))),
        )
        self.vertex_project: Optional[str] = (
            self.config.get("vertex_project")
            or self.config.get("gcloud", {}).get("project_id")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        self.vertex_location: str = str(
            self.config.get("vertex_location") or os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
        )
        self.compaction_config: Optional[EventsCompactionConfig] = self._build_compaction_config()
        self._run_compression_config: Optional[types.ContextWindowCompressionConfig] = (
            self._build_run_compression_config()
        )

        self._deep_planner_agent: Agent = self._create_deep_planner_agent()
        self._deep_planner_app: App = App(
            name="narratron_story_deep_planner",
            root_agent=self._deep_planner_agent,
            events_compaction_config=self.compaction_config,
        )
        self._deep_planner_runner: Runner = Runner(
            app=self._deep_planner_app,
            session_service=self.session_service,
            auto_create_session=True,
        )

        # Initial stickies
        if self._sticky_definitions:
            initial_structured = {
                topic: definition["initial"]
                for topic, definition in self._sticky_definitions.items()
                if definition.get("initial") is not None
            }
            validated_initial = self._deep_plan_update_model.model_validate(
                {"sticky_notes": initial_structured}
            ).model_dump(mode="json", by_alias=True)["sticky_notes"]
            for topic, value in validated_initial.items():
                self._structured_sticky_notes[topic] = value
                info = render_structured_sticky(self._sticky_definitions[topic], value)
                self._sticky_notes[topic] = info
                if topic in self._required_stickies:
                    self._required_stickies[topic] = info
        else:
            initial_notes = self.config.get("initial_sticky_notes", self.config.get("initial_elements", {}))
            if isinstance(initial_notes, dict):
                for k, v in initial_notes.items():
                    topic = str(k)[:MAX_STICKY_NOTE_TOPIC_CHARS]
                    info = str(v)[:MAX_STICKY_NOTE_INFO_CHARS]
                    if topic:
                        self._sticky_notes[topic] = info
                        if topic in self._required_stickies and not self._required_stickies[topic]:
                            self._required_stickies[topic] = info
            elif isinstance(initial_notes, list):
                for elem in initial_notes:
                    if isinstance(elem, dict):
                        topic = str(elem.get("topic", elem.get("name", "")))[:MAX_STICKY_NOTE_TOPIC_CHARS]
                        info = str(elem.get("info", elem.get("content", "")))[:MAX_STICKY_NOTE_INFO_CHARS]
                        if topic:
                            self._sticky_notes[topic] = info
                            if topic in self._required_stickies and not self._required_stickies[topic]:
                                self._required_stickies[topic] = info

        for req_topic, req_info in self._required_stickies.items():
            if req_topic not in self._sticky_notes:
                self._sticky_notes[req_topic] = req_info

        while len(self._sticky_notes) > self.max_sticky_notes:
            evict_key = next((k for k in self._sticky_notes if k not in self._required_stickies), None)
            if evict_key is not None:
                del self._sticky_notes[evict_key]
            else:
                break

    def _build_compaction_config(self) -> Optional[EventsCompactionConfig]:
        compaction = self.config.get("compaction")
        if compaction is False:
            return None
        if compaction is None:
            compaction = {}
        compaction_interval = int(compaction.get("compaction_interval", compaction.get("interval", 3)))
        overlap_size = int(compaction.get("overlap_size", compaction.get("overlap", 1)))
        token_threshold = compaction.get("token_threshold", compaction.get("trigger_tokens", DEFAULT_COMPACTION_TRIGGER_TOKENS))
        event_retention_size = compaction.get("event_retention_size", 6)
        if token_threshold is not None and event_retention_size is None:
            event_retention_size = max(1, overlap_size * 2)
        elif event_retention_size is not None and token_threshold is None:
            token_threshold = DEFAULT_COMPACTION_TRIGGER_TOKENS

        return EventsCompactionConfig(
            compaction_interval=compaction_interval,
            overlap_size=overlap_size,
            token_threshold=int(token_threshold) if token_threshold is not None else None,
            event_retention_size=int(event_retention_size) if event_retention_size is not None else None,
        )

    def _build_run_compression_config(self) -> Optional[types.ContextWindowCompressionConfig]:
        compaction = self.config.get("compaction")
        if compaction is False:
            return None
        if compaction is None:
            compaction = {}
        trigger = compaction.get("trigger_tokens", compaction.get("token_threshold", DEFAULT_COMPACTION_TRIGGER_TOKENS))
        target = compaction.get("target_tokens", DEFAULT_COMPACTION_TARGET_TOKENS)
        return types.ContextWindowCompressionConfig(
            trigger_tokens=int(trigger) if trigger is not None else None,
            sliding_window=types.SlidingWindow(target_tokens=int(target)) if target is not None else None,
        )

    def deep_read_lore(self, document: str = "") -> str:
        with self._deep_lore_calls_lock:
            if self._deep_read_lore_calls_this_run >= MAX_DEEP_READ_LORE_CALLS_PER_RUN:
                return (
                    f"Error: Maximum deep_read_lore limit "
                    f"({MAX_DEEP_READ_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize the deep plan now."
                )
            self._deep_read_lore_calls_this_run += 1
        return self.lore_library.read_lore(document)

    def deep_search_lore(self, query: str) -> str:
        with self._deep_lore_calls_lock:
            if self._deep_search_lore_calls_this_run >= MAX_DEEP_SEARCH_LORE_CALLS_PER_RUN:
                return (
                    f"Error: Maximum deep_search_lore limit "
                    f"({MAX_DEEP_SEARCH_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize the deep plan now."
                )
            self._deep_search_lore_calls_this_run += 1
        return self.lore_library.search_lore(query)

    def reset_deep_lore_call_counts(self) -> None:
        with self._deep_lore_calls_lock:
            self._deep_read_lore_calls_this_run = 0
            self._deep_search_lore_calls_this_run = 0

    def _lookup_character(self, query: str = "") -> str:
        return self.character_manager.lookup_character(query)

    def _build_deep_planner_instruction(self, ctx: Any = None) -> str:
        lore_context = self.lore_library.get_lore_context()
        recent_story_log = self.recent_story_log_fn() if self.recent_story_log_fn else ""
        return (
            "You maintain the long-horizon plan for one interactive adventure. "
            "Return only the requested structured deep-plan update.\n\n"
            f"Story-planning style: {self.style}\n\n"
            "Available theater lore (top-level documents and directories):\n"
            f"{lore_context or '(No theater lore is available.)'}\n\n"
            f"Recent committed story log (up to {STORY_LOG_CONTEXT_LINES} entries):\n"
            f"{recent_story_log or '(No committed history yet.)'}"
        )

    def _create_deep_planner_agent(self) -> Agent:
        generate_content_config = None
        if self.deep_thinking_budget is not None:
            generate_content_config = types.GenerateContentConfig(
                max_output_tokens=self.deep_max_output_tokens,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=self.deep_thinking_budget
                ),
            )
        return Agent(
            name="story_deep_planner",
            description="Long-horizon story planner and sole Adventure Mode sticky-note owner.",
            model=VertexGemini(
                model=self.deep_planner_model,
                project_id=self.vertex_project,
                location=self.vertex_location,
            ),
            instruction=self._build_deep_planner_instruction,
            tools=[
                self.deep_search_lore,
                self.deep_read_lore,
                self._lookup_character,
            ],
            output_schema=self._deep_plan_update_model,
            output_key="deep_plan_update",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            generate_content_config=generate_content_config,
        )

    def restart_deep_planner_agent(self) -> None:
        logger.info(
            "[StoryPlanningModule] Resetting deep planner agent for theater=%s",
            self.theater_id,
        )
        self._deep_planner_agent = self._create_deep_planner_agent()
        self._deep_planner_app = App(
            name="narratron_story_deep_planner",
            root_agent=self._deep_planner_agent,
            events_compaction_config=self.compaction_config,
        )
        self._deep_planner_runner = Runner(
            app=self._deep_planner_app,
            session_service=self.session_service,
            auto_create_session=True,
        )

    def _get_or_create_deep_planner_runner(self) -> Runner:
        if self._deep_planner_runner is None:
            self.restart_deep_planner_agent()
        return self._deep_planner_runner

    def update_sticky_note(self, topic: str, info: str) -> str:
        """Insert or replace one sticky note in the current scene."""
        clean_topic = str(topic or "").strip()
        clean_info = str(info or "").strip()
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
                existing_info = self._sticky_notes[clean_topic]
                if clean_info.count("|") != existing_info.count("|"):
                    return (
                        f"Error: Sticky note divider count mismatch for '{clean_topic}'. "
                        f"Expected valid update for '{existing_info}'."
                    )
                self._sticky_notes[clean_topic] = clean_info
                self._sticky_notes.move_to_end(clean_topic)
            else:
                if len(self._sticky_notes) >= self.max_sticky_notes:
                    evict_key = next((k for k in self._sticky_notes if k not in self._required_stickies), None)
                    if evict_key is not None:
                        del self._sticky_notes[evict_key]
                        dropped_topic = evict_key
                self._sticky_notes[clean_topic] = clean_info
            current_count = len(self._sticky_notes)

        if self.on_save_state:
            self.on_save_state()

        action = "Updated" if is_update else "Added"
        logger.debug(
            "[StoryPlanningModule] %s sticky note '%s' (theater=%s). Active sticky notes count: %d",
            action,
            clean_topic,
            self.theater_id or "default",
            current_count,
        )

        warning = ""
        if dropped_topic:
            warning = f" Warning: Maximum limit of {self.max_sticky_notes} sticky notes reached. Oldest sticky note '{dropped_topic}' was dropped to make room."
        elif current_count >= self.max_sticky_notes and not is_update:
            warning = f" Note: Sticky note limit of {self.max_sticky_notes} reached. Adding another new note will drop the oldest non-required one."

        return f"{action} sticky note '{clean_topic}'.{warning}"

    def update_or_insert_named_element(self, name: str, content: str) -> str:
        return self.update_sticky_note(topic=name, info=content)

    def get_present_sticky_notes(self, include_hidden: bool = True) -> list[dict[str, str]]:
        with self._sticky_notes_lock:
            items = [
                (t, i) for t, i in self._sticky_notes.items()
                if include_hidden or t not in self.hidden_stickies
            ]
            return [
                {
                    "topic": topic[:MAX_STICKY_NOTE_TOPIC_CHARS],
                    "info": info[:MAX_STICKY_NOTE_INFO_CHARS],
                    "name": topic[:MAX_STICKY_NOTE_TOPIC_CHARS],
                    "content": info[:MAX_STICKY_NOTE_INFO_CHARS],
                }
                for topic, info in items[-self.max_sticky_notes:]
            ]

    def get_present_elements(self, include_hidden: bool = True) -> list[dict[str, str]]:
        return self.get_present_sticky_notes(include_hidden=include_hidden)

    def get_present_structured_sticky_notes(self) -> Dict[str, Any]:
        with self._sticky_notes_lock:
            return {
                topic: dict(value) if isinstance(value, dict) else value
                for topic, value in self._structured_sticky_notes.items()
            }

    def get_required_sticky_notes(self) -> list[str]:
        return list(self._required_stickies.keys())

    def get_deep_plan(self) -> Dict[str, Any]:
        with self._deep_plan_lock:
            return dict(self._deep_plan)

    def wait_for_deep_planning(
        self, timeout: float = 5.0, target_turn_id: Optional[int] = None
    ) -> bool:
        if not self.deep_planning_enabled:
            return True
        if target_turn_id is None:
            with self._deep_plan_lock:
                target_turn_id = self._deep_plan_through_turn_id
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._deep_plan_lock:
                if self._deep_plan_through_turn_id >= target_turn_id:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _commit_deep_plan_update(
        self, turn_id: int, raw_update: Dict[str, Any]
    ) -> bool:
        if not isinstance(raw_update, dict) or raw_update.get("error"):
            return False
        update = self._deep_plan_update_model.model_validate(raw_update)

        raw_notes = update.sticky_notes
        if self._sticky_definitions:
            structured_notes = update.model_dump(mode="json", by_alias=True)["sticky_notes"]
            with self._sticky_notes_lock:
                self._structured_sticky_notes = OrderedDict(structured_notes)
                self._sticky_notes = OrderedDict(
                    (
                        topic,
                        render_structured_sticky(self._sticky_definitions[topic], value),
                    )
                    for topic, value in self._structured_sticky_notes.items()
                )
        elif raw_notes:
            with self._sticky_notes_lock:
                existing_snapshot = dict(self._sticky_notes)
                new_notes: OrderedDict[str, str] = OrderedDict()
                for item in raw_notes:
                    if isinstance(item, dict):
                        raw_topic = item.get("topic", item.get("name", ""))
                        raw_info = item.get("info", item.get("content", ""))
                    else:
                        raw_topic = getattr(item, "topic", "")
                        raw_info = getattr(item, "info", "")
                    topic = str(raw_topic or "").strip()[:MAX_STICKY_NOTE_TOPIC_CHARS]
                    info = str(raw_info or "").strip()[:MAX_STICKY_NOTE_INFO_CHARS]
                    if not topic or not info:
                        continue
                    new_notes[topic] = info

                for req_key, default_info in self._required_stickies.items():
                    if req_key not in new_notes:
                        new_notes[req_key] = existing_snapshot.get(req_key, default_info)

                if len(new_notes) > self.max_sticky_notes:
                    excess = len(new_notes) - self.max_sticky_notes
                    non_required = [
                        key for key in new_notes if key not in self._required_stickies
                    ]
                    keys_to_drop = set(non_required[:excess])
                    new_notes = OrderedDict(
                        (key, value)
                        for key, value in new_notes.items()
                        if key not in keys_to_drop
                    )
                self._sticky_notes = new_notes

        with self._deep_plan_lock:
            self._deep_plan_revision += 1
            self._deep_plan_through_turn_id = max(
                self._deep_plan_through_turn_id, turn_id
            )
            plan = update.model_dump(mode="json", by_alias=True)
            plan["revision"] = self._deep_plan_revision
            plan["through_turn_id"] = self._deep_plan_through_turn_id
            if not self._sticky_definitions:
                plan["sticky_notes"] = [
                    {"topic": note["topic"], "info": note["info"]}
                    for note in self.get_present_sticky_notes()
                ]
            self._deep_plan = plan

        if self.on_save_state:
            self.on_save_state()
        logger.info(
            "[StoryPlanningModule] Deep plan revision=%d committed through turn=%d",
            self._deep_plan_revision,
            turn_id,
        )
        return True

    def queue_deep_planning(
        self, turn_id: int, action: str, result: Dict[str, Any]
    ) -> threading.Thread:
        if not self.deep_planning_enabled:
            thread = threading.Thread(target=lambda: None, daemon=True)
            thread.start()
            return thread

        with self._deep_planning_queue_lock:
            self._deep_planning_queue.append((turn_id, action, dict(result)))
            if self._deep_planning_worker and self._deep_planning_worker.is_alive():
                return self._deep_planning_worker

            def _worker() -> None:
                idle_refinements_remaining = self.deep_idle_refinement_turns
                consecutive_failures = 0
                while True:
                    time.sleep(self.deep_heartbeat_seconds)
                    with self._deep_planning_queue_lock:
                        batch = list(self._deep_planning_queue)[: self.deep_max_events_per_turn]

                    if batch:
                        update = self._run_deep_planner_agent(batch)
                        highest_turn_id = batch[-1][0]
                        try:
                            committed = self._commit_deep_plan_update(highest_turn_id, update)
                        except Exception:
                            committed = False
                            logger.exception(
                                "[StoryPlanningModule] Failed to commit deep heartbeat through turn=%d",
                                highest_turn_id,
                            )
                        if not committed:
                            consecutive_failures += 1
                            if consecutive_failures <= self.deep_heartbeat_failure_retries:
                                continue
                            with self._deep_planning_queue_lock:
                                self._deep_planning_worker = None
                            return
                        with self._deep_planning_queue_lock:
                            for expected in batch:
                                if (
                                    self._deep_planning_queue
                                    and self._deep_planning_queue[0][0] == expected[0]
                                ):
                                    self._deep_planning_queue.popleft()
                        idle_refinements_remaining = self.deep_idle_refinement_turns
                        consecutive_failures = 0
                        continue

                    if idle_refinements_remaining > 0:
                        with self._deep_plan_lock:
                            through_turn_id = self._deep_plan_through_turn_id
                        update = self._run_deep_planner_agent([])
                        try:
                            self._commit_deep_plan_update(through_turn_id, update)
                        except Exception:
                            logger.exception(
                                "[StoryPlanningModule] Failed to commit idle deep-plan refinement"
                            )
                        idle_refinements_remaining -= 1
                        continue

                    with self._deep_planning_queue_lock:
                        if not self._deep_planning_queue:
                            self._deep_planning_worker = None
                            return

            thread = threading.Thread(
                target=_worker,
                daemon=True,
                name=f"deep-planner-{self.theater_id or 'default'}",
            )
            self._deep_planning_worker = thread
            thread.start()
            return thread

    def _run_deep_planner_agent(
        self,
        turn_events: List[Tuple[int, str, Dict[str, Any]]],
    ) -> Dict[str, Any]:
        self.reset_deep_lore_call_counts()
        runner = self._get_or_create_deep_planner_runner()
        with self._deep_plan_lock:
            deep_plan_json = json.dumps(self._deep_plan, ensure_ascii=False, indent=2)
            current_through_turn = self._deep_plan_through_turn_id
        event_payloads = [
            {
                "turn_id": turn_id,
                "user_action": user_action,
                "narration": str(scene_reaction.get("narration") or "").strip(),
                "dialogue": scene_reaction.get("dialogue") or [],
                "planning_signals": scene_reaction.get("planning_signals") or [],
                "characters": scene_reaction.get("manifested_characters") or [],
                "die_rolls": scene_reaction.get("die_rolls") or [],
            }
            for turn_id, user_action, scene_reaction in turn_events
        ]
        heartbeat_through_turn = (
            turn_events[-1][0] if turn_events else current_through_turn
        )
        prompt_input = _DEEP_PLANNING_PROMPT_TEMPLATE.render(
            deep_plan_json=deep_plan_json if deep_plan_json != "{}" else "",
            current_notes=self.get_present_sticky_notes(),
            structured_sticky_json=json.dumps(
                self.get_present_structured_sticky_notes(),
                ensure_ascii=False,
                indent=2,
            ),
            sticky_schema_json=json.dumps(
                {
                    topic: {
                        k: v
                        for k, v in definition.items()
                        if k in ("description", "fields", "render", "required") and v is not None
                    }
                    for topic, definition in self._sticky_definitions.items()
                },
                ensure_ascii=False,
                indent=2,
            )
            if self._sticky_definitions
            else "",
            required_stickies=self.get_required_sticky_notes(),
            turn_events=event_payloads,
            max_sticky_notes=self.max_sticky_notes,
        ).strip()

        async def run_turn() -> Dict[str, Any]:
            run_config = (
                RunConfig(context_window_compression=self._run_compression_config)
                if self._run_compression_config
                else None
            )
            session = await self.session_service.get_session(
                app_name="narratron_story_deep_planner",
                user_id="story_deep_planner",
                session_id=self.session_id,
            )
            if session and session.state:
                session.state.pop("deep_plan_update", None)

            final_text = ""
            async for event in runner.run_async(
                user_id="story_deep_planner",
                session_id=self.session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt_input)]),
                run_config=run_config,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = "".join(part.text or "" for part in event.content.parts)

            session = await self.session_service.get_session(
                app_name="narratron_story_deep_planner",
                user_id="story_deep_planner",
                session_id=self.session_id,
            )
            stored_update = (session.state or {}).get("deep_plan_update") if session else None
            if isinstance(stored_update, BaseModel):
                raw_update = stored_update.model_dump()
            elif isinstance(stored_update, dict):
                raw_update = stored_update
            elif isinstance(stored_update, str):
                raw_update = json.loads(stored_update)
            else:
                raw_update = {}
            if not raw_update:
                raise ValueError(
                    "Deep planner returned no fresh plan update "
                    f"(final_response_chars={len(final_text)})."
                )
            return self._deep_plan_update_model.model_validate(raw_update).model_dump(
                mode="json", by_alias=True
            )

        try:
            return asyncio.run(
                asyncio.wait_for(run_turn(), timeout=self.deep_planner_timeout_seconds)
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.error(
                "[StoryPlanningModule] Deep planner timed out after %.1f seconds at turn=%d",
                self.deep_planner_timeout_seconds,
                heartbeat_through_turn,
            )
            self.restart_deep_planner_agent()
            return {"error": "Deep planner timed out; the previous plan was preserved."}
        except Exception as exc:
            logger.exception(
                "[StoryPlanningModule] Deep planner heartbeat failed through turn=%d",
                heartbeat_through_turn,
            )
            return {"error": f"Deep planner failed: {exc}"}

    def export_planning_state(self) -> Dict[str, Any]:
        with self._sticky_notes_lock:
            canvas_notes = [
                {"topic": topic, "info": info, "name": topic, "content": info}
                for topic, info in self._sticky_notes.items()
                if topic not in self.hidden_stickies
            ]
            all_notes = [
                {"topic": topic, "info": info, "name": topic, "content": info}
                for topic, info in self._sticky_notes.items()
            ]
        with self._deep_plan_lock:
            deep_plan = dict(self._deep_plan)
            deep_plan_revision = self._deep_plan_revision
            deep_plan_through_turn_id = self._deep_plan_through_turn_id

        return {
            "sticky_notes": [{"topic": n["topic"], "info": n["info"]} for n in canvas_notes],
            "all_sticky_notes": [{"topic": n["topic"], "info": n["info"]} for n in all_notes],
            "structured_sticky_notes": {
                k: dict(v) if isinstance(v, dict) else v
                for k, v in self._structured_sticky_notes.items()
            },
            "named_elements": [{"name": n["name"], "content": n["content"]} for n in canvas_notes],
            "deep_plan": deep_plan,
            "deep_plan_revision": deep_plan_revision,
            "deep_plan_through_turn_id": deep_plan_through_turn_id,
        }

    def import_planning_state(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return

        with self._sticky_notes_lock:
            self._sticky_notes.clear()
            if self._sticky_definitions:
                structured = state.get("structured_sticky_notes")
                if not isinstance(structured, dict):
                    deep_state = state.get("deep_plan", {})
                    structured = (
                        deep_state.get("sticky_notes")
                        if isinstance(deep_state, dict)
                        else None
                    )
                if isinstance(structured, dict):
                    try:
                        structured = self._deep_plan_update_model.model_validate(
                            {"sticky_notes": structured}
                        ).model_dump(mode="json", by_alias=True)["sticky_notes"]
                        self._structured_sticky_notes = OrderedDict(structured)
                    except Exception as exc:
                        logger.warning(
                            "[StoryPlanningModule] Ignoring invalid persisted structured sticky state: %s",
                            exc,
                        )
                for topic, value in self._structured_sticky_notes.items():
                    self._sticky_notes[topic] = render_structured_sticky(
                        self._sticky_definitions[topic], value
                    )
            else:
                notes = state.get("all_sticky_notes", state.get("sticky_notes", state.get("named_elements", [])))
                if isinstance(notes, list):
                    for elem in notes:
                        if isinstance(elem, dict):
                            topic = str(elem.get("topic", elem.get("name", "")))[:MAX_STICKY_NOTE_TOPIC_CHARS]
                            info = str(elem.get("info", elem.get("content", "")))[:MAX_STICKY_NOTE_INFO_CHARS]
                            if topic and info:
                                self._sticky_notes[topic] = info
                elif isinstance(notes, dict):
                    for k, v in notes.items():
                        self._sticky_notes[str(k)[:MAX_STICKY_NOTE_TOPIC_CHARS]] = str(v)[:MAX_STICKY_NOTE_INFO_CHARS]

            for req_topic, req_info in self._required_stickies.items():
                if req_topic not in self._sticky_notes:
                    self._sticky_notes[req_topic] = req_info

        with self._deep_plan_lock:
            imported_plan = state.get("deep_plan", {})
            self._deep_plan = (
                {
                    key: value
                    for key, value in imported_plan.items()
                    if key != "plot_beats"
                }
                if isinstance(imported_plan, dict)
                else {}
            )
            try:
                self._deep_plan_revision = max(0, int(state.get("deep_plan_revision", 0)))
            except (TypeError, ValueError):
                self._deep_plan_revision = 0
            try:
                self._deep_plan_through_turn_id = max(
                    0,
                    int(
                        state.get(
                            "deep_plan_through_turn_id",
                            self._deep_plan.get("through_turn_id", 0),
                        )
                    ),
                )
            except (TypeError, ValueError):
                self._deep_plan_through_turn_id = 0
