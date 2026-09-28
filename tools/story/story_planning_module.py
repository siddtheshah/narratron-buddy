"""Story planning module: owns sticky notes and background deep planning."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
from pathlib import Path
import threading
from threading import Lock
import time
from typing import Callable, Optional

from jinja2 import Template
from google.adk.agents import Agent
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import RunConfig, Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from components.canvas.story_state import (
    CharacterState,
    PlayerCharacterState,
    StoryStateDiff,
    StoryStateSnapshot,
)
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from components.character_manager import CharacterLookupResult, CharacterManager, PlayerCharacter
from components.lore_library import LoreLibrary
from components.image_library import ImageLibrary
from components.notepad import (
    Notepad,
)
from tools.story.story_models import (
    DEFAULT_COMPACTION_TARGET_TOKENS,
    DEFAULT_COMPACTION_TRIGGER_TOKENS,
    DEFAULT_STORY_PLANNING_STYLE,
    VertexGemini,
)

logger = logging.getLogger(__name__)

DEFAULT_DEEP_THINKING_BUDGET = 2048
DEFAULT_DEEP_MAX_OUTPUT_TOKENS = 6144
DEFAULT_DEEP_PLANNER_TIMEOUT_SECONDS = 45.0
DEFAULT_DEEP_HEARTBEAT_SECONDS = 1.0
DEFAULT_DEEP_MAX_EVENTS_PER_TURN = 4
DEFAULT_DEEP_IDLE_REFINEMENT_TURNS = 0
DEFAULT_DEEP_HEARTBEAT_FAILURE_RETRIES = 2
STORY_LOG_CONTEXT_LINES = 200
MAX_DEEP_READ_LORE_CALLS_PER_RUN = 3
MAX_DEEP_SEARCH_LORE_CALLS_PER_RUN = 3
DEFAULT_DEEP_RECENT_IMAGES_LIMIT = 5


_DEEP_PLANNING_INSTRUCTION_TEMPLATE = Template(
"""# Role & Mission
You are the deep planner for an interactive adventure. You do not narrate the current scene and you never choose actions, speech, thoughts, or feelings for the player. Take a broad view of the player's accumulated actions, established lore, unresolved setups, off-screen actors, deadlines, and world consequences.

You are the sole owner of Adventure Mode sticky notes. Assimilate the committed turn by deciding which individual notes, if any, need an update. Use `update_sticky_note` once per changed sticky; do not replace or restate notes that did not change. Before updating a schema-backed sticky, call `check_schema` for that sticky and use its result to retry any rejected update.

# Non-Negotiable Planning Rules
- Committed events are immutable facts. Replan around them; never retcon them to protect an outline.
- Independent Analysis & Responder Communication: The turn responder directly communicates cues via 'Turn responder direct communication & planning signals' to improve recall (such as `[STICKY UPDATE: <Topic>]`). While you must promptly review all flagged stickies, DO NOT solely rely on responder signals. The responder operates quickly and focuses on immediate narration; it may overlook subtle stat changes, numeric increments, lore rules, or off-screen consequences. You are the sole authoritative owner of sticky notes and must perform your own independent analysis across the entire turn (action, narration, dialogue, dice results, and lore constraints). Update any sticky note that requires modification, even if the responder omitted or failed to flag it.
- Preserve player agency. Never prescribe player actions.
- Maintain causal continuity across many turns. Advance factions and antagonists off-screen when their knowledge, resources, motives, and elapsed time justify it.
- Audit earlier player behavior when lore calls for counter-plotting. Opposition should investigate evidence the player plausibly left, but must not gain impossible knowledge.
- Use the author-defined sticky fields as the only durable planning vocabulary. Do not invent a parallel generic summary, thread tracker, consequence list, clock, or faction model.
- Think across short, medium, and long horizons, but publish only the configured sticky state.
- If there are stickies that are for plot building or reveals, these are a priority for you to set up and maintain.
- For stickies that track numeric state, ensure you follow the appropriate rules defined in the lore to faithfully update them.
- For you, accuracy is more important than speed.

# Character, Lore, and Image Tools
You can canonically manage the player character using `update_player_character` (name, visual description, image reference) and query them with `get_player_character_info`. You can canonically create or update NPC records using `update_character` and look up all characters using `_lookup_character`.
Use `deep_search_lore` and `deep_read_lore` only when the current queue batch exposes a concrete lore gap. Use `find_image_names` when a sticky needs the name of an older mounted or generated image. Store the returned `alias` (preferred) or `name`, never a guessed filename or absolute path. This heartbeat has a small independent tool budget. Prefer established lore over invention. Do not roll dice; you are planning, not resolving an uncertain action.

