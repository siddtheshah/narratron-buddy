"""Story planning module: owns planner sticky notes and background deep planning."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
import threading
from threading import Lock
import time
from typing import Callable, Optional

from jinja2 import Template
from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import RunConfig, Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import JsonValue, TypeAdapter

from components.canvas.story_state import (
    CharacterState,
    PlayerCharacterState,
    StoryState,
)
from tools.story.story_snapshot import (
    StoryStateDiff,
    StoryStateSnapshot,
    create_snapshot,
    diff,
)
from components.canvas_state import CanvasStateManager
from components.canvas.game_state import GameState
from components.theater_manager import Theater
from components.reference_manager import CharacterLookupResult, ReferenceManager, PlayerCharacter
from components.lore_library import LoreLibrary
from components.notepad import NoteSnapshot
from tools.story.story_session import StorySession
from tools.story.story_staged_notes import StagedStoryNotes
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
MAX_DEEP_READ_LORE_CALLS_PER_RUN = 8
MAX_DEEP_SEARCH_LORE_CALLS_PER_RUN = 8


_DEEP_PLANNING_INSTRUCTION_TEMPLATE = Template(
"""# Role & Mission
You are the deep planner for an interactive adventure. You do not narrate the current scene and you never choose actions, speech, thoughts, or feelings for the player. Take a broad view of the player's accumulated actions, established lore, unresolved setups, off-screen actors, deadlines, and world consequences.

You own only `planner`-owned sticky notes. Assimilate the committed turn by deciding which planner-owned notes, if any, need an update. Use `check_schema(topic)` before updating a schema-backed sticky note and `update_sticky_note(topic, info)` once per changed note; never attempt to write `recorder`-owned sticky notes or restate notes that did not change.
{% if allow_freeform -%}
Freeform sticky creation is enabled. You may also create and update shared freeform topics with `update_sticky_note(topic, info)`, using a descriptive topic and a concise plain string for contingent plans. No planning.yaml entry is needed for a new topic. Shared freeform notes can be updated by either recorder or planner; preserve established facts and avoid duplicating existing topics. Declared topics still require their owner's schema. Notes have a bounded capacity; older freeform topics may be evicted.
{% endif -%}
Follow your pinned PLANNER.md for adventure-specific priorities. The responder (RESPONDER.md) owns immediate action resolution and narration, and the recorder (RECORDER.md) owns factual state recording and bookkeeping. Player Action events are intentions awaiting resolution: research relevant lore and prepare plans immediately, but never record attempted actions as successful, spend resources, advance clocks, or apply consequences until a Responder Response or committed game event establishes them. The response is a second event for the same action, not another player turn; never apply its costs or consequences twice.

# Non-Negotiable Planning Rules
- Committed events are immutable facts. Replan around them; never retcon them to protect an outline.
- Independent Analysis & Responder Communication: The turn responder directly communicates cues via 'Turn responder direct communication & planning signals' to improve recall (such as `[STICKY UPDATE: <Topic>]`) and planning requests to highlight emerging developments. While you must promptly review all flagged stickies, DO NOT solely rely on responder signals. The responder operates quickly and focuses on immediate narration; it may overlook subtle lore rules, looming deadlines, or off-screen consequences. You are the sole authoritative owner of planner-owned sticky notes and must perform your own independent analysis across the entire turn (action, narration, dialogue, dice results, and lore constraints). Update any planner-owned sticky note that requires modification, even if the responder omitted or failed to flag it. Factual recording of inventory, location, stats, and resources is maintained by the recorder.
- Preserve player agency. Never prescribe player actions.
- Maintain causal continuity across many turns. Advance factions and antagonists off-screen when their knowledge, resources, motives, and elapsed time justify it.
- Audit earlier player behavior when lore calls for counter-plotting. Opposition should investigate evidence the player plausibly left, but must not gain impossible knowledge.
{% if not allow_freeform -%}
- Use the author-defined sticky fields as the only durable planning vocabulary. Do not invent a parallel generic summary, thread tracker, consequence list, clock, or faction model.
- Think across short, medium, and long horizons, but publish only the configured sticky state.
{% else -%}
- Think across short, medium, and long horizons. Publish concise contingent plans in declared planner-owned topics or shared freeform topics.
{% endif -%}
- If there are stickies that are for plot building or reveals, these are a priority for you to set up and maintain.
- For planner-owned stickies that track numeric state, clocks, or hidden scores, ensure you follow the appropriate rules defined in the lore to faithfully update them. Factual inventory, player stats, and mechanical state are handled by the recorder.
- For you, accuracy is more important than speed.
{% if has_game -%}
- Read the Current Game State and rules to develop conditional plans. Game state is read only. The recorder alone chooses and executes permitted game actions and decides whether narration is warranted.
{% endif -%}

