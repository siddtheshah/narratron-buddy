"""Session-scoped story planning and planner-owned plot-beat tools.

Logging & Script Inspection:
----------------------------
All plot-beat updates, character mutations, and scene element mutations emit
formatted debug log records tagged with ``[StoryPlanningTools]``.

To inspect story script outputs over time during server execution, filter
console logging using the existing ``--log_prefixes`` flag when running
``main.py``:

    python main.py --log_prefixes="[StoryPlanningTools]"
"""

from collections import OrderedDict
import json
import logging
import os
import re
import secrets
import math
from threading import Lock
import time
import asyncio
from typing import Any, Callable, List, Dict, Optional, Tuple

from jinja2 import Template
from pydantic import BaseModel, Field, PrivateAttr
from google.adk.agents import Agent
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.models.google_llm import Gemini
from google.adk.runners import RunConfig, Runner
from google.adk.sessions import InMemorySessionService
from google import genai
from google.genai import types

from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from tools.base_tool import BaseTools, logged_tool_call, with_cooldown
from services.quirk_service import get_quirk_generator_service
from providers import (
    TextResponseProvider,
    TextResponseRequest,
)

logger = logging.getLogger(__name__)

DEFAULT_COMPACTION_TRIGGER_TOKENS = 12_000
DEFAULT_COMPACTION_TARGET_TOKENS = 6_000
DEFAULT_MAX_STICKY_NOTES = 5
DEFAULT_MAX_ACTIVE_CHARACTERS = 3
DEFAULT_STORY_PLANNING_STYLE = "balanced, consequence-driven, and player-agency-first"
DEFAULT_THINKING_BUDGET = 1024
USER_ACTION_TIMEOUT_SECONDS = 20.0
MAX_STORY_PLANNING_STYLE_CHARS = 500
MAX_PLAYER_ACTION_CHARS = 2_000
MAX_NUDGE_CHARS = 1_000
MAX_LORE_DOCUMENT_CONTEXT_CHARS = 12_000
MAX_CONTEXT_FIELD_CHARS = 500
MAX_STICKY_NOTE_TOPIC_CHARS = 100
MAX_STICKY_NOTE_INFO_CHARS = 500
MAX_PLOT_BEAT_CHARS = 500
MAX_NARRATION_CHARS = 2_000
MAX_NODES_AHEAD = 10
MAX_STICKY_NOTES = 10
MAX_NAMED_ELEMENTS = 10
MAX_ACTIVE_CHARACTERS = 10
MAX_LORE_DOCUMENTS_LISTED = 100
MAX_READ_LORE_CALLS_PER_TURN = 3
MAX_SEARCH_LORE_CALLS_PER_TURN = 3
SUPPORTED_VOICE_TAGS = {"male", "female"}


def normalize_voice_tags(tags: Any) -> List[str]:
    """Normalize input into a list containing only supported voice tags ('male' or 'female')."""
    if not tags:
        return []
    if isinstance(tags, str):
        candidates = [t.strip().lower() for t in re.split(r"[,\s]+", tags) if t.strip()]
    elif isinstance(tags, (list, tuple, set)):
        candidates = [str(t).strip().lower() for t in tags if str(t).strip()]
    else:
        candidates = [str(tags).strip().lower()]
    seen = set()
    result = []
    for c in candidates:
        if c in SUPPORTED_VOICE_TAGS and c not in seen:
            seen.add(c)
            result.append(c)
    return result

_CHARACTER_GEN_PROMPT_TEMPLATE = Template(
    """Character name: {{ name }}
{% if description -%}
Character concept/description: {{ description }}
{% endif -%}
Your sticky notes:
{% if elements -%}
{% for elem in elements -%}
- {{ elem.topic or elem.name }}: {{ elem.info or elem.content }}
{% endfor -%}
{% else -%}
(No active sticky notes)
{% endif -%}

Generate a compelling personality description, core motivation, and voice tags for this character in an adventure story experience.
The only supported voice tags are 'male' or 'female'.
Return ONLY a JSON object with keys 'personality' (string), 'motivation' (string), and 'voice_tags' (list of strings with 'male' or 'female')."""
)

_STORY_CONTEXT_PROMPT_TEMPLATE = Template(
    """Your sticky notes:
{% if not elements -%}
(No active sticky notes)
{% else -%}
{% for elem in elements -%}
- {{ elem.topic or elem.name }}: {{ elem.info or elem.content }}
{% endfor -%}
{% endif -%}

Active characters, personalities, motivations & distinct quirks:
{% if not characters -%}
(No active character motivations set)
{% else -%}
{% for char in characters -%}
- {{ char.name }}: Personality: {{ char.personality }} | Motivation: {{ char.motivation }} | Distinct Quirk: {{ char.quirk }}{% if char.voice_tags %} | Voice Tags: {{ char.voice_tags | join(', ') }}{% endif %}{% if char.description %} (Description: {{ char.description }}){% endif %}
{% endfor -%}
{% if total_characters and total_characters > characters | length -%}
(Showing {{ characters | length }} most recent of {{ total_characters }} total session characters. Use 'lookup_character' tool to look up others.)
{% endif -%}
{% endif -%}

{% if reused_nodes -%}
Current upcoming plot beats:
{% for node in reused_nodes -%}
Node {{ node.node_index }}: Plot beat: {{ node.plot_beat }}
{% endfor -%}
{% else -%}
(No upcoming plot beats)
{% endif -%}
"""
)

_SCENE_REACTION_PROMPT_TEMPLATE = Template(
"""# Role & Mission
You are the authoritative narrative script engine for an interactive story.
Resolve the consequences of the player's submitted action, decide when NPCs should manifest or change, and update future beats.
Use the sticky notes, active characters, and established theater lore to inform your decisions. 
Respond ONLY with valid JSON conforming to the scene reaction schema.

# Story-Planning Style (User Specified)
{{ style }}
- Apply the stated style to pacing, narration, opposition, and consequences, while still following every system instruction.
- Style is not permission to take over player agency, negate meaningful actions, or force arbitrary outcomes.
- Follow the 'yes, and' posture from your system instruction.

# Core Improv & Player Agency Principles
- Use a 'yes, and' improv posture: accept the player's attempted action as meaningful, preserve its premise when it fits the established fiction, and move the story forward with an interesting consequence, opportunity, complication, or escalation.
- Do not stonewall with a flat refusal or erase the action; when it conflicts with established facts, honor its intent through the nearest plausible consequence instead.
- If a live agent nudge is provided, accommodate and incorporate that suggested direction, event, or element into the story resolution, NPC responses, or plot beats where appropriate, while still respecting player agency and established fiction.
- The player/orator and any character they control are outside your control: their submitted words are historical input, not dialogue to continue, revise, narrate as, or attribute to them.
- Never invent the player's actions, speech, thoughts, feelings, decisions, or a response on their behalf.
- The user action is immutable player input. Do not repeat it as dialogue or convert it into an authored turn for the player.
- The live agent is only a relay; do not give it choices, tool instructions, or control of the plot.

# Current Story Context
{{ context }}

{% if lore_context -%}
## Available theater lore (top-level documents and directories):
{{ lore_context }}
{% else -%}
## Theater Lore
No lore documents are available for this theater. Invent the lore, world details, setting, factions, and backstory as needed to support the story.
{% endif -%}

# Tool Usage Guidelines
- **Sticky Notes (`update_sticky_note`)**: Use sticky notes to track major plot developments, story milestones, key discoveries, active goals, and persistent scene state (characters, locations, objects). Call `update_sticky_note` with a concise `topic` and informative `info` whenever a major plot event or status shift occurs to maintain narrative continuity across turns. The scene holds at most {{ max_sticky_notes or 5 }} sticky notes; when full, adding a new topic will drop the oldest non-required sticky note. Required sticky notes are persistent and will never be dropped.
- **Lore Search & Reading (`search_lore`, `read_lore`)**: Ground the narrative, characters, factions, and setting in established theater lore. You may call `search_lore` to perform a keyword search across all lore files and find the most relevant documents by relevance score, and `read_lore` to read full lore documents or directories. `search_lore` and `read_lore` are capped separately: you may call search_lore at most 3 times and read_lore at most 3 times in a single turn. Once you have sufficient context, proceed immediately to return the scene reaction. If no lore is available, invent the lore freely without calling search_lore or read_lore.
- **Dice Rolling (`roll_dice`)**: When an action's outcome is genuinely uncertain, call roll_dice and use the returned result to decide the consequence; do not fabricate a roll.
- **Character Lookup (`lookup_character`)**: Call `lookup_character` to list all known session characters or search for a specific NPC by name, role, or trait to view their full profile, personality, motivation, and quirk when encountering or referencing characters created earlier in the story.
- **Character Generation (`generate_character_profile`)**: You may call generate_character_profile to enrich a proposed NPC, then include its returned profile in character_updates.
- Those tools only provide information: the scene delta is the sole source of changes.

# Scene Reaction Output Requirements
- **Narration**: Write narration only about the world and the consequences of the submitted action. Keep responses focused: narration should normally be 20-50 words that also describe the visual resolution and immediate outcome of the character's action rather than just scenery alone. Return one complete scene delta that leaves the player's next action, speech, thoughts, and choices entirely open.
- **Dialogue**: `dialogue` is optional and must contain NPC speech only (at most three short lines). Dialogue may be spoken only by NPCs; never emit dialogue for a speaker called Player, User, Orator, You, or for the player-controlled character.
- **Plot Beats**: Include exactly {{ nodes_ahead }} general plot beats in plot_beats; they must not predict, request, or prescribe player actions.
- **Character Updates**: Character updates are for NPCs only. Include character_updates only for NPCs that should enter or materially change; never create or update the player-controlled character. When creating or updating characters, define voice_tags as a list containing 'male' or 'female' to guide speech synthesis.

# Character Generation
Do not expose secret character information via the character name when creating a character. Everything else is otherwise private.
If a character is diguised, make sure you give them an alias that hides their nature, rather than using their real name.

# Scene Labeling
Ensure the scene has a label. The location name is generally a good choice. Keep using that label until a major shift occurs.

# Reference Usage
If the lore documents mention reference images for characters and images, communicate them via the reference_images field.
"""
)