# Completion
After tool calls, respond with a brief confirmation. Do not include a sticky-notes JSON payload in the final response.
{% if style -%}

# Story-Planning Style
{{ style }}
{% endif -%}
{% if sticky_schema_json -%}

# Sticky State Schema Reference
Each topic below is independently validated by the notepad tool. Do not attempt to publish a complete `sticky_notes` object. Call `check_schema(topic)` immediately before each schema-backed update; for field-backed notes, its `info` argument must be a JSON object encoded as a string.
{{ sticky_schema_json }}
{% endif -%}
{% if lore_context -%}

# Available Theater Lore
Available theater lore (top-level documents and directories):
{{ lore_context }}
{% endif -%}
"""
)


_DEEP_PLANNING_TURN_PROMPT_TEMPLATE = Template(
"""# Heartbeat Work
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
- Turn responder direct communication & planning signals:
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
No new responder events are queued. Use this heartbeat for one conservative refinement of unresolved threads and causal world consequences.
{% endif -%}

{% if is_initial is defined and not is_initial -%}
# State Diffs (Changes Since Previous Heartbeat)
{% if sticky_diffs -%}
## Sticky Notes Changes
{% for diff in sticky_diffs -%}
- {{ diff }}
{% endfor -%}
{% else -%}
## Sticky Notes Changes
(No sticky notes changed since last heartbeat)
{% endif -%}

{% if player_character_diff -%}
## Player Character Update
- {{ player_character_diff }}

{% endif -%}
{% if character_diffs -%}
## Character Updates
{% for diff in character_diffs -%}
- {{ diff }}
{% endfor -%}

{% endif -%}
{% if new_images -%}
## Newly Generated Images
The following images were generated since the last heartbeat:
{% for img in new_images -%}
- Alias: `{{ img.alias }}` | Name: `{{ img.name }}`{% if img.title %} | Title: "{{ img.title }}"{% endif %}{% if img.description and img.description != ('Image ' ~ img.name) and img.description != ('Image ' ~ img.name ~ '.jpg') and img.description != ('Image ' ~ img.name ~ '.png') and img.description != ('Image ' ~ img.name ~ '.webp') %} | Description: {{ img.description }}{% endif %}
{% endfor -%}

{% endif -%}
{% if deep_plan_diff -%}
## Deep Plan Update
- {{ deep_plan_diff }}
{% endif -%}
{% else -%}
# Current Adventure State
{% if current_notes -%}
# Current Sticky Notes
{% for note in current_notes -%}
- {{ note.topic }}: {{ note.info }}
{% endfor -%}
{% else -%}
# Current Sticky Notes
(No active sticky notes)
{% endif -%}

{% if player_character -%}
# Canonical Player Character
- Name: {{ player_character.name or 'Unnamed Explorer' }}{% if player_character.image_description %} | Visual: {{ player_character.image_description }}{% endif %}{% if player_character.reference %} | Image Reference: {{ player_character.reference }}{% endif %}

{% endif -%}
{% if characters -%}
# Canonical Active Characters
{% for char in characters -%}
- {{ char.name }}: Personality: {{ char.personality or 'N/A' }} | Motivation: {{ char.motivation or 'N/A' }} | Quirk: {{ char.quirk or 'N/A' }}{% if char.image_reference %} | Image Reference: {{ char.image_reference }}{% endif %}
{% endfor -%}

{% endif -%}
{% if recent_images -%}
# Recently Generated Images
The most recent generated images available to reference in sticky notes (newest first):
{% for img in recent_images -%}
- Alias: `{{ img.alias }}` | Name: `{{ img.name }}`{% if img.title %} | Title: "{{ img.title }}"{% endif %}{% if img.description and img.description != ('Image ' ~ img.name) and img.description != ('Image ' ~ img.name ~ '.jpg') and img.description != ('Image ' ~ img.name ~ '.png') and img.description != ('Image ' ~ img.name ~ '.webp') %} | Description: {{ img.description }}{% endif %}
{% endfor -%}