# Character, Lore, and Image Tools
Use get_player_character_info and lookup_character for read-only reference context. The recorder owns canonical character and scene changes.
Use `deep_search_lore` and `deep_read_lore` to close concrete lore gaps, with up to eight searches and eight reads per run. Use `find_image_names` when a sticky needs the name of an available reference image. Store the returned `alias` (preferred) or `name`, never a guessed filename or absolute path. Prefer established lore over invention. Do not roll dice; you are planning, not resolving an uncertain action.

# Completion
After tool calls, respond with a brief confirmation. Do not include a sticky-notes JSON payload in the final response.
{% if style -%}

# Typical Planning Procedure

1. Search lore library for relevant story info {% if has_game %} or game rules {% endif %} using `deep_search_lore`
2. Read relevant lore documents using `deep_read_lore`
3. Update planner-owned sticky notes{% if allow_freeform %} or shared freeform topics{% endif %} using lore rules. Never execute game actions or trigger peers.
4. Come up with some interesting possible developments given current state.
5. Use your taste and imagination to update planner-owned sticky notes{% if allow_freeform %} or shared freeform topics{% endif %} based on that plan.

# Story-Planning Style
{{ style }}
{% endif -%}
{% if sticky_schema_json -%}

# Sticky State Schema Reference
Each planner-owned topic below is independently validated by the notepad tool. Do not attempt to publish a complete `sticky_notes` object. Call `check_schema(topic)` immediately before each schema-backed update; for field-backed notes, its `info` argument must be a JSON object encoded as a string. Never attempt to write recorder-owned sticky notes.
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
Assimilate the supplied event batch and use the available tool budget to research and maintain accurate state. Player intentions and their eventual responder results arrive separately; reconcile them without counting an action twice.
{% if game_state_json -%}
# Current Game State and Narratron Actions
{{ game_state_json }}
{% endif -%}
{% if reference_images -%}
## Available References
{% for img in reference_images -%}
- Name: `{{ img.name }}` | Description: {{ img.description }}
{% endfor -%}
{% endif -%}
{% if turn_events -%}
## Story Event Queue
{% for event in turn_events -%}
{% if event.source == 'game' -%}
### Player Game Action
- Submitted action: {{ event.game_action_json }}
- Committed new state: {{ event.committed_game_state_json }}
{% elif event.source == 'user_action' -%}
### Player Action (Awaiting Responder Resolution)
- Player action: {{ event.user_action }}
{% if event.nudge %}- Responder direction: {{ event.nudge }}{% endif %}
{% else -%}
### Responder Response — Turn {{ event.turn_id }}
- Player action: {{ event.user_action }}
- Immediate narration: {{ event.narration }}
{% endif -%}
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
## Newly Available References
The following reference images became available since the last heartbeat:
{% for img in new_images -%}
- Name: `{{ img.name }}`{% if img.title %} | Title: "{{ img.title }}"{% endif %}{% if img.description and img.description != ('Image ' ~ img.name) and img.description != ('Image ' ~ img.name ~ '.jpg') and img.description != ('Image ' ~ img.name ~ '.png') and img.description != ('Image ' ~ img.name ~ '.webp') %} | Description: {{ img.description }}{% endif %}
{% endfor -%}