class PlannerDialogue(BaseModel):
    speaker: str = "Narrator"
    text: str
    kind: str = "speech"


class PlannerCharacter(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    motivation: str = ""
    quirk: str = ""
    voice_tags: List[str] = Field(
        default_factory=list,
        description="Voice classification tags ('male' or 'female') to guide speech synthesis. Required if gendered.",
    )


class SceneReaction(BaseModel):
    """Complete, typed scene delta emitted by the ephemeral ADK story planner."""
    narration: str = Field(
        default="",
        description=(
            "Evocative narrative prose (20-50 words) depicting the environment "
            "and the visible resolution or outcome of the character's action, "
            "not just scenery alone."
        ),
    )
    scene_label: str = Field(
        default="",
        description=(
            "The label for this scene."
        ),
    )
    dialogue: List[PlannerDialogue] = Field(default_factory=list)
    # These are a structured fallback when the model finalizes directly
    # instead of issuing the equivalent staged ADK tool call.
    reference_images: List[str] = Field(
        default_factory=list,
        description=(
            "If the lore mentions a reference image and it would be suitable to use,"
            "list it here (with its file extension)."
        ),
    )
    plot_beats: List[str] = Field(default_factory=list)
    character_updates: List[PlannerCharacter] = Field(default_factory=list)


class VertexGemini(Gemini):
    """ADK Gemini model with an explicit Vertex AI client, independent of env defaults."""

    project_id: Optional[str] = None
    location: str = "global"
    _client_cache: dict = PrivateAttr(default_factory=dict)

    @property
    def api_client(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop not in self._client_cache:
            self._client_cache[loop] = genai.Client(vertexai=True, project=self.project_id, location=self.location)
        return self._client_cache[loop]


def build_story_context_prompt(
    elements: List[Dict[str, str]],
    characters: List[Dict[str, Any]],
    reused_nodes: List[Dict[str, Any]],
    total_characters: int = 0,
) -> str:
    """Render shared scene, character, and plot context for every planner task."""
    return _STORY_CONTEXT_PROMPT_TEMPLATE.render(
        elements=elements or [],
        characters=characters or [],
        reused_nodes=reused_nodes or [],
        total_characters=total_characters,
    ).strip()


def build_scene_reaction_prompt(
    context: str,
    style: str,
    nodes_ahead: int,
    lore_context: str = "",
    max_sticky_notes: int = DEFAULT_MAX_STICKY_NOTES,
) -> str:
    """Render the authoritative scene reaction instruction for the planner agent."""
    return _SCENE_REACTION_PROMPT_TEMPLATE.render(
        context=context,
        style=style,
        nodes_ahead=nodes_ahead,
        lore_context=lore_context,
        max_sticky_notes=max_sticky_notes,
    ).strip()


class StoryPlanningTools(BaseTools):
    """Maintain scene context, characters, and planner-owned durable plot beats.

    Emits formatted logger records prefixed with ``[StoryPlanningTools]`` whenever scene elements
    or plot beats are updated. Filter output to story script logs using:
        python main.py --log_prefixes="[StoryPlanningTools]"
    """

    def __init__(
        self,
        config: dict | None,
        theater_id: str,
        canvas_state_service: CanvasStateService,
        theater_manager: TheaterManager,
        text_response_provider: TextResponseProvider,
    ):
        if not theater_id:
            raise ValueError("theater_id is required.")
        if canvas_state_service is None:
            raise ValueError("canvas_state_service is required.")
        if theater_manager is None:
            raise ValueError("theater_manager is required.")
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")

        super().__init__(
            config=config or {},
            theater_id=theater_id,
            canvas_state_service=canvas_state_service,
        )
        self._sticky_notes: OrderedDict[str, str] = OrderedDict()
        self._required_stickies: OrderedDict[str, str] = OrderedDict()
        self._elements = self._sticky_notes
        self._sticky_notes_lock = Lock()
        self._elements_lock = self._sticky_notes_lock
        self._characters: OrderedDict[str, Dict[str, str]] = OrderedDict()
        self._characters_lock = Lock()
        self._plot_beats: List[Dict[str, str]] = []
        self._plot_beats_lock = Lock()
        self._last_scene_reaction: Dict[str, Any] = {}
        self.theater_manager = theater_manager
        self.text_response_provider = text_response_provider

        req_keys = self.config.get("required_stickies", self.config.get("required_sticky_notes", self.config.get("required_elements", [])))
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

        self.nodes_ahead: int = max(1, min(int(self.config.get("nodes_ahead", 3)), MAX_NODES_AHEAD))
        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        configured_style = self.config.get("style", DEFAULT_STORY_PLANNING_STYLE)
        self.style: str = (
            str(configured_style).strip()[:MAX_STORY_PLANNING_STYLE_CHARS]
            or DEFAULT_STORY_PLANNING_STYLE
        )
        self.max_sticky_notes: int = max(
            len(self._required_stickies),
            max(
                1,
                min(
                    int(self.config.get("max_sticky_notes", self.config.get("max_named_elements", DEFAULT_MAX_STICKY_NOTES))),
                    MAX_STICKY_NOTES,
                ),
            ),
        )
        self.max_named_elements: int = self.max_sticky_notes
        self.max_active_characters: int = max(
            1,
            min(
                int(self.config.get("max_active_characters", DEFAULT_MAX_ACTIVE_CHARACTERS)),
                MAX_ACTIVE_CHARACTERS,
            ),
        )
        self.cooldown_duration: float = float(self.config.get("cooldown_duration", 0.0))
        self.action_cooldown_base_seconds: float = max(
            0.0, float(self.config.get("action_cooldown_base_seconds", self.cooldown_duration or 10.0))
        )
        self.action_cooldown_words_per_second: float = max(
            1.0, float(self.config.get("action_cooldown_words_per_second", 5.0))
        )
        self.action_cooldown_max_seconds: float = max(
            self.action_cooldown_base_seconds,
            float(self.config.get("action_cooldown_max_seconds", 30.0)),
        )
        self.user_action_timeout_seconds: float = USER_ACTION_TIMEOUT_SECONDS
        self.require_voice_input: bool = bool(self.config.get("require_voice_input", False))
        self._voice_input_detected: bool = not self.require_voice_input
        self._voice_input_lock: Lock = Lock()
        self._last_action_response_word_count: int = 0
        self._read_lore_calls_this_turn: int = 0
        self._read_lore_lock: Lock = Lock()
        self._search_lore_calls_this_turn: int = 0
        self._search_lore_lock: Lock = Lock()
        self._lore_activity_this_turn: List[Dict[str, Any]] = []
        self._lore_activity_lock: Lock = Lock()
        self._lore_index_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._lore_cache_lock: Lock = Lock()
        self._search_query_cache: Dict[str, str] = {}
        # Bound by AgentSession after its live queue is running. The callback
        # receives the completed planner result from a background worker.
        self.on_scene_reaction: Optional[Callable[[Dict[str, Any]], None]] = (
            self.config.get("on_scene_reaction")
        )
        # Bound by AgentSession so completed planner turns can be billed using
        # the same idempotent usage path as image and music generation.
        self.on_story_plan_completed: Optional[Callable[[], None]] = (
            self.config.get("on_story_plan_completed")
        )

        self.session_id: str = str(
            self.config.get("session_id") or f"planner_{self.theater_id}_{secrets.token_hex(8)}"
        )
        self.session_service: InMemorySessionService = (
            self.config.get("session_service") or InMemorySessionService()
        )
        self.compaction_config: Optional[EventsCompactionConfig] = self._build_compaction_config()
        self._run_compression_config: Optional[types.ContextWindowCompressionConfig] = (
            self._build_run_compression_config()
        )

        self.planner_model: str = str(self.config.get("planner_model") or "gemini-3.7-flash")
        if "thinking_budget" in self.config:
            raw_budget = self.config.get("thinking_budget")
            self.thinking_budget: Optional[int] = int(raw_budget) if raw_budget is not None else None
        else:
            self.thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET
        self.vertex_project: Optional[str] = (
            self.config.get("vertex_project")
            or self.config.get("gcloud", {}).get("project_id")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        self.vertex_location: str = str(
            self.config.get("vertex_location") or os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
        )
        self._planner_agent: Agent = self._create_planner_agent()
        self._planner_app: App = App(
            name="narratron_story_planner",
            root_agent=self._planner_agent,
            events_compaction_config=self.compaction_config,
        )
        self._planner_runner: Runner = Runner(
            app=self._planner_app,
            session_service=self.session_service,
            auto_create_session=True,
        )

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

        # Ensure all required stickies are present in _sticky_notes
        for req_topic, req_info in self._required_stickies.items():
            if req_topic not in self._sticky_notes:
                self._sticky_notes[req_topic] = req_info

        # Respect capacity by dropping excess non-required notes if initial elements exceeded limit
        while len(self._sticky_notes) > self.max_sticky_notes:
            evict_key = next((k for k in self._sticky_notes if k not in self._required_stickies), None)
            if evict_key is not None:
                del self._sticky_notes[evict_key]
            else:
                break

        initial_characters = self.config.get("initial_characters", {})
        if isinstance(initial_characters, dict):
            for k, v in initial_characters.items():
                if isinstance(v, dict):
                    self._characters[str(k)] = {
                        "name": str(k),
                        "description": str(v.get("description", "")),
                        "personality": str(v.get("personality", "")),
                        "motivation": str(v.get("motivation", "")),
                        "quirk": str(v.get("quirk", "")),
                        "voice_tags": normalize_voice_tags(v.get("voice_tags", v.get("voice_type"))),
                    }
        elif isinstance(initial_characters, list):
            for char in initial_characters:
                if isinstance(char, dict) and "name" in char:
                    self._characters[str(char["name"])] = {
                        "name": str(char["name"]),
                        "description": str(char.get("description", "")),
                        "personality": str(char.get("personality", "")),
                        "motivation": str(char.get("motivation", "")),
                        "quirk": str(char.get("quirk", "")),
                        "voice_tags": normalize_voice_tags(char.get("voice_tags", char.get("voice_type"))),
                    }

        self.reload_from_session_state()

    def export_story_planning_state(self) -> Dict[str, Any]:
        """Export durable story state."""
        with self._sticky_notes_lock:
            notes_list = [
                {"topic": topic, "info": info, "name": topic, "content": info}
                for topic, info in self._sticky_notes.items()
            ]
        with self._characters_lock:
            chars_list = [dict(c) for c in self._characters.values()]
        with self._plot_beats_lock:
            plot_beats = list(self._plot_beats)

        return {
            "sticky_notes": [{"topic": n["topic"], "info": n["info"]} for n in notes_list],
            "named_elements": [{"name": n["name"], "content": n["content"]} for n in notes_list],
            "characters": chars_list,
            "plot_beats": plot_beats,
            "last_scene_reaction": dict(self._last_scene_reaction),
        }

    def import_story_planning_state(self, state: Dict[str, Any]) -> None:
        """Import full story planning state from a dict."""
        if not isinstance(state, dict):
            return

        with self._sticky_notes_lock:
            self._sticky_notes.clear()
            notes = state.get("sticky_notes", state.get("named_elements", []))
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

            # Ensure all required stickies are present
            for req_topic, req_info in self._required_stickies.items():
                if req_topic not in self._sticky_notes:
                    self._sticky_notes[req_topic] = req_info

        with self._characters_lock:
            self._characters.clear()
            chars = state.get("characters", [])
            if isinstance(chars, list):
                for char in chars:
                    if isinstance(char, dict) and "name" in char:
                        self._characters[str(char["name"])] = {
                            "name": str(char["name"]),
                            "description": str(char.get("description", "")),
                            "personality": str(char.get("personality", "")),
                            "motivation": str(char.get("motivation", "")),
                            "quirk": str(char.get("quirk", "")),
                            "voice_tags": normalize_voice_tags(char.get("voice_tags", char.get("voice_type"))),
                        }

        with self._plot_beats_lock:
            beats = state.get("plot_beats", [])
            self._plot_beats = [
                {"plot_beat": str(item.get("plot_beat") or "").strip()}
                for item in beats
                if isinstance(item, dict) and item.get("plot_beat")
            ] if isinstance(beats, list) else []
        self._last_scene_reaction = state.get("last_scene_reaction", {}) if isinstance(state.get("last_scene_reaction", {}), dict) else {}

    def reload_from_session_state(self) -> None:
        """Reload story planning state from session state manager if present."""
        if not self.canvas_state_service or not self.theater_id:
            return
        try:
            state_mgr = None
            if hasattr(self.canvas_state_service, "get"):
                state_mgr = self.canvas_state_service.get(self.theater_id)
            elif (
                hasattr(self.canvas_state_service, "get_story_planning_state")
                or hasattr(self.canvas_state_service, "get_sticky_notes")
                or hasattr(self.canvas_state_service, "get_named_elements")
            ):
                state_mgr = self.canvas_state_service

            if state_mgr and hasattr(state_mgr, "get_story_planning_state"):
                sp_state = state_mgr.get_story_planning_state()
                if sp_state:
                    self.import_story_planning_state(sp_state)
                    return

            if state_mgr and hasattr(state_mgr, "get_sticky_notes"):
                saved_notes = state_mgr.get_sticky_notes()
                if saved_notes:
                    with self._sticky_notes_lock:
                        self._sticky_notes.clear()
                        if isinstance(saved_notes, list):
                            for elem in saved_notes:
                                if isinstance(elem, dict):
                                    topic = str(elem.get("topic", elem.get("name", "")))[:MAX_STICKY_NOTE_TOPIC_CHARS]
                                    info = str(elem.get("info", elem.get("content", "")))[:MAX_STICKY_NOTE_INFO_CHARS]
                                    if topic and info:
                                        self._sticky_notes[topic] = info
                        elif isinstance(saved_notes, dict):
                            for k, v in saved_notes.items():
                                self._sticky_notes[str(k)[:MAX_STICKY_NOTE_TOPIC_CHARS]] = str(v)[:MAX_STICKY_NOTE_INFO_CHARS]
                        for req_topic, req_info in self._required_stickies.items():
                            if req_topic not in self._sticky_notes:
                                self._sticky_notes[req_topic] = req_info
                    return

            if state_mgr and hasattr(state_mgr, "get_named_elements"):
                saved_elements = state_mgr.get_named_elements()
                if saved_elements:
                    with self._sticky_notes_lock:
                        self._sticky_notes.clear()
                        if isinstance(saved_elements, list):
                            for elem in saved_elements:
                                if isinstance(elem, dict):
                                    topic = str(elem.get("topic", elem.get("name", "")))[:MAX_STICKY_NOTE_TOPIC_CHARS]
                                    info = str(elem.get("info", elem.get("content", "")))[:MAX_STICKY_NOTE_INFO_CHARS]
                                    if topic and info:
                                        self._sticky_notes[topic] = info
                        elif isinstance(saved_elements, dict):
                            for k, v in saved_elements.items():
                                self._sticky_notes[str(k)[:MAX_STICKY_NOTE_TOPIC_CHARS]] = str(v)[:MAX_STICKY_NOTE_INFO_CHARS]
                        for req_topic, req_info in self._required_stickies.items():
                            if req_topic not in self._sticky_notes:
                                self._sticky_notes[req_topic] = req_info
                    return
        except Exception as e:
            logger.warning(
                "[StoryPlanningTools] Failed to reload story planning state from session state: %s",
                e,
            )

    def save_to_session_state(self) -> None:
        """Persist story planning snapshot to session state."""
        if not self.canvas_state_service or not self.theater_id:
            return
        try:
            state_mgr = None
            if hasattr(self.canvas_state_service, "get"):
                state_mgr = self.canvas_state_service.get(self.theater_id)
            elif (
                hasattr(self.canvas_state_service, "set_story_planning_state")
                or hasattr(self.canvas_state_service, "set_sticky_notes")
                or hasattr(self.canvas_state_service, "set_named_elements")
            ):
                state_mgr = self.canvas_state_service

            if state_mgr and hasattr(state_mgr, "set_story_planning_state"):
                state_mgr.set_story_planning_state(self.export_story_planning_state())
            elif state_mgr and hasattr(state_mgr, "set_sticky_notes"):
                state_mgr.set_sticky_notes(self.get_present_sticky_notes())
            elif state_mgr and hasattr(state_mgr, "set_named_elements"):
                state_mgr.set_named_elements(self.get_present_elements())
        except Exception as e:
            logger.warning(
                "[StoryPlanningTools] Failed to save story planning state to session state: %s",
                e,
            )

    def _format_story_log(self, plot_beats: List[Dict[str, Any]]) -> str:
        """Format active characters and durable plot beats into a clean debug string."""
        lines = []
        chars = self.get_present_characters()
        if chars:
            lines.append("  Active Characters:")
            for c in chars:
                desc_str = f" ({c['description']})" if c.get("description") else ""
                voice_part = f" | Voice Tags: {', '.join(c['voice_tags'])}" if c.get("voice_tags") else ""
                lines.append(
                    f"    - {c['name']}{desc_str}: Personality: {c.get('personality', 'N/A')} | "
                    f"Motivation: {c.get('motivation', 'N/A')} | Quirk: {c.get('quirk', 'N/A')}{voice_part}"
                )

        if not plot_beats:
            lines.append("  (No plot beats available)")
        else:
            lines.append("  Upcoming Plot Beats:")
            for index, node in enumerate(plot_beats):
                lines.append(f"    [Beat {index}] {node.get('plot_beat', '')}")
        return "\n".join(lines)

    def _log_story_update(self, plot_beats: List[Dict[str, Any]], source: str) -> None:
        """Emit formatted logger output for durable story-state tracking."""
        theater = self.theater_id or "default"
        logger.debug(
            "[StoryPlanningTools] Plot beats active (source=%s, theater=%s, count=%d):\n%s",
            source,
            theater,
            len(plot_beats),
            self._format_story_log(plot_beats),
        )

    def get_tools(self) -> List[Any]:
        """Return bound tool methods exposed to the agent based on configuration."""
        if self.adventure_mode:
            # In Adventure Mode the planner owns story context and progression;
            # the live agent only relays player input to this authority.
            return [self.process_user_action]
        return [self.update_sticky_note, self.clear_scene]


    @property
    def is_voice_input_detected(self) -> bool:
        """Return True if voice input has been detected since the last processed action."""
        with self._voice_input_lock:
            return self._voice_input_detected

    def record_voice_input(self) -> None:
        """Mark that user voice input was detected, re-enabling process_user_action."""
        with self._voice_input_lock:
            self._voice_input_detected = True
            logger.debug("[StoryPlanningTools] Voice input detected; process_user_action is re-enabled.")

    def reset_lore_call_counts(self) -> None:
        """Reset per-turn read_lore and search_lore invocation counters and lore activity."""
        with self._read_lore_lock:
            self._read_lore_calls_this_turn = 0
        with self._search_lore_lock:
            self._search_lore_calls_this_turn = 0
        with self._lore_activity_lock:
            self._lore_activity_this_turn = []

    def _record_lore_activity(self, activity_type: str, target: str, summary: str = "", **kwargs: Any) -> None:
        """Record a lore document read, search, or preload event for the active turn."""
        with self._lore_activity_lock:
            entry = {
                "type": activity_type,
                "document": target,
                "summary": summary or target,
                **kwargs,
            }
            if not any(e.get("type") == activity_type and e.get("document") == target for e in self._lore_activity_this_turn):
                self._lore_activity_this_turn.append(entry)

    def get_lore_docs_browsed_this_turn(self) -> List[str]:
        """Return unique list of lore document paths browsed, searched, or preloaded during this turn."""
        with self._lore_activity_lock:
            docs: List[str] = []
            seen = set()
            for item in self._lore_activity_this_turn:
                doc = item.get("document")
                if doc and doc not in seen and not doc.startswith("("):
                    seen.add(doc)
                    docs.append(doc)
                for m in item.get("matching_documents", []):
                    if m not in seen:
                        seen.add(m)
                        docs.append(m)
                for m in item.get("matched_documents", []):
                    if m not in seen:
                        seen.add(m)
                        docs.append(m)
            return docs

    def get_lore_activity_this_turn(self) -> List[Dict[str, Any]]:
        """Return list of lore activities recorded during this turn."""
        with self._lore_activity_lock:
            return list(self._lore_activity_this_turn)

    @logged_tool_call
    def read_lore(self, document: str = "") -> str:
        """List or read the theater's text-only lore documents.

        Call without ``document`` to list available ``.txt`` paths. Call again
        with one listed relative path to read it before planning characters or
        scenes. This tool is read-only and cannot access files outside ``lore/``.
        At most 3 read_lore calls are allowed per story planning turn.
        Once the limit is reached, you must proceed to return your final scene reaction.
        """
        with self._read_lore_lock:
            if self._read_lore_calls_this_turn >= MAX_READ_LORE_CALLS_PER_TURN:
                logger.warning(
                    "[StoryPlanningTools] read_lore call limit reached (%d/%d) for theater=%s",
                    self._read_lore_calls_this_turn,
                    MAX_READ_LORE_CALLS_PER_TURN,
                    self.theater_id,
                )
                return (
                    f"Error: Maximum read_lore call limit ({MAX_READ_LORE_CALLS_PER_TURN}) reached for this turn. "
                    "You cannot read additional lore. Finalize and return the scene reaction now."
                )
            self._read_lore_calls_this_turn += 1
            call_count = self._read_lore_calls_this_turn

        limit_note = (
            f"\n\n[Note: You have reached the maximum limit of {MAX_READ_LORE_CALLS_PER_TURN} read_lore calls for this turn. "
            "Do not call read_lore again. Proceed immediately to finalize and return the scene reaction JSON.]"
            if call_count >= MAX_READ_LORE_CALLS_PER_TURN
            else ""
        )

        if not self.theater_id:
            logger.warning("[StoryPlanningTools] Read lore called without active theater.")
            return "No theater is active, so no lore documents are available." + limit_note
        if not document:
            documents = self.theater_manager.get_lore_documents(self.theater_id)
            listed_documents = documents[:MAX_LORE_DOCUMENTS_LISTED]
            omission = (
                f"\n[+{len(documents) - len(listed_documents)} additional documents omitted.]"
                if len(documents) > len(listed_documents)
                else ""
            )
            self._record_lore_activity("list", "(all lore documents)", summary=f"Listed {len(documents)} lore files")
            logger.debug(
                "[StoryPlanningTools] Listing lore documents for theater=%s (total=%d, listed=%d)",
                self.theater_id,
                len(documents),
                len(listed_documents),
            )
            return (
                ("Available lore documents:\n" + "\n".join(f"- {path}" for path in listed_documents) + omission
                if documents
                else "No lore documents are available for this theater.")
                + limit_note
            )
        clean_doc = str(document or "").strip().replace("\\", "/")
        if not clean_doc.lower().endswith(".txt"):
            prefix = clean_doc.rstrip("/") + "/"
            matching = [
                doc for doc in self.theater_manager.get_lore_documents(self.theater_id)
                if doc.startswith(prefix)
            ]
            if matching:
                listed_matching = matching[:MAX_LORE_DOCUMENTS_LISTED]
                omission = (
                    f"\n[+{len(matching) - len(listed_matching)} additional documents omitted.]"
                    if len(matching) > len(listed_matching)
                    else ""
                )
                self._record_lore_activity(
                    "read_dir",
                    clean_doc,
                    summary=f"Browsed lore directory '{clean_doc}' ({len(matching)} matching docs)",
                    matching_documents=matching[:10],
                )
                logger.debug(
                    "[StoryPlanningTools] Reading lore directory '%s' for theater=%s (matching=%d)",
                    clean_doc,
                    self.theater_id,
                    len(matching),
                )
                return (
                    f"Lore documents in '{clean_doc}':\n"
                    + "\n".join(f"- {path}" for path in listed_matching)
                    + omission
                    + limit_note
                )
        try:
            content = self.theater_manager.read_lore_document(self.theater_id, clean_doc)
        except ValueError as error:
            logger.warning(
                "[StoryPlanningTools] Failed to read lore document '%s' for theater=%s: %s",
                clean_doc,
                self.theater_id,
                error,
            )
            return f"Error: {error}" + limit_note
        self._record_lore_activity(
            "read_file",
            clean_doc,
            summary=f"Read lore document '{clean_doc}'",
            excerpt=content[:300] if content else "",
        )
        logger.debug(
            "[StoryPlanningTools] Read lore document '%s' for theater=%s (chars=%d)",
            clean_doc,
            self.theater_id,
            len(content),
        )
        excerpt = content[:MAX_LORE_DOCUMENT_CONTEXT_CHARS]
        suffix = "\n[Excerpt truncated for planner context.]" if len(content) > len(excerpt) else ""
        return f"Lore document: {clean_doc}\n\n{excerpt}{suffix}{limit_note}"


    @staticmethod
    def _extract_snippet(content: str, terms: List[str], max_len: int = 150) -> str:
        """Extract a short representative snippet around the first occurrence of any query term."""
        content_lower = content.lower()
        earliest_idx = -1
        for t in terms:
            idx = content_lower.find(t)
            if idx != -1:
                if earliest_idx == -1 or idx < earliest_idx:
                    earliest_idx = idx

        if earliest_idx == -1:
            snippet = content[:max_len].replace("\n", " ").strip()
            return f'"{snippet}..."' if len(content) > max_len else f'"{snippet}"'

        start = max(0, earliest_idx - 30)
        end = min(len(content), earliest_idx + max_len - 30)
        snippet = content[start:end].replace("\n", " ").strip()
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(content) else ""
        return f'"{prefix}{snippet}{suffix}"'

    def _get_lore_corpus_index(self) -> Dict[str, Dict[str, Any]]:
        """Return cached pre-tokenized lore corpus index for the active theater."""
        with self._lore_cache_lock:
            if self._lore_index_cache is not None:
                return self._lore_index_cache

            if not self.theater_id:
                return {}

            documents = self.theater_manager.get_lore_documents(self.theater_id)
            corpus_index: Dict[str, Dict[str, Any]] = {}
            for doc_path in documents:
                try:
                    content = self.theater_manager.read_lore_document(self.theater_id, doc_path)
                except Exception:
                    continue
                tokens = re.findall(r"\w+", content.lower())
                counts: Dict[str, int] = {}
                for t in tokens:
                    counts[t] = counts.get(t, 0) + 1
                corpus_index[doc_path] = {
                    "content": content,
                    "tokens": tokens,
                    "counts": counts,
                    "length": len(tokens),
                }
            self._lore_index_cache = corpus_index
            return self._lore_index_cache

    def clear_lore_cache(self) -> None:
        """Clear cached lore search index and cached query results."""
        with self._lore_cache_lock:
            self._lore_index_cache = None
            self._search_query_cache.clear()

    @logged_tool_call
    def search_lore(self, query: str) -> str:
        """Perform a keyword search (TF-IDF based) across all text lore documents in the theater.

        Returns lore filenames ranked by relevance score along with matching excerpts.
        Use this tool to find relevant documents before reading them with read_lore.
        At most 3 search_lore calls are allowed per story planning turn.
        Once the limit is reached, you must proceed to return your final scene reaction.
        """
        with self._search_lore_lock:
            if self._search_lore_calls_this_turn >= MAX_SEARCH_LORE_CALLS_PER_TURN:
                logger.warning(
                    "[StoryPlanningTools] search_lore call limit reached (%d/%d) for theater=%s",
                    self._search_lore_calls_this_turn,
                    MAX_SEARCH_LORE_CALLS_PER_TURN,
                    self.theater_id,
                )
                return (
                    f"Error: Maximum search_lore call limit ({MAX_SEARCH_LORE_CALLS_PER_TURN}) reached for this turn. "
                    "You cannot search additional lore. Finalize and return the scene reaction now."
                )
            self._search_lore_calls_this_turn += 1
            call_count = self._search_lore_calls_this_turn

        limit_note = (
            f"\n\n[Note: You have reached the maximum limit of {MAX_SEARCH_LORE_CALLS_PER_TURN} search_lore calls for this turn. "
            "Do not call search_lore again. Proceed immediately to finalize and return the scene reaction JSON.]"
            if call_count >= MAX_SEARCH_LORE_CALLS_PER_TURN
            else ""
        )

        if not self.theater_id:
            logger.warning("[StoryPlanningTools] Search lore called without active theater.")
            return "No theater is active, so no lore documents are available." + limit_note

        clean_query = str(query or "").strip()
        if not clean_query:
            return "Error: Search query cannot be empty." + limit_note

        query_terms = re.findall(r"\w+", clean_query.lower())
        if not query_terms:
            return "Error: Search query must contain alphanumeric keywords." + limit_note

        query_key = " ".join(query_terms)
        with self._lore_cache_lock:
            cached_result = self._search_query_cache.get(query_key)
        if cached_result is not None:
            logger.debug(
                "[StoryPlanningTools] Using cached search_lore result for query='%s' in theater=%s",
                clean_query,
                self.theater_id,
            )
            return cached_result + limit_note

        corpus_index = self._get_lore_corpus_index()
        if not corpus_index:
            return "No lore documents are available for this theater." + limit_note

        total_docs = len(corpus_index)
        unique_query_terms = list(dict.fromkeys(query_terms))
        df: Dict[str, int] = {}
        for t in unique_query_terms:
            df[t] = sum(1 for doc in corpus_index.values() if doc["counts"].get(t, 0) > 0)

        idf: Dict[str, float] = {}
        for t in unique_query_terms:
            idf[t] = math.log((total_docs + 1) / (df[t] + 1)) + 1.0

        scores: List[Tuple[str, float, str]] = []
        for doc_path, doc_info in corpus_index.items():
            length = doc_info["length"]
            if length == 0:
                continue
            counts = doc_info["counts"]
            score = 0.0
            for t in query_terms:
                tf = counts.get(t, 0) / length
                score += tf * idf[t]

            if score > 0:
                snippet = self._extract_snippet(doc_info["content"], unique_query_terms)
                scores.append((doc_path, score, snippet))

        scores.sort(key=lambda item: item[1], reverse=True)

        if not scores:
            res_str = f"No matching lore documents found for query: '{clean_query}'"
            self._record_lore_activity("search", clean_query, summary=f"Searched lore for '{clean_query}' (0 matches)", matched_documents=[])
        else:
            results_lines = [f"Lore search results for query '{clean_query}':"]
            matched_docs = [doc_path for doc_path, score, snippet in scores[:10]]
            for doc_path, score, snippet in scores[:10]:
                results_lines.append(f"- {doc_path} (score: {score:.4f})")
                if snippet:
                    results_lines.append(f"  Snippet: {snippet}")
            res_str = "\n".join(results_lines)
            self._record_lore_activity(
                "search",
                clean_query,
                summary=f"Searched lore for '{clean_query}' ({len(scores)} matches)",
                matched_documents=matched_docs[:5],
            )

        with self._lore_cache_lock:
            self._search_query_cache[query_key] = res_str

        logger.debug(
            "[StoryPlanningTools] Searched lore for query='%s' in theater=%s (matches=%d)",
            clean_query,
            self.theater_id,
            len(scores),
        )

        return res_str + limit_note

    @logged_tool_call
    def roll_dice(
        self,
        sides: int = 20,
        count: int = 1,
        modifier: int = 0,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Roll dice to resolve a genuinely uncertain story outcome.

        Use this when chance should decide how well a player attempt works,
        such as a risky feat, a contest, or an unpredictable discovery. The
        returned values are authoritative for the current scene delta. Do not
        roll for ordinary actions with obvious consequences. Supports 1-10
        dice, 2-1000 sides per die, and a -1000 to +1000 modifier. High is a GOOD,
        well aligned outcome for what the player is attempting. Low is BAD, but should
        still lead to interesting situations even if misaligned with the player goal.
        """
        try:
            clean_sides = int(sides)
            clean_count = int(count)
            clean_modifier = int(modifier)
        except (TypeError, ValueError):
            return {"error": "sides, count, and modifier must be integers."}
        if not 2 <= clean_sides <= 1_000:
            return {"error": "sides must be between 2 and 1000."}
        if not 1 <= clean_count <= 10:
            return {"error": "count must be between 1 and 10."}
        if not -1_000 <= clean_modifier <= 1_000:
            return {"error": "modifier must be between -1000 and 1000."}

        rolls = [secrets.randbelow(clean_sides) + 1 for _ in range(clean_count)]
        total = sum(rolls) + clean_modifier
        result = {
            "notation": f"{clean_count}d{clean_sides}" + (f"{clean_modifier:+d}" if clean_modifier else ""),
            "rolls": rolls,
            "modifier": clean_modifier,
            "total": total,
        }
        if str(reason or "").strip():
            result["reason"] = str(reason).strip()[:300]
        if self.canvas_state_service:
            self.canvas_state_service.set_tool_activity(
                "dice", active=True, theater_id=self.theater_id, recent_seconds=2.5,
            )
        logger.debug(
            "[StoryPlanningTools] Dice roll (theater=%s, reason=%s): %s",
            self.theater_id or "default",
            result.get("reason", "unspecified"),
            result,
        )
        return result

    def _get_lore_context(self) -> str:
        """List top-level lore documents and directories for the planner context, automatically expanding files prefixed with 'read'."""
        documents = self.theater_manager.get_lore_documents(self.theater_id)
        if not documents:
            return ""
        top_level_files: list[str] = []
        top_level_dirs: set[str] = set()
        expanded_files: list[str] = []
        for doc in documents:
            parts = doc.split("/")
            filename = parts[-1]
            if filename.lower().startswith("read") or doc.lower().startswith("read"):
                content = self.theater_manager.read_lore_document(self.theater_id, doc)
                self._record_lore_activity(
                    "preloaded",
                    doc,
                    summary=f"Preloaded premise lore document '{doc}'",
                )
                if len(content) > MAX_LORE_DOCUMENT_CONTEXT_CHARS:
                    content = (
                        content[:MAX_LORE_DOCUMENT_CONTEXT_CHARS]
                        + "\n[Excerpt truncated for planner context.]"
                    )
                expanded_files.append(f"- {doc}:\n{content}")
                if len(parts) > 1:
                    top_level_dirs.add(parts[0] + "/")
            else:
                if len(parts) == 1:
                    top_level_files.append(doc)
                else:
                    top_level_dirs.add(parts[0] + "/")
        items = (
            [f"- {d} (directory)" for d in sorted(top_level_dirs)]
            + [f"- {f}" for f in sorted(top_level_files)]
            + expanded_files
        )
        if not items:
            items = [f"- {doc}" for doc in documents[:MAX_LORE_DOCUMENTS_LISTED]]
        return "\n".join(items[:MAX_LORE_DOCUMENTS_LISTED])

    @logged_tool_call
    def lookup_character(self, query: str = "") -> str:
        """List all session characters or search for a character by name or trait.

        Call without ``query`` (or with an empty query) to list all known character names and roles in this session.
        Call with a character name or search term to look up detailed profiles (personality, motivation, quirk, voice tags).
        """
        clean_query = str(query or "").strip()
        with self._characters_lock:
            if not self._characters:
                return "No characters have been created in this session yet."

            if not clean_query:
                lines = [f"Known characters in this session ({len(self._characters)} total):"]
                for name, char in self._characters.items():
                    desc = f" - {char.get('description')}" if char.get("description") else ""
                    pers = f" | Personality: {char.get('personality')}" if char.get("personality") else ""
                    lines.append(f"- {name}{desc}{pers}")
                return "\n".join(lines)

            query_lower = clean_query.lower()
            matches = []

            for name, char in self._characters.items():
                if name.lower() == query_lower:
                    matches.insert(0, char)
                elif query_lower in name.lower():
                    matches.append(char)
                else:
                    combined = f"{char.get('description', '')} {char.get('personality', '')} {char.get('motivation', '')} {char.get('quirk', '')}".lower()
                    if query_lower in combined:
                        matches.append(char)

            if not matches:
                names_list = ", ".join(self._characters.keys())
                return f"No character matching '{clean_query}' found. Known characters: {names_list}"

            seen_names = set()
            unique_matches = []
            for m in matches:
                if m["name"] not in seen_names:
                    seen_names.add(m["name"])
                    unique_matches.append(m)

            lines = [f"Found {len(unique_matches)} character(s) matching '{clean_query}':"]
            for char in unique_matches:
                desc_str = f" ({char['description']})" if char.get("description") else ""
                voice_part = f" | Voice Tags: {', '.join(char['voice_tags'])}" if char.get("voice_tags") else ""
                lines.append(
                    f"- {char['name']}{desc_str}:\n"
                    f"  Personality: {char.get('personality', 'N/A')}\n"
                    f"  Motivation: {char.get('motivation', 'N/A')}\n"
                    f"  Quirk: {char.get('quirk', 'N/A')}{voice_part}"
                )
            return "\n".join(lines)

    @logged_tool_call
    def generate_character_profile(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: List[str] | str | None = None,
    ) -> Dict[str, Any]:
        """Return an enriched character profile without changing CanvasState.

        This is the planner's sole input tool: it helps draft a character that
        the planner may include in its returned scene delta. Persistence happens
        only when that delta is committed.
        """
        clean_name = str(name or "").strip()
        clean_desc = str(description or "").strip()
        clean_pers = str(personality or "").strip()
        clean_motiv = str(motivation or "").strip()
        clean_quirk = str(quirk or "").strip()
        clean_tags = normalize_voice_tags(voice_tags)

        if not clean_name:
            return {"error": "Character name cannot be empty."}

        # Assign random quirk from QuirkGeneratorService if not specified
        if not clean_quirk:
            active_quirks = [
                c.get("quirk", "") for c in self.get_present_characters() if c.get("quirk")
            ]
            quirk_service = get_quirk_generator_service()
            clean_quirk = quirk_service.get_random_quirk(exclude=active_quirks)

        # If personality, motivation, or voice_tags are missing, generate them via text provider
        if not clean_pers or not clean_motiv or not clean_tags:
            elements = self.get_present_elements()
            prompt = _CHARACTER_GEN_PROMPT_TEMPLATE.render(
                name=clean_name,
                description=clean_desc,
                elements=elements,
            ).strip()

            request = TextResponseRequest(
                prompt=prompt,
                system_instruction=(
                    "You are a character design assistant for an adventure story. "
                    "Respond ONLY with valid JSON."
                ),
                temperature=0.7,
            )
            response = self.text_response_provider.generate(request)
            resp_text = str(getattr(response, "text", "") or "").strip()
            if resp_text.startswith("```"):
                resp_text = re.sub(r"^```(?:json)?\s*", "", resp_text)
                resp_text = re.sub(r"\s*```$", "", resp_text)
            try:
                parsed = json.loads(resp_text)
            except Exception:
                parsed = {}
            if isinstance(parsed, dict):
                if not clean_pers:
                    clean_pers = str(parsed.get("personality") or "").strip()
                if not clean_motiv:
                    clean_motiv = str(parsed.get("motivation") or "").strip()
                if not clean_tags:
                    clean_tags = normalize_voice_tags(parsed.get("voice_tags", parsed.get("voice_type")))

        return {
            "name": clean_name,
            "description": clean_desc,
            "personality": clean_pers,
            "motivation": clean_motiv,
            "quirk": clean_quirk,
            "voice_tags": clean_tags,
        }

    @with_cooldown("generating character")
    def generate_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: List[str] | str | None = None,
    ) -> str:
        """Generate or update a persisted character outside a planner turn."""
        char_data = self.generate_character_profile(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
        )
        if "error" in char_data:
            return f"Error: {char_data['error']}"
        clean_name = char_data["name"]

        with self._characters_lock:
            self._characters[clean_name] = char_data
            self._characters.move_to_end(clean_name)

        self.save_to_session_state()

        self._log_story_update(self.get_plot_beats(), source="character_added")

        tags_str = f". Voice Tags: {', '.join(char_data['voice_tags'])}" if char_data.get("voice_tags") else ""
        return (
            f"Created/Updated character '{clean_name}'. Personality: {char_data['personality']}. "
            f"Motivation: {char_data['motivation']}. Quirk: {char_data['quirk']}{tags_str}."
        )

    @logged_tool_call
    def update_sticky_note(self, topic: str, info: str) -> str:
        """Insert or replace one sticky note in the current scene.

        Can be used to note objects, characters, locations, lore, relationships, or state within a scene.
        """
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

        self.save_to_session_state()

        action = "Updated" if is_update else "Added"
        logger.debug(
            "[StoryPlanningTools] %s sticky note '%s' (theater=%s). Active sticky notes count: %d",
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
        """Backward-compatible alias for update_sticky_note."""
        return self.update_sticky_note(topic=name, info=content)

    @logged_tool_call
    def clear_scene(self) -> str:
        """Clear dynamic sticky notes and characters from the current scene before starting a new one. Required sticky notes are preserved."""
        with self._sticky_notes_lock:
            non_required_keys = [k for k in self._sticky_notes if k not in self._required_stickies]
            for k in non_required_keys:
                del self._sticky_notes[k]
            # Ensure all required stickies remain present
            for req_topic, req_info in self._required_stickies.items():
                if req_topic not in self._sticky_notes:
                    self._sticky_notes[req_topic] = req_info
            cleared_notes_count = len(non_required_keys)

        with self._characters_lock:
            char_count = len(self._characters)
            self._characters.clear()

        with self._plot_beats_lock:
            self._plot_beats.clear()
        self._last_scene_reaction = {}
        self._publish_scene_dialogue([])
        self._publish_narration("")

        self.save_to_session_state()

        logger.debug(
            "[StoryPlanningTools] Cleared %d sticky note(s) (preserved %d required), %d character(s), and plot beats (theater=%s).",
            cleared_notes_count,
            len(self._required_stickies),
            char_count,
            self.theater_id or "default",
        )

        if char_count > 0:
            return f"Cleared {cleared_notes_count} sticky note(s) and {char_count} character(s) from the scene."
        return f"Cleared {cleared_notes_count} sticky note(s) from the scene."

    def get_present_sticky_notes(self) -> list[dict[str, str]]:
        """Return a stable snapshot of active sticky notes."""
        with self._sticky_notes_lock:
            return [
                {
                    "topic": topic[:MAX_STICKY_NOTE_TOPIC_CHARS],
                    "info": info[:MAX_STICKY_NOTE_INFO_CHARS],
                    "name": topic[:MAX_STICKY_NOTE_TOPIC_CHARS],
                    "content": info[:MAX_STICKY_NOTE_INFO_CHARS],
                }
                for topic, info in list(self._sticky_notes.items())[-self.max_sticky_notes:]
            ]

    def get_present_elements(self) -> list[dict[str, str]]:
        """Return a stable snapshot for live-agent observability."""
        return self.get_present_sticky_notes()

    def get_required_sticky_notes(self) -> list[str]:
        """Return the list of required sticky note keys that cannot be dropped."""
        return list(self._required_stickies.keys())

    def get_present_characters(self) -> list[dict[str, Any]]:
        """Return a stable snapshot of active characters with personalities and motivations."""
        with self._characters_lock:
            return [
                dict(char)
                for char in list(self._characters.values())[-self.max_active_characters:]
            ]

    def get_plot_beats(self) -> List[Dict[str, str]]:
        """Return the durable, non-consumable plot beats."""
        with self._plot_beats_lock:
            return list(self._plot_beats)

    @staticmethod
    def _clean_dialogue(dialogue: Any) -> List[Dict[str, str]]:
        if not isinstance(dialogue, list):
            return []
        cleaned: List[Dict[str, str]] = []
        for item in dialogue[:3]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            kind = str(item.get("kind") or "speech").strip().lower()
            cleaned.append({
                "speaker": str(item.get("speaker") or "Narrator").strip()[:80],
                "text": text[:500],
                "kind": kind if kind in {"speech", "thought"} else "speech",
            })
        return cleaned

    def _publish_scene_dialogue(self, dialogue: List[Dict[str, str]]) -> None:
        """Persist dialogue for the canvas without making the live agent own it."""
        if not self.canvas_state_service or not self.theater_id:
            return
        try:
            state_mgr = (
                self.canvas_state_service.get(self.theater_id)
                if hasattr(self.canvas_state_service, "get")
                else self.canvas_state_service
            )
            if hasattr(state_mgr, "set_scene_dialogue"):
                state_mgr.set_scene_dialogue(dialogue)
        except Exception as exc:
            logger.warning("[StoryPlanningTools] Failed to publish scene dialogue: %s", exc)

    def _publish_narration(self, narration: str) -> None:
        """Persist the planner's narration for the canvas."""
        if not self.canvas_state_service or not self.theater_id:
            return
        try:
            state_mgr = (
                self.canvas_state_service.get(self.theater_id)
                if hasattr(self.canvas_state_service, "get")
                else self.canvas_state_service
            )
            if hasattr(state_mgr, "set_narration"):
                state_mgr.set_narration(narration)
        except Exception as exc:
            logger.warning("[StoryPlanningTools] Failed to publish narration: %s", exc)

    def _apply_planner_character_updates(self, updates: Any) -> List[Dict[str, str]]:
        """Execute planner-selected character manifestations without live-agent control."""
        if not isinstance(updates, list):
            return []
        manifested: List[Dict[str, str]] = []
        for update in updates[:2]:
            if not isinstance(update, dict):
                continue
            name = str(update.get("name") or "").strip()
            if not name:
                continue
            # This is an internal planner action, not a live-agent tool call;
            # it must not be rejected because a previous character was just
            # manifested during the same scene resolution.
            type(self).generate_character.__wrapped__(
                self,
                name=name,
                description=str(update.get("description") or "").strip(),
                personality=str(update.get("personality") or "").strip(),
                motivation=str(update.get("motivation") or "").strip(),
                quirk=str(update.get("quirk") or "").strip(),
                voice_tags=normalize_voice_tags(update.get("voice_tags", update.get("voice_type"))),
            )
            manifested.extend([
                character for character in self.get_present_characters()
                if character["name"] == name
            ])
        return manifested

    def _build_compaction_config(self) -> Optional[EventsCompactionConfig]:
        """Build ADK event compaction configuration for the story planner."""
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
        """Build context window compression config for the ADK runner."""
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

    def _build_planner_instruction(self, ctx: Any = None) -> str:
        """Render the dynamic scene reaction instruction for the planner agent."""
        with self._characters_lock:
            total_chars = len(self._characters)
        snapshot = {
            "elements": self.get_present_elements(),
            "characters": self.get_present_characters(),
            "total_characters": total_chars,
            "plot_beats": [node.get("plot_beat", "") for node in self.get_plot_beats()],
            "nodes_ahead": self.nodes_ahead,
            "style": self.style,
        }
        lore_context = self._get_lore_context()
        planner_context = build_story_context_prompt(
            elements=snapshot["elements"],
            characters=snapshot["characters"],
            reused_nodes=[
                {"node_index": index, "plot_beat": beat}
                for index, beat in enumerate(snapshot["plot_beats"])
            ],
            total_characters=snapshot["total_characters"],
        )
        return build_scene_reaction_prompt(
            context=planner_context,
            style=self.style,
            nodes_ahead=self.nodes_ahead,
            lore_context=lore_context,
            max_sticky_notes=self.max_sticky_notes,
        )

    def _create_planner_agent(self) -> Agent:
        """Create the persistent ADK planner agent instance."""
        generate_content_config = None
        if self.thinking_budget is not None:
            generate_content_config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    thinking_budget=self.thinking_budget
                )
            )
        return Agent(
            name="story_planner",
            description="Authoritative planner for interactive story turns.",
            model=VertexGemini(
                model=self.planner_model,
                project_id=self.vertex_project,
                location=self.vertex_location,
            ),
            instruction=self._build_planner_instruction,
            tools=[
                self.search_lore,
                self.read_lore,
                self.lookup_character,
                self.generate_character_profile,
                self.roll_dice,
                self.update_sticky_note,
            ],
            output_schema=SceneReaction,
            output_key="scene_reaction",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            generate_content_config=generate_content_config,
        )

    def restart_planner_agent(self) -> None:
        """Reset and recreate the ADK planner agent, app, and runner."""
        logger.info(
            "[StoryPlanningTools] Resetting and restarting story planner agent for theater=%s",
            self.theater_id,
        )
        self._planner_runner = None
        self._planner_agent = None
        self._planner_app = None
        self._get_or_create_planner_runner()

    @property
    def is_action_in_flight(self) -> bool:
        """Return True if a user action resolution is currently in flight."""
        return self.is_in_flight("process_user_action")

    def _get_or_create_planner_runner(self) -> Runner:
        """Return or lazily instantiate the reusable ADK runner for the planner session."""
        if self._planner_runner is None:
            self._planner_agent = self._create_planner_agent()
            self._planner_app = App(
                name="narratron_story_planner",
                root_agent=self._planner_agent,
                events_compaction_config=self.compaction_config,
            )
            self._planner_runner = Runner(
                app=self._planner_app,
                session_service=self.session_service,
                auto_create_session=True,
            )
        return self._planner_runner

    def _run_planner_agent(self, user_action: str, nudge: str = "") -> Dict[str, Any]:
        """Run one ADK planner turn resumed from the session."""
        self.reset_lore_call_counts()
        runner = self._get_or_create_planner_runner()
        session_id = self.session_id
        theater = self.theater_id or "default"
        logger.debug(
            "[StoryPlanningTools] Running planner agent (theater=%s): user_action=%r, nudge=%r",
            theater,
            user_action,
            nudge,
        )

        async def run_turn() -> Dict[str, Any]:
            final_text = ""
            run_config = (
                RunConfig(context_window_compression=self._run_compression_config)
                if self._run_compression_config
                else None
            )
            prompt_input = user_action
            if nudge:
                prompt_input = f"{user_action}\n\n[Live Agent Nudge to Accommodate]: {nudge}"
            async for event in runner.run_async(
                user_id="story_planner",
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt_input)]),
                run_config=run_config,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = "".join(part.text or "" for part in event.content.parts)
            session = await self.session_service.get_session(
                app_name="narratron_story_planner",
                user_id="story_planner",
                session_id=session_id,
            )
            stored_reaction = (session.state or {}).get("scene_reaction") if session else None
            try:
                if isinstance(stored_reaction, BaseModel):
                    reaction = stored_reaction.model_dump()
                elif isinstance(stored_reaction, dict):
                    reaction = stored_reaction
                elif isinstance(stored_reaction, str):
                    reaction = json.loads(stored_reaction)
                else:
                    reaction = json.loads(final_text) if final_text else {}
            except json.JSONDecodeError as exc:
                raise ValueError("Story planner returned invalid structured output.") from exc
            if not isinstance(reaction, dict):
                raise ValueError("Story planner returned an invalid scene reaction.")
            if not reaction:
                state_keys = sorted((session.state or {}).keys()) if session else []
                raise ValueError(
                    "Story planner returned no scene delta "
                    f"(final_response_chars={len(final_text)}, state_keys={state_keys})."
                )
            try:
                scene_delta = SceneReaction.model_validate(reaction)
            except Exception as exc:
                raise ValueError("Story planner returned an invalid scene delta.") from exc
            if len(scene_delta.plot_beats) != self.nodes_ahead:
                raise ValueError("Story planner must return the complete plot-beat buffer in its scene delta.")
            return scene_delta.model_dump()

        try:
            return asyncio.run(
                asyncio.wait_for(run_turn(), timeout=self.user_action_timeout_seconds)
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.error(
                "[StoryPlanningTools] Story planner timed out after %.1f seconds for user action: %r",
                self.user_action_timeout_seconds,
                user_action,
            )
            self.restart_planner_agent()
            return {
                "error": (
                    f"Story planner timed out after {self.user_action_timeout_seconds} seconds. "
                    "The story planner agent was killed and restarted. "
                    "Please re-try the action or guide the story."
                )
            }

    @with_cooldown(
        "resolving another player action",
        duration=lambda tools: tools.get_user_action_cooldown_seconds(),
    )
    def process_user_action(self, user_action: str, nudge: str = "") -> Dict[str, Any]:
        """Queue a non-blocking authoritative resolution of an orator action.

        Args:
            user_action: The player's submitted action or speech in the interactive story.
            nudge: An optional suggestion, event, or direction that the live agent wishes to
                introduce to the story, which the story planner is meant to accommodate.

        The final reaction is delivered through ``on_scene_reaction``. Callers
        receive immediately so a slow planner cannot stall the Live session.
        Only one user action resolution call may be in flight at a time.
        """
        with self._voice_input_lock:
            if self.require_voice_input and not self._voice_input_detected:
                return {
                    "error": (
                        "Cannot process user action: No voice input from the orator was detected. "
                        "Please wait for the orator to speak before submitting an action."
                    )
                }

        action = str(user_action or "").strip()
        clean_nudge = str(nudge or "").strip()
        if not action:
            return {"error": "User action cannot be empty."}
        if len(action) > MAX_PLAYER_ACTION_CHARS:
            return {"error": f"Player actions must be {MAX_PLAYER_ACTION_CHARS} characters or fewer."}
        if len(clean_nudge) > MAX_NUDGE_CHARS:
            return {"error": f"Nudge must be {MAX_NUDGE_CHARS} characters or fewer."}

        if not self.acquire_in_flight("process_user_action"):
            return {
                "error": (
                    "A user action is already being processed. Please wait for the "
                    "'[Story Planner Result]' notification before submitting another action."
                )
            }

        with self._voice_input_lock:
            if self.require_voice_input:
                self._voice_input_detected = False

        def resolve_and_notify() -> None:
            result = None
            try:
                result = self._resolve_user_action(action, nudge=clean_nudge)
            except Exception as exc:
                logger.exception("[StoryPlanningTools] Scene reaction failed")
                result = {"error": f"Story planner failed: {exc}"}
            finally:
                self.release_in_flight("process_user_action")

            callback = self.on_scene_reaction
            if callback and result is not None:
                try:
                    callback(result)
                except Exception:
                    logger.exception("[StoryPlanningTools] Scene reaction callback failed")

        import threading
        threading.Thread(target=resolve_and_notify, daemon=True).start()
        return {"status": "processing", "message": "Story planner is resolving the action."}

    def get_user_action_cooldown_seconds(self) -> float:
        """Scale the next action cooldown from the previous completed response."""
        extra_seconds = math.ceil(
            self._last_action_response_word_count / self.action_cooldown_words_per_second
        )
        return min(self.action_cooldown_max_seconds, self.action_cooldown_base_seconds + extra_seconds)

    def _resolve_user_action(self, action: str, nudge: str = "") -> Dict[str, Any]:
        """Run and commit one planner turn in a background worker."""
        if not self.adventure_mode:
            return {"error": "Adventure Mode is not enabled for this theater."}
        if self.nodes_ahead <= 0:
            raise ValueError("nodes_ahead must be positive.")
        parsed = self._run_planner_agent(action, nudge=nudge)
        if not isinstance(parsed, dict):
            raise ValueError("Story planner must return a JSON object.")
        if "error" in parsed:
            return parsed

        manifested_characters = self._apply_planner_character_updates(parsed.get("character_updates"))
        narration = str(
            parsed.get("narration") or "The scene shifts in response to your action."
        ).strip()[:MAX_NARRATION_CHARS]
        dialogue = self._clean_dialogue(parsed.get("dialogue"))
        plot_beats = self._normalize_plot_beats(parsed.get("plot_beats", []))
        if len(plot_beats) != self.nodes_ahead:
            raise ValueError("Story planner must produce the complete plot-beat buffer.")
        with self._plot_beats_lock:
            self._plot_beats = plot_beats
        result = {
            "narration": narration,
            "dialogue": dialogue,
            "manifested_characters": manifested_characters,
            "plot_beats": list(plot_beats),
            "lore_activity": self.get_lore_activity_this_turn(),
            "lore_docs_browsed": self.get_lore_docs_browsed_this_turn(),
        }
        self._last_scene_reaction = result
        self._last_action_response_word_count = self._count_response_words(result)
        last_action_time = self._last_call_times.get("process_user_action")
        if last_action_time is not None:
            remaining = self.get_user_action_cooldown_seconds() - (time.time() - last_action_time)
            self._schedule_cooldown_timer("process_user_action", remaining)
        self._publish_scene_dialogue(dialogue)
        self._publish_narration(narration)
        self.save_to_session_state()
        self._log_story_update(plot_beats, source="user_action")
        scene_name = str(parsed.get("scene_label") or "").strip()
        reference_images = parsed.get("reference_images") or []
        logger.debug(
            "[StoryPlanningTools] Scene name: %s | Reference images: %s",
            scene_name or "(unspecified)",
            reference_images,
        )
        callback = self.on_story_plan_completed
        if callback:
            try:
                callback()
            except Exception:
                logger.exception("[StoryPlanningTools] Story-planning usage callback failed")
        return result

    @staticmethod
    def _normalize_plot_beats(raw_beats: Any) -> List[Dict[str, str]]:
        """Convert typed scene-delta beats to the persisted CanvasState shape."""
        if not isinstance(raw_beats, list):
            return []
        normalized: List[Dict[str, str]] = []
        for item in raw_beats:
            if isinstance(item, str):
                beat = item.strip()
            elif isinstance(item, dict):
                beat = str(item.get("plot_beat") or "").strip()
            else:
                beat = ""
            if beat:
                normalized.append({"plot_beat": beat[:MAX_PLOT_BEAT_CHARS]})
        return normalized

    @staticmethod
    def _count_response_words(result: Dict[str, Any]) -> int:
        """Count visible planner response words for the next action cooldown."""
        text_parts = [
            str(result.get("narration") or ""),
        ]
        text_parts.extend(
            str(item.get("text") or "")
            for item in result.get("dialogue", [])
            if isinstance(item, dict)
        )
        text_parts.extend(
            str(item.get("plot_beat") or "")
            for item in result.get("plot_beats", [])
            if isinstance(item, dict)
        )
        return len(re.findall(r"\b[\w'-]+\b", " ".join(text_parts)))