{% endif -%}
{% if deep_plan_json -%}
# Current Deep Plan
{{ deep_plan_json }}
{% endif -%}
{% endif -%}
"""
)

PlannerStateSnapshot = StoryStateSnapshot


class StoryPlanningModule:
    """Sole owner of background deep planning."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        lore_library: LoreLibrary,
        character_manager: CharacterManager,
        session_service: InMemorySessionService,
        session_id: str,
        notepad: Notepad,
        story_log_context_fn: Optional[Callable[[], str]] = None,
        image_library: Optional[ImageLibrary] = None,
    ) -> None:
        if theater is None:
            raise ValueError("theater is required.")
        if canvas_manager is None:
            raise ValueError("canvas_manager is required.")
        if lore_library is None:
            raise ValueError("lore_library is required.")
        if character_manager is None:
            raise ValueError("character_manager is required.")
        if session_service is None:
            raise ValueError("session_service is required.")
        if not session_id:
            raise ValueError("session_id is required.")
        if notepad is None:
            raise ValueError("notepad is required.")

        self.theater = theater
        self.theater_id: str = theater.theater_id or ""
        self.canvas_manager = canvas_manager
        self.lore_library = lore_library
        self.image_library = image_library or ImageLibrary(theater)
        self.character_manager = character_manager
        self._deep_read_lore_calls_this_run = 0
        self._deep_search_lore_calls_this_run = 0
        self._deep_lore_calls_lock = Lock()
        self.session_service = session_service
        self.session_id = str(session_id)
        self._story_log_context_fn = story_log_context_fn or (lambda: "")
        self.notepad = notepad
        self.config = notepad.config

        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        configured_style = self.config.get("style", DEFAULT_STORY_PLANNING_STYLE)
        self.style: str = str(configured_style).strip() or DEFAULT_STORY_PLANNING_STYLE

        # Deep plan state
        self._deep_plan: dict[str, object] = {}
        self._deep_plan_revision: int = 0
        self._deep_plan_through_turn_id: int = 0
        self._deep_plan_lock = Lock()
        self._deep_planning_queue: deque[tuple[int, str, dict[str, object]]] = deque()
        self._deep_planning_queue_lock = Lock()
        self._deep_planning_worker: Optional[threading.Thread] = None
        self._last_state_snapshot: Optional[PlannerStateSnapshot] = None

        # Configuration for deep planner
        raw_deep_config = self.config.get("deep_planning") or {}
        self.deep_planning_config: dict[str, object] = dict(raw_deep_config)
        self.deep_planning_enabled: bool = bool(
            self.deep_planning_config.get("enabled", self.adventure_mode)
        )
        self.planner_model: str = str(self.config.get("planner_model") or "gemini-3.7-flash")
        self.deep_planner_model: str = str(
            self.deep_planning_config.get("model")
            or self.config.get("deep_planner_model")
            or self.planner_model
        )
        self.deep_planner_timeout_seconds: float = DEFAULT_DEEP_PLANNER_TIMEOUT_SECONDS
        self.deep_heartbeat_seconds: float = DEFAULT_DEEP_HEARTBEAT_SECONDS
        self.deep_max_events_per_turn: int = max(
            1,
            int(self.deep_planning_config.get("max_events_per_turn", DEFAULT_DEEP_MAX_EVENTS_PER_TURN)),
        )
        self.deep_recent_images_limit: int = max(
            1,
            int(
                self.deep_planning_config.get(
                    "recent_images_limit", DEFAULT_DEEP_RECENT_IMAGES_LIMIT
                )
            ),
        )
        self.deep_idle_refinement_turns: int = DEFAULT_DEEP_IDLE_REFINEMENT_TURNS
        self.deep_heartbeat_failure_retries: int = DEFAULT_DEEP_HEARTBEAT_FAILURE_RETRIES
        raw_deep_budget = DEFAULT_DEEP_THINKING_BUDGET
        self.deep_thinking_budget: Optional[int] = (
            int(raw_deep_budget) if raw_deep_budget is not None else None
        )
        self.deep_max_output_tokens: int = DEFAULT_DEEP_MAX_OUTPUT_TOKENS
        raw_gcloud = self.config.get("gcloud") or {}
        gcloud_dict: dict[str, object] = dict(raw_gcloud)
        self.vertex_project: Optional[str] = (
            self.config.get("vertex_project")
            or gcloud_dict.get("project_id")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        self.vertex_location: str = str(
            self.config.get("vertex_location") or os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
        )
        self.compaction_config: Optional[EventsCompactionConfig] = self._build_compaction_config()
        self._run_compression_config: Optional[types.ContextWindowCompressionConfig] = (
            self._build_run_compression_config()
        )

        self.plugins: list[BasePlugin] = [ReflectAndRetryToolPlugin()]

        self._deep_planner_agent: Agent = self._create_deep_planner_agent()
        self._deep_planner_app: App = App(
            name="narratron_story_deep_planner",
            root_agent=self._deep_planner_agent,
            plugins=self.plugins,
            events_compaction_config=self.compaction_config,
        )
        self._deep_planner_runner: Runner = Runner(
            app=self._deep_planner_app,
            session_service=self.session_service,
            auto_create_session=True,
        )

    def _build_compaction_config(self) -> Optional[EventsCompactionConfig]:
        compaction = self.config.get("compaction")
        if compaction is False:
            return None
        raw_compaction = compaction or {}
        compaction_dict: dict[str, object] = dict(raw_compaction)
        compaction_interval = int(compaction_dict.get("compaction_interval", compaction_dict.get("interval", 3)))
        overlap_size = int(compaction_dict.get("overlap_size", compaction_dict.get("overlap", 1)))
        token_threshold = compaction_dict.get("token_threshold", compaction_dict.get("trigger_tokens", DEFAULT_COMPACTION_TRIGGER_TOKENS))
        event_retention_size = compaction_dict.get("event_retention_size", 6)
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
        raw_compaction = compaction or {}
        compaction_dict: dict[str, object] = dict(raw_compaction)
        trigger = compaction_dict.get("trigger_tokens", compaction_dict.get("token_threshold", DEFAULT_COMPACTION_TRIGGER_TOKENS))
        target = compaction_dict.get("target_tokens", DEFAULT_COMPACTION_TARGET_TOKENS)
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

    def _lookup_character(self, query: str = "") -> CharacterLookupResult:
        """List all session characters or search by name or trait."""
        return self.character_manager.lookup_character(query)

    def get_player_character(self) -> PlayerCharacter | None:
        """Return the canonical persisted identity and visual reference for the player character."""
        return self.character_manager.get_player_character()

    def get_player_character_info(self) -> str:
        """Retrieve the canonical player character identity and visual reference."""
        player = self.character_manager.get_player_character()
        if not player:
            return "No player character has been canonically established yet."
        return player.describe()

    def update_player_character(
        self,
        name: str = "",
        image_description: str = "",
        reference: str = "",
    ) -> str:
        """Canonically manage or update the player character's name, visual description, or image reference."""
        player = self.character_manager.update_player_character(
            name=name,
            image_description=image_description,
            reference=reference or None,
        )
        ref_info = f" Image reference: {player.reference}." if player.reference else ""
        return f"Canonically updated player character '{player.name}'. Visual: {player.image_description or 'N/A'}.{ref_info}"

    def update_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Optional[list[str]] = None,
        gender: Optional[str] = None,
        image_reference: str = "",
    ) -> str:
        """Canonically create or update an NPC record in the character manager."""
        char = self.character_manager.generate_character(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
            gender=gender,
            image_reference=image_reference,
        )
        if not char:
            return f"Failed to update character '{name}'."
        ref_info = f" Image reference: {char.image_reference}." if char.image_reference else ""
        return f"Canonically updated character '{char.name}' (alias: {char.alias}).{ref_info}"

    def find_image_names(self, query: str = "") -> list[dict[str, str]]:
        """Find available image aliases and names for use in sticky notes.

        Returns only catalog data; this never displays an image or changes the
        canvas. Prefer the returned alias when recording an image reference.
        """
        return self.image_library.find_image_names(query)

    def _get_recent_images(self) -> list[dict[str, object]]:
        """Fetch recently generated images safely from the image library."""
        images = self.image_library.get_recent_images(
            limit=self.deep_recent_images_limit, generated_only=True
        )
        return list(images) if images else []

    def _format_recent_images(self, recent_images: list[dict[str, object]]) -> str:
        if not recent_images:
            return ""
        lines: list[str] = []
        for img in recent_images:
            desc = str(img.get("description") or "")
            title = str(img.get("title") or "")
            name = str(img.get("name") or "")
            alias = str(img.get("alias") or "")
            parts = [f"Alias: `{alias}`", f"Name: `{name}`"]
            if title:
                parts.append(f'Title: "{title}"')
            path = str(img.get("path") or "")
            default_desc = f"Image {Path(path).name}" if path else ""
            if desc and desc != default_desc:
                parts.append(f"Description: {desc}")
            lines.append("- " + " | ".join(parts))
        return "\n".join(lines)

    def _build_deep_planner_instruction(self, ctx: object = None) -> str:
        lore_context = self.lore_library.get_lore_context()
        sticky_schema_json = (
            json.dumps(
                {
                    topic: {
                        k: v
                        for k, v in definition.items()
                        if k in ("description", "fields", "render", "required") and v is not None
                    }
                    for topic, definition in self.notepad.sticky_definitions.items()
                },
                ensure_ascii=False,
                indent=2,
            )
            if self.notepad.sticky_definitions
            else ""
        )
        return _DEEP_PLANNING_INSTRUCTION_TEMPLATE.render(
            style=self.style,
            sticky_schema_json=sticky_schema_json,
            lore_context=lore_context or "",
        ).strip()

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
                self.get_player_character_info,
                self.update_player_character,
                self.update_character,
                self.find_image_names,
                self.notepad.check_schema,
                self.notepad.update_sticky_note,
            ],
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            generate_content_config=generate_content_config,
        )

    def restart_deep_planner_agent(self) -> None:
        logger.info(
            "[StoryPlanningModule] Resetting deep planner agent for theater=%s",
            self.theater_id,
        )
        self._last_state_snapshot = None
        self._deep_planner_agent = self._create_deep_planner_agent()
        self._deep_planner_app = App(
            name="narratron_story_deep_planner",
            root_agent=self._deep_planner_agent,
            plugins=self.plugins,
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

    def get_deep_plan(self) -> dict[str, object]:
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

    def _record_state_snapshot(self) -> None:
        current_stickies = {
            note["topic"]: note["info"]
            for note in self.notepad.get_present_sticky_notes()
        }
        current_pc_obj = self.character_manager.get_player_character()
        current_pc: Optional[PlayerCharacterState] = None
        if current_pc_obj is not None:
            current_pc = PlayerCharacterState(
                name=current_pc_obj.name,
                image_description=current_pc_obj.image_description,
                reference=current_pc_obj.reference,
            )
        current_chars_list = self.character_manager.get_present_characters()
        current_chars: dict[str, CharacterState] = {
            c.name: CharacterState(
                name=c.name,
                personality=c.personality,
                motivation=c.motivation,
                quirk=c.quirk,
                image_reference=c.image_reference,
            )
            for c in current_chars_list
        }
        recent_images = self._get_recent_images()
        previous_known: set[str] = (
            self._last_state_snapshot.known_image_names
            if self._last_state_snapshot is not None
            else set()
        )
        all_known_images = previous_known | {
            str(img.get("name") or "") for img in recent_images if img.get("name")
        }
        self._last_state_snapshot = self.canvas_manager.story.create_snapshot(
            stickies=current_stickies,
            player_character=current_pc,
            characters=current_chars,
            known_image_names=all_known_images,
            deep_plan_revision=self._deep_plan_revision,
        )

    def _commit_deep_plan_update(
        self, turn_id: int, raw_update: dict[str, object]
    ) -> bool:
        if not raw_update or raw_update.get("error"):
            return False
        # Tool-driven planning has already applied each accepted update. Keep
        # the replacement path for importing older persisted plans and direct
        # callers during the transition.
        if not raw_update.get("tool_updates"):
            update = self.notepad.deep_plan_update_model.model_validate(raw_update)
            self.notepad.replace_from_deep_update(update)

        with self._deep_plan_lock:
            self._deep_plan_revision += 1
            self._deep_plan_through_turn_id = max(
                self._deep_plan_through_turn_id, turn_id
            )
            if raw_update.get("tool_updates"):
                plan: dict[str, object] = {
                    "sticky_notes": (
                        self.notepad.get_present_structured_sticky_notes()
                        if self.notepad.sticky_definitions
                        else [
                            {"topic": note["topic"], "info": note["info"]}
                            for note in self.notepad.get_present_sticky_notes()
                        ]
                    )
                }
            else:
                plan = update.model_dump(mode="json", by_alias=True)
            plan["revision"] = self._deep_plan_revision
            plan["through_turn_id"] = self._deep_plan_through_turn_id
            if not self.notepad.sticky_definitions:
                plan["sticky_notes"] = [
                    {"topic": note["topic"], "info": note["info"]}
                    for note in self.notepad.get_present_sticky_notes()
                ]
            self._deep_plan = plan

        self._record_state_snapshot()

        logger.info(
            "[StoryPlanningModule] Deep plan revision=%d committed through turn=%d",
            self._deep_plan_revision,
            turn_id,
        )
        return True

    def queue_deep_planning(
        self, turn_id: int, action: str, result: dict[str, object]
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

    def _compute_state_diffs(
        self,
        previous: StoryStateSnapshot,
        current_stickies: dict[str, str],
        current_pc: Optional[PlayerCharacterState],
        current_chars: dict[str, CharacterState],
        recent_images: list[dict[str, object]],
        current_deep_plan_revision: int,
        current_through_turn: int,
    ) -> StoryStateDiff:
        return self.canvas_manager.story.diff(
            previous=previous,
            current_stickies=current_stickies,
            current_pc=current_pc,
            current_chars=current_chars,
            recent_images=recent_images,
            current_deep_plan_revision=current_deep_plan_revision,
            current_through_turn=current_through_turn,
        )

    def _run_deep_planner_agent(
        self,
        turn_events: list[tuple[int, str, dict[str, object]]],
    ) -> dict[str, object]:
        self.reset_deep_lore_call_counts()
        runner = self._get_or_create_deep_planner_runner()
        with self._deep_plan_lock:
            deep_plan_json = json.dumps(self._deep_plan, ensure_ascii=False, indent=2)
            current_through_turn = self._deep_plan_through_turn_id
            current_deep_plan_revision = self._deep_plan_revision
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

        current_stickies = {
            note["topic"]: note["info"]
            for note in self.notepad.get_present_sticky_notes()
        }
        current_pc_obj = self.character_manager.get_player_character()
        current_pc: Optional[PlayerCharacterState] = None
        if current_pc_obj is not None:
            current_pc = PlayerCharacterState(
                name=current_pc_obj.name,
                image_description=current_pc_obj.image_description,
                reference=current_pc_obj.reference,
            )
        current_chars_list = self.character_manager.get_present_characters()
        current_chars: dict[str, CharacterState] = {
            c.name: CharacterState(
                name=c.name,
                personality=c.personality,
                motivation=c.motivation,
                quirk=c.quirk,
                image_reference=c.image_reference,
            )
            for c in current_chars_list
        }
        recent_images = self._get_recent_images()

        is_initial = self._last_state_snapshot is None
        if is_initial:
            prompt_input = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
                is_initial=True,
                turn_events=event_payloads,
                current_notes=self.notepad.get_present_sticky_notes(),
                player_character=current_pc_obj,
                characters=current_chars_list,
                recent_images=recent_images,
                deep_plan_json=deep_plan_json if deep_plan_json != "{}" else "",
            ).strip()
        else:
            prev = self._last_state_snapshot
            assert prev is not None
            story_diff = self.canvas_manager.story.diff(
                previous=prev,
                current_stickies=current_stickies,
                current_pc=current_pc,
                current_chars=current_chars,
                recent_images=recent_images,
                current_deep_plan_revision=current_deep_plan_revision,
                current_through_turn=heartbeat_through_turn,
            )
            prompt_input = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
                is_initial=False,
                turn_events=event_payloads,
                sticky_diffs=story_diff.sticky_diffs,
                player_character_diff=story_diff.player_character_diff,
                character_diffs=story_diff.character_diffs,
                new_images=story_diff.new_images,
                deep_plan_diff=story_diff.deep_plan_diff,
            ).strip()

        async def run_turn() -> dict[str, object]:
            run_config = (
                RunConfig(context_window_compression=self._run_compression_config)
                if self._run_compression_config
                else None
            )
            async for event in runner.run_async(
                user_id="story_deep_planner",
                session_id=self.session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt_input)]),
                run_config=run_config,
            ):
                # Sticky writes occur through notepad tools as the agent runs.
                pass
            # Record state snapshot at the end of the agent's turn
            self._record_state_snapshot()
            # A heartbeat that finds nothing to change is still a valid pass.
            return {"tool_updates": True}

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

    def export_planning_state(self) -> dict[str, object]:
        with self._deep_plan_lock:
            deep_plan = dict(self._deep_plan)
            deep_plan_revision = self._deep_plan_revision
            deep_plan_through_turn_id = self._deep_plan_through_turn_id

        return {
            **self.notepad.export_state(),
            "deep_plan": deep_plan,
            "deep_plan_revision": deep_plan_revision,
            "deep_plan_through_turn_id": deep_plan_through_turn_id,
        }

    def import_planning_state(self, state: dict[str, object]) -> None:
        if not state:
            return

        self._last_state_snapshot = None
        self.notepad.import_state(state)

        with self._deep_plan_lock:
            raw_imported_plan = state.get("deep_plan") or {}
            self._deep_plan = dict(raw_imported_plan)
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