{% endif -%}
{% if notepad_diff -%}
## Notepad Update
- {{ notepad_diff }}
{% endif -%}
{% else -%}

# Current Sticky Notes
{% if current_notes -%}
{% for note in current_notes -%}
- {% if note.owner %}[{{ note.owner }}] {% endif %}{{ note.topic }}: {{ note.info }}
{% endfor -%}
{% else -%}
(No active sticky notes)
{% endif -%}

{% if player_character -%}
# Canonical Player Character
- Name: {{ player_character.name or 'Unnamed Explorer' }}{% if player_character.image_description %} | Visual: {{ player_character.image_description }}{% endif %}

{% endif -%}
{% if characters -%}
# Canonical Active Characters
{% for char in characters -%}
- {{ char.name }}: Personality: {{ char.personality or 'N/A' }} | Motivation: {{ char.motivation or 'N/A' }} | Quirk: {{ char.quirk or 'N/A' }}
{% endfor -%}

{% endif -%}

{% endif -%}
"""
)

PlannerStateSnapshot = StoryStateSnapshot


class StoryPlanningModule:
    """Owner of background deep planning and planner-owned sticky notes."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        lore_library: LoreLibrary,
        reference_manager: ReferenceManager,
        session_service: InMemorySessionService,
        session_id: str,
        story_log_context_fn: Optional[Callable[[], str]] = None,
        game_state: GameState | None = None,
        story_session: StorySession | None = None,
    ) -> None:
        if theater is None:
            raise ValueError("theater is required.")
        if canvas_manager is None:
            raise ValueError("canvas_manager is required.")
        if canvas_manager.notepad is None:
            raise ValueError("canvas_manager.notepad is required.")
        if lore_library is None:
            raise ValueError("lore_library is required.")
        if reference_manager is None:
            raise ValueError("reference_manager is required.")
        if session_service is None:
            raise ValueError("session_service is required.")
        if not session_id:
            raise ValueError("session_id is required.")
        self.notepad = canvas_manager.notepad
        self.story_session = story_session
        self.staged_notes = StagedStoryNotes(self.notepad, "planner")

        self.theater = theater
        self.theater_id: str = theater.theater_id or ""
        self.canvas_manager = canvas_manager
        self.game_state = game_state
        self.lore_library = lore_library
        self.reference_manager = reference_manager
        self._deep_read_lore_calls_this_run = 0
        self._deep_search_lore_calls_this_run = 0
        self._deep_lore_calls_lock = Lock()
        self.session_service = session_service
        self.session_id = str(session_id)
        self._story_log_context_fn = story_log_context_fn or (lambda: "")
        raw_config = theater.config()
        self.config = raw_config.get("story_planning", raw_config)

        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        configured_style = self.config.get("style", DEFAULT_STORY_PLANNING_STYLE)
        self.style: str = str(configured_style).strip() or DEFAULT_STORY_PLANNING_STYLE

        # Processing progress is independent of notepad content revisions.
        self._planned_through_turn_id: int = 0
        self._planning_progress_lock = Lock()
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
        self.planner_model: str = str(self.config.get("planner_model") or "gemini-3.8-flash")
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
        self.compaction_config: Optional[EventsCompactionConfig] = (
            None if story_session is not None else self._build_compaction_config()
        )
        self._run_compression_config: Optional[types.ContextWindowCompressionConfig] = (
            None if story_session is not None else self._build_run_compression_config()
        )

        self.plugins: list[BasePlugin] = [ReflectAndRetryToolPlugin()]

        self.on_trigger_story_response: Optional[
            Callable[[str, str], dict[str, JsonValue] | str]
        ] = None
        self._running_heartbeat: bool = False
        self._current_heartbeat_has_game_action: bool = False
        self._current_game_action: Optional[dict[str, JsonValue]] = None
        self._story_response_triggered_this_heartbeat: bool = False

        self._deep_planner_agent: Agent = self._create_deep_planner_agent()
        self._deep_planner_app: App | None = None if story_session is not None else App(
            name="narratron_story_deep_planner",
            root_agent=self._deep_planner_agent,
            plugins=self.plugins,
            events_compaction_config=self.compaction_config,
        )
        self._deep_planner_runner: Runner | None = None if story_session is not None else Runner(
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
        return self.lore_library.read_lore(document, role="planner")

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

    def lookup_character(self, query: str = "") -> CharacterLookupResult:
        """List all session characters or search by name or trait."""
        return self.reference_manager.lookup_character(query).for_model_context()

    def get_player_character(self) -> PlayerCharacter | None:
        """Return the canonical persisted identity and visual reference for the player character."""
        return self.reference_manager.get_player_character()

    def get_player_character_info(self) -> str:
        """Retrieve the canonical player character identity and visual reference."""
        player = self.reference_manager.get_player_character()
        if not player:
            return "No player character has been canonically established yet."
        return player.describe()

    def update_player_character(
        self,
        name: str = "",
        image_description: str = "",
    ) -> str:
        """Canonically manage or update the player character's name or visual description."""
        player = self.reference_manager.update_player_character(
            name=name,
            image_description=image_description,
        )
        return f"Canonically updated player character '{player.name}'. Visual: {player.image_description or 'N/A'}."

    def create_or_update_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Optional[list[str]] = None,
        gender: Optional[str] = None,
    ) -> str:
        """Canonically create or update an NPC record in the reference manager."""
        char = self.reference_manager.create_or_update_character(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
            gender=gender,
        )
        if not char:
            return f"Failed to update character '{name}'."
        return f"Canonically updated character '{char.name}' (alias: {char.alias})."

    def create_or_update_scene(
        self,
        name: str,
        description: str = "",
    ) -> str:
        """Canonically create or update a scene record in the reference manager."""
        clean_name = str(name).strip()
        if not clean_name:
            return "Scene name cannot be empty."
        entry = self.reference_manager.create_or_update_scene(
            name=clean_name,
            description=str(description).strip(),
        )
        if entry is None:
            return f"Failed to generate or resolve scene reference for '{clean_name}'."
        return f"Scene reference for '{clean_name}' is set."

    def find_image_names(self, query: str = "") -> list[dict[str, str]]:
        """Find reference image tags and names for use in sticky notes.

        Returns only catalog data; this never displays an image or changes the
        canvas. Prefer the returned alias when recording an image reference.
        """
        return self.reference_manager.find_image_names(query)

    def _build_deep_planner_instruction(self, ctx: ReadonlyContext | None = None) -> str:
        lore_context = self.lore_library.get_lore_context(role="planner")
        sticky_definitions = {
            topic: {
                k: v
                for k, v in definition.items()
                if k in ("description", "fields", "render", "required", "owner") and v is not None
            }
            for topic, definition in self.notepad.sticky_definitions.items()
            if definition.get("owner") == "planner"
        }
        sticky_schema_json = (
            json.dumps(
                sticky_definitions,
                ensure_ascii=False,
                indent=2,
            )
            if sticky_definitions
            else ""
        )
        return _DEEP_PLANNING_INSTRUCTION_TEMPLATE.render(
            style=self.style,
            sticky_schema_json=sticky_schema_json,
            lore_context=lore_context or "",
            has_game=self.game_state is not None and self.game_state.program is not None,
            allow_freeform=not self.notepad.enforce_structured,
        ).strip()

    def take_game_action(self, name: str, arguments_json: str, revision: int) -> str:
        return "Error: Only the recorder may mutate game state."

    def trigger_story_response(self, user_action: str = "", nudge: str = "") -> str:
        return "Error: Only StoryTool may schedule story roles."

    def _game_state_json(self) -> str:
        if self.game_state is None:
            return ""
        snapshot = self.game_state.story_snapshot("narratron")
        return json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else ""

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
            description="Long-horizon story planner and owner of planner-owned sticky notes.",
            model=VertexGemini(
                model=self.deep_planner_model,
                project_id=self.vertex_project,
                location=self.vertex_location,
            ),
            instruction=self._build_deep_planner_instruction,
            tools=[
                self.deep_search_lore,
                self.deep_read_lore,
                self.lookup_character,
                self.get_player_character_info,
                self.find_image_names,
                self.staged_notes.check_schema,
                self.staged_notes.update_sticky_note,
            ],
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            output_key="story_planner_output",
            include_contents="none" if self.story_session is not None else "default",
            generate_content_config=generate_content_config,
        )

    def plan_recorded_turn(self, handoff: dict[str, JsonValue], source: NoteSnapshot) -> dict[str, str]:
        """Plan exactly one committed turn; publishing cannot schedule a peer."""
        if self.story_session is None:
            raise RuntimeError("A shared story session is required for turn planning.")
        self.reset_deep_lore_call_counts()
        self.staged_notes.updates.clear()
        prompt = json.dumps({
            "recorded_turn": handoff,
            "source_revision": source.revision,
            "sticky_notes": source.notes,
            "game_state": handoff.get("game_state"),
        }, ensure_ascii=False)
        self.story_session.run("planner", prompt, timeout=self.deep_planner_timeout_seconds, stage_id=str(handoff["turn_id"]))
        return dict(self.staged_notes.updates)

    def restart_deep_planner_agent(self) -> None:
        if self.story_session is not None:
            self._last_state_snapshot = None
            self.reset_deep_lore_call_counts()
            return
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

    def wait_for_deep_planning(
        self, timeout: float = 5.0, target_turn_id: Optional[int] = None
    ) -> bool:
        if not self.deep_planning_enabled:
            return True
        if target_turn_id is None:
            return self.wait_for_pending_events(timeout)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._planning_progress_lock:
                if self._planned_through_turn_id >= target_turn_id:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def wait_for_pending_events(self, timeout: float = 5.0) -> bool:
        """Drain queued events, including game events that have no story turn ID."""
        with self._deep_planning_queue_lock:
            worker = self._deep_planning_worker
        if worker is not None:
            worker.join(timeout=max(0.0, timeout))
            if worker.is_alive():
                return False
        with self._deep_planning_queue_lock:
            return not self._deep_planning_queue

    def _record_state_snapshot(self) -> None:
        current_stickies = {
            note["topic"]: note["info"]
            for note in self.notepad.get_present_sticky_notes()
        }
        current_pc_obj = self.reference_manager.get_player_character()
        current_pc: Optional[PlayerCharacterState] = None
        if current_pc_obj is not None:
            current_pc = PlayerCharacterState(
                name=current_pc_obj.name,
                image_description=current_pc_obj.image_description,
                reference=current_pc_obj.reference,
            )
        current_chars_list = self.reference_manager.get_present_characters()
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
        reference_images = self.reference_manager.list_references()
        previous_known: set[str] = (
            self._last_state_snapshot.known_image_names
            if self._last_state_snapshot is not None
            else set()
        )
        all_known_images = previous_known | {
            str(img.get("name") or "") for img in reference_images if img.get("name")
        }
        story_state = self.canvas_manager.story if self.canvas_manager is not None and self.canvas_manager.story is not None else StoryState()
        self._last_state_snapshot = create_snapshot(
            story_state=story_state,
            notepad=self.notepad,
            stickies=current_stickies,
            player_character=current_pc,
            characters=current_chars,
            known_image_names=all_known_images,
            notepad_revision=self.notepad.revision,
        )

    def _complete_planning_heartbeat(
        self, turn_id: int, result: dict[str, JsonValue]
    ) -> bool:
        if not result.get("tool_updates") or result.get("error"):
            return False

        with self._planning_progress_lock:
            self._planned_through_turn_id = max(self._planned_through_turn_id, turn_id)

        logger.info(
            "[StoryPlanningModule] Planning completed through turn=%d at notepad revision=%d",
            turn_id,
            self.notepad.revision,
        )
        return True

    def queue_deep_planning(
        self, turn_id: int, action: str, result: dict[str, object]
    ) -> threading.Thread:
        if self.story_session is not None:
            raise RuntimeError("StoryTool owns per-turn planner scheduling.")
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
                    with self._deep_planning_queue_lock:
                        batch = list(self._deep_planning_queue)[: self.deep_max_events_per_turn]

                    if batch:
                        update = self._run_deep_planner_agent(batch)
                        highest_turn_id = max(turn_id for turn_id, _, _ in batch)
                        try:
                            committed = self._complete_planning_heartbeat(highest_turn_id, update)
                        except Exception:
                            committed = False
                            logger.exception(
                                "[StoryPlanningModule] Failed to commit deep heartbeat through turn=%d",
                                highest_turn_id,
                            )
                        if not committed:
                            consecutive_failures += 1
                            if consecutive_failures <= self.deep_heartbeat_failure_retries:
                                time.sleep(self.deep_heartbeat_seconds)
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
                        with self._planning_progress_lock:
                            through_turn_id = self._planned_through_turn_id
                        update = self._run_deep_planner_agent([])
                        try:
                            self._complete_planning_heartbeat(through_turn_id, update)
                        except Exception:
                            logger.exception(
                                "[StoryPlanningModule] Failed to complete idle planning refinement"
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
        current_notepad_revision: int,
        current_through_turn: int,
    ) -> StoryStateDiff:
        story = self.canvas_manager.story if self.canvas_manager is not None else None
        return diff(
            previous=previous,
            story_state=story,
            notepad=self.notepad,
            current_stickies=current_stickies,
            current_pc=current_pc,
            current_chars=current_chars,
            recent_images=recent_images,
            current_notepad_revision=current_notepad_revision,
            current_through_turn=current_through_turn,
        )

    def _run_deep_planner_agent(
        self,
        turn_events: list[tuple[int, str, dict[str, object]]],
    ) -> dict[str, JsonValue]:
        self.reset_deep_lore_call_counts()
        has_game_action: bool = False
        latest_game_action: Optional[dict[str, JsonValue]] = None
        for _, _, reaction in turn_events:
            if reaction.get("source") == "game":
                has_game_action = True
                game_act = reaction.get("game_action")
                if type(game_act) is dict:
                    latest_game_action = game_act
        self._current_heartbeat_has_game_action = has_game_action
        self._current_game_action = latest_game_action
        self._story_response_triggered_this_heartbeat = False
        self._running_heartbeat = True
        try:
            runner = self._get_or_create_deep_planner_runner()
            with self._planning_progress_lock:
                current_through_turn = self._planned_through_turn_id
            current_notepad_revision = self.notepad.revision
            event_payloads = [
                {
                    "source": str(scene_reaction.get("source") or "story"),
                    "turn_id": turn_id,
                    "user_action": user_action,
                    "nudge": str(scene_reaction.get("nudge") or ""),
                    "game_action_json": json.dumps(scene_reaction.get("game_action"), ensure_ascii=False),
                    "committed_game_state_json": json.dumps(scene_reaction.get("game_state"), ensure_ascii=False),
                    "narration": str(scene_reaction.get("narration") or "").strip(),
                    "dialogue": scene_reaction.get("dialogue") or [],
                    "planning_signals": scene_reaction.get("planning_signals") or [],
                    "characters": scene_reaction.get("manifested_characters") or [],
                    "die_rolls": scene_reaction.get("die_rolls") or [],
                }
                for turn_id, user_action, scene_reaction in turn_events
            ]
            heartbeat_through_turn = (
                max(turn_id for turn_id, _, _ in turn_events) if turn_events else current_through_turn
            )

            current_stickies = {
                note["topic"]: note["info"]
                for note in self.notepad.get_present_sticky_notes()
            }
            current_pc_obj = self.reference_manager.get_player_character()
            current_pc: Optional[PlayerCharacterState] = None
            if current_pc_obj is not None:
                current_pc = PlayerCharacterState(
                    name=current_pc_obj.name,
                    image_description=current_pc_obj.image_description,
                    reference=current_pc_obj.reference,
                )
            current_chars_list = self.reference_manager.get_present_characters()
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
            reference_images = self.reference_manager.list_references()

            owners = {
                topic: str(definition.get("owner") or "unknown")
                for topic, definition in self.notepad.sticky_definitions.items()
            }
            present_notes = [
                {
                    **note,
                    **({"owner": owners[note["topic"]]} if note["topic"] in owners else {}),
                }
                for note in self.notepad.get_present_sticky_notes()
            ]

            is_initial = self._last_state_snapshot is None
            if is_initial:
                prompt_input = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
                    is_initial=True,
                    game_state_json=self._game_state_json(),
                    turn_events=event_payloads,
                    current_notes=present_notes,
                    player_character=current_pc_obj,
                    characters=current_chars_list,
                    reference_images=reference_images,
                ).strip()
            else:
                prev = self._last_state_snapshot
                assert prev is not None
                story_diff = self._compute_state_diffs(
                    previous=prev,
                    current_stickies=current_stickies,
                    current_pc=current_pc,
                    current_chars=current_chars,
                    recent_images=reference_images,
                    current_notepad_revision=current_notepad_revision,
                    current_through_turn=heartbeat_through_turn,
                )
                prompt_input = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
                    is_initial=False,
                    game_state_json=self._game_state_json(),
                    turn_events=event_payloads,
                    sticky_diffs=story_diff.sticky_diffs,
                    player_character_diff=story_diff.player_character_diff,
                    character_diffs=story_diff.character_diffs,
                    reference_images=reference_images,
                    new_images=story_diff.new_images,
                    notepad_diff=story_diff.notepad_diff,
                ).strip()

            async def run_turn() -> dict[str, JsonValue]:
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
                return {"error": "Deep planner timed out; accepted tool updates remain applied."}
            except Exception as exc:
                logger.exception(
                    "[StoryPlanningModule] Deep planner heartbeat failed through turn=%d",
                    heartbeat_through_turn,
                )
                return {"error": f"Deep planner failed: {exc}"}
        finally:
            self._running_heartbeat = False
            self._current_heartbeat_has_game_action = False
            self._current_game_action = None
            self._story_response_triggered_this_heartbeat = False

    def export_planning_state(self) -> dict[str, JsonValue]:
        with self._planning_progress_lock:
            through_turn_id = self._planned_through_turn_id
        return {
            **self.notepad.export_state(),
            "planned_through_turn_id": through_turn_id,
        }

    def import_planning_state(self, state: dict[str, JsonValue]) -> None:
        if not state:
            return

        self._last_state_snapshot = None
        self.notepad.import_state(state)
        try:
            legacy_plan = TypeAdapter(dict[str, JsonValue]).validate_python(state.get("deep_plan") or {})
        except ValueError:
            legacy_plan = {}
        with self._planning_progress_lock:
            try:
                self._planned_through_turn_id = max(0, TypeAdapter(int).validate_python(
                    state.get("planned_through_turn_id", state.get("deep_plan_through_turn_id", legacy_plan.get("through_turn_id", 0)))
                ))
            except ValueError:
                self._planned_through_turn_id = 0
