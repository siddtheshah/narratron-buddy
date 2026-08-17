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
from functools import cached_property
import json
import logging
import os
import re
import secrets
import math
from threading import Lock
import time
import asyncio
from typing import Any, Callable, List, Dict, Optional

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
DEFAULT_MAX_NAMED_ELEMENTS = 5
DEFAULT_STORY_PLANNING_STYLE = "balanced, consequence-driven, and player-agency-first"
DEFAULT_THINKING_BUDGET = 1024
MAX_STORY_PLANNING_STYLE_CHARS = 500
MAX_PLAYER_ACTION_CHARS = 2_000
MAX_LORE_DOCUMENT_CONTEXT_CHARS = 12_000
MAX_CONTEXT_FIELD_CHARS = 500
MAX_PLOT_BEAT_CHARS = 500
MAX_NARRATION_CHARS = 2_000
MAX_NODES_AHEAD = 10
MAX_NAMED_ELEMENTS = 10
MAX_ACTIVE_CHARACTERS = 5
MAX_LORE_DOCUMENTS_LISTED = 100
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
Current scene elements:
{% if elements -%}
{% for elem in elements -%}
- {{ elem.name }}: {{ elem.content }}
{% endfor -%}
{% else -%}
(No active scene elements)
{% endif -%}

Generate a compelling personality description, core motivation, and voice tags for this character in an adventure story experience.
The only supported voice tags are 'male' or 'female'.
Return ONLY a JSON object with keys 'personality' (string), 'motivation' (string), and 'voice_tags' (list of strings with 'male' or 'female')."""
)

_STORY_CONTEXT_PROMPT_TEMPLATE = Template(
    """Current scene elements:
{% if not elements -%}
(No active named elements)
{% else -%}
{% for elem in elements -%}
- {{ elem.name }}: {{ elem.content }}
{% endfor -%}
{% endif -%}

Active characters, personalities, motivations & distinct quirks:
{% if not characters -%}
(No active character motivations set)
{% else -%}
{% for char in characters -%}
- {{ char.name }}: Personality: {{ char.personality }} | Motivation: {{ char.motivation }} | Distinct Quirk: {{ char.quirk }}{% if char.voice_tags %} | Voice Tags: {{ char.voice_tags | join(', ') }}{% endif %}{% if char.description %} (Description: {{ char.description }}){% endif %}
{% endfor -%}
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
"""You are the authoritative narrative script engine for an interactive story.
Resolve the consequences of the player's submitted action, decide when NPCs should manifest or change, and update future beats.
Use a 'yes, and' improv posture: accept the player's attempted action as meaningful, preserve its premise when it fits the established fiction, and move the story forward with an interesting consequence, opportunity, complication, or escalation.
Do not stonewall with a flat refusal or erase the action; when it conflicts with established facts, honor its intent through the nearest plausible consequence instead.
The player/orator and any character they control are outside your control: their submitted words are historical input, not dialogue to continue, revise, narrate as, or attribute to them.
Never invent the player's actions, speech, thoughts, feelings, decisions, or a response on their behalf.
Write narration only about the world and the consequences of the submitted action.
Dialogue may be spoken only by NPCs; never emit dialogue for a speaker called Player, User, Orator, You, or for the player-controlled character.
Character updates are for NPCs only.
The live agent is only a relay; do not give it choices, tool instructions, or control of the plot.
Respond ONLY with valid JSON.

{{ context }}
{% if lore_context -%}

Available theater lore (top-level documents and directories):
{{ lore_context }}
{% endif -%}

Story-planning style: {{ style }}

Apply the stated style to pacing, narration, opposition, and consequences, while still following every system instruction.
Style is not permission to take over player agency, negate meaningful actions, or force arbitrary outcomes.
The user action is immutable player input. Do not repeat it as dialogue or convert it into an authored turn for the player.
Follow the 'yes, and' posture from your system instruction.
When an action's outcome is genuinely uncertain, call roll_dice and use the returned result to decide the consequence; do not fabricate a roll.
Keep responses focused: narration should normally be 20-50 words that also describe the visual resolution and immediate outcome of the character's action rather than just scenery alone; dialogue should have at most three short lines.
Return one complete scene delta that leaves the player's next action, speech, thoughts, and choices entirely open.
Include exactly {{ nodes_ahead }} general plot beats in plot_beats; they must not predict, request, or prescribe player actions.
When available theater lore documents or directories are listed above, ground the narrative, characters, factions, and setting in that established theater lore. Do not call `browse_lore` to list or read files on ordinary turns unless a specific unlisted lore fact is strictly necessary to resolve the immediate action.
Include character_updates only for NPCs that should enter or materially change; never create or update the player-controlled character. When creating or updating characters, define voice_tags as a list containing 'male' or 'female' to guide speech synthesis.
You may call generate_character_profile to enrich a proposed NPC, then include its returned profile in character_updates.
Those tools only provide information: the scene delta is the sole source of changes.
`dialogue` is optional and must contain NPC speech only.
Then return the scene reaction.

# Scene Labeling
Ensure the scene has a label. THe location name is generally a good choice. Keep using that label until a major shift occurs.

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
        description="Voice classification tags ('male' or 'female') to guide speech synthesis.",
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
) -> str:
    """Render shared scene, character, and plot context for every planner task."""
    return _STORY_CONTEXT_PROMPT_TEMPLATE.render(
        elements=elements or [],
        characters=characters or [],
        reused_nodes=reused_nodes or [],
    ).strip()


def build_scene_reaction_prompt(
    context: str,
    style: str,
    nodes_ahead: int,
    lore_context: str = "",
) -> str:
    """Render the authoritative scene reaction instruction for the planner agent."""
    return _SCENE_REACTION_PROMPT_TEMPLATE.render(
        context=context,
        style=style,
        nodes_ahead=nodes_ahead,
        lore_context=lore_context,
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
        self._elements: OrderedDict[str, str] = OrderedDict()
        self._elements_lock = Lock()
        self._characters: OrderedDict[str, Dict[str, str]] = OrderedDict()
        self._characters_lock = Lock()
        self._plot_beats: List[Dict[str, str]] = []
        self._plot_beats_lock = Lock()
        self._last_scene_reaction: Dict[str, Any] = {}
        self.theater_manager = theater_manager
        self.text_response_provider = text_response_provider

        self.nodes_ahead: int = max(1, min(int(self.config.get("nodes_ahead", 3)), MAX_NODES_AHEAD))
        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        configured_style = self.config.get("style", DEFAULT_STORY_PLANNING_STYLE)
        self.style: str = (
            str(configured_style).strip()[:MAX_STORY_PLANNING_STYLE_CHARS]
            or DEFAULT_STORY_PLANNING_STYLE
        )
        self.max_named_elements: int = max(
            1,
            min(
                int(self.config.get("max_named_elements", DEFAULT_MAX_NAMED_ELEMENTS)),
                MAX_NAMED_ELEMENTS,
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
        self._last_action_response_word_count: int = 0
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

        initial_elements = self.config.get("initial_elements", {})
        if isinstance(initial_elements, dict):
            for k, v in initial_elements.items():
                if len(self._elements) >= self.max_named_elements:
                    break
                self._elements[str(k)] = str(v)
        elif isinstance(initial_elements, list):
            for elem in initial_elements:
                if len(self._elements) >= self.max_named_elements:
                    break
                if isinstance(elem, dict) and "name" in elem and "content" in elem:
                    self._elements[str(elem["name"])] = str(elem["content"])

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
        with self._elements_lock:
            elements_list = [
                {"name": name, "content": content}
                for name, content in self._elements.items()
            ]
        with self._characters_lock:
            chars_list = [dict(c) for c in self._characters.values()]
        with self._plot_beats_lock:
            plot_beats = list(self._plot_beats)

        return {
            "named_elements": elements_list,
            "characters": chars_list,
            "plot_beats": plot_beats,
            "last_scene_reaction": dict(self._last_scene_reaction),
        }

    def import_story_planning_state(self, state: Dict[str, Any]) -> None:
        """Import full story planning state from a dict."""
        if not isinstance(state, dict):
            return

        with self._elements_lock:
            self._elements.clear()
            elements = state.get("named_elements", [])
            if isinstance(elements, list):
                for elem in elements:
                    if isinstance(elem, dict) and "name" in elem and "content" in elem:
                        self._elements[str(elem["name"])] = str(elem["content"])
            elif isinstance(elements, dict):
                for k, v in elements.items():
                    self._elements[str(k)] = str(v)

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
            elif hasattr(self.canvas_state_service, "get_story_planning_state") or hasattr(self.canvas_state_service, "get_named_elements"):
                state_mgr = self.canvas_state_service

            if state_mgr and hasattr(state_mgr, "get_story_planning_state"):
                sp_state = state_mgr.get_story_planning_state()
                if sp_state:
                    self.import_story_planning_state(sp_state)
                    return

            if state_mgr and hasattr(state_mgr, "get_named_elements"):
                saved_elements = state_mgr.get_named_elements()
                if saved_elements:
                    with self._elements_lock:
                        self._elements.clear()
                        if isinstance(saved_elements, list):
                            for elem in saved_elements:
                                if isinstance(elem, dict) and "name" in elem and "content" in elem:
                                    self._elements[str(elem["name"])] = str(elem["content"])
                        elif isinstance(saved_elements, dict):
                            for k, v in saved_elements.items():
                                self._elements[str(k)] = str(v)
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
                or hasattr(self.canvas_state_service, "set_named_elements")
            ):
                state_mgr = self.canvas_state_service

            if state_mgr and hasattr(state_mgr, "set_story_planning_state"):
                state_mgr.set_story_planning_state(self.export_story_planning_state())
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
        return [self.update_or_insert_named_element, self.clear_scene]


    @logged_tool_call
    def browse_lore(self, document: str = "") -> str:
        """List or read the theater's text-only lore documents.

        Call without ``document`` to list available ``.txt`` paths. Call again
        with one listed relative path to read it before planning characters or
        scenes. This tool is read-only and cannot access files outside ``lore/``.
        """
        if not self.theater_id:
            return "No theater is active, so no lore documents are available."
        if not document:
            documents = self.theater_manager.get_lore_documents(self.theater_id)
            listed_documents = documents[:MAX_LORE_DOCUMENTS_LISTED]
            omission = (
                f"\n[+{len(documents) - len(listed_documents)} additional documents omitted.]"
                if len(documents) > len(listed_documents)
                else ""
            )
            return (
                "Available lore documents:\n" + "\n".join(f"- {path}" for path in listed_documents) + omission
                if documents
                else "No lore documents are available for this theater."
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
                return (
                    f"Lore documents in '{clean_doc}':\n"
                    + "\n".join(f"- {path}" for path in listed_matching)
                    + omission
                )
        try:
            content = self.theater_manager.read_lore_document(self.theater_id, clean_doc)
        except ValueError as error:
            return f"Error: {error}"
        excerpt = content[:MAX_LORE_DOCUMENT_CONTEXT_CHARS]
        suffix = "\n[Excerpt truncated for planner context.]" if len(content) > len(excerpt) else ""
        return f"Lore document: {clean_doc}\n\n{excerpt}{suffix}"

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
        """List top-level lore documents and directories for the planner context."""
        documents = self.theater_manager.get_lore_documents(self.theater_id)
        if not documents:
            return ""
        top_level_files: list[str] = []
        top_level_dirs: set[str] = set()
        for doc in documents:
            parts = doc.split("/")
            if len(parts) == 1:
                top_level_files.append(doc)
            else:
                top_level_dirs.add(parts[0] + "/")
        items = [f"- {d} (directory)" for d in sorted(top_level_dirs)] + [f"- {f}" for f in sorted(top_level_files)]
        if not items:
            items = [f"- {doc}" for doc in documents[:MAX_LORE_DOCUMENTS_LISTED]]
        return "\n".join(items[:MAX_LORE_DOCUMENTS_LISTED])

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

    @with_cooldown("updating scene elements")
    def update_or_insert_named_element(self, name: str, content: str) -> str:
        """Insert or replace one named element in the current scene.

        Can be used to take objects, characters, locations, and relationships within a scene.
        """
        clean_name = str(name or "").strip()
        clean_content = str(content or "").strip()
        if not clean_name:
            return "Error: Named element name cannot be empty."
        if not clean_content:
            return "Error: Named element content cannot be empty."

        with self._elements_lock:
            is_update = clean_name in self._elements
            if is_update:
                self._elements[clean_name] = clean_content
                self._elements.move_to_end(clean_name)
            else:
                if len(self._elements) >= self.max_named_elements:
                    self._elements.popitem(last=False)
                self._elements[clean_name] = clean_content

        self.save_to_session_state()

        action = "Updated" if is_update else "Added"
        logger.debug(
            "[StoryPlanningTools] %s named element '%s' (theater=%s). Active elements count: %d",
            action,
            clean_name,
            self.theater_id or "default",
            len(self._elements),
        )

        return f"{action} named element '{clean_name}'."

    @logged_tool_call
    def clear_scene(self) -> str:
        """Clear every named element and character from the current scene before starting a new one."""
        with self._elements_lock:
            elem_count = len(self._elements)
            self._elements.clear()

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
            "[StoryPlanningTools] Cleared %d scene element(s), %d character(s), and plot beats (theater=%s).",
            elem_count,
            char_count,
            self.theater_id or "default",
        )

        if char_count > 0:
            return f"Cleared {elem_count} named element(s) and {char_count} character(s) from the scene."
        return f"Cleared {elem_count} named element(s) from the scene."

    def get_present_elements(self) -> list[dict[str, str]]:
        """Return a stable snapshot for live-agent observability."""
        with self._elements_lock:
            return [
                {"name": name[:MAX_CONTEXT_FIELD_CHARS], "content": content[:MAX_CONTEXT_FIELD_CHARS]}
                for name, content in list(self._elements.items())[-self.max_named_elements:]
            ]

    def get_present_characters(self) -> list[dict[str, Any]]:
        """Return a stable snapshot of active characters with personalities and motivations."""
        with self._characters_lock:
            return [
                dict(char)
                for char in list(self._characters.values())[-MAX_ACTIVE_CHARACTERS:]
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
        snapshot = {
            "elements": self.get_present_elements(),
            "characters": self.get_present_characters(),
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
        )
        return build_scene_reaction_prompt(
            context=planner_context,
            style=self.style,
            nodes_ahead=self.nodes_ahead,
            lore_context=lore_context,
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
            tools=[self.browse_lore, self.generate_character_profile, self.roll_dice],
            output_schema=SceneReaction,
            output_key="scene_reaction",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            generate_content_config=generate_content_config,
        )

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

    def _run_planner_agent(self, user_action: str) -> Dict[str, Any]:
        """Run one ADK planner turn resumed from the session."""
        runner = self._get_or_create_planner_runner()
        session_id = self.session_id

        async def run_turn() -> Dict[str, Any]:
            final_text = ""
            run_config = (
                RunConfig(context_window_compression=self._run_compression_config)
                if self._run_compression_config
                else None
            )
            async for event in runner.run_async(
                user_id="story_planner",
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=user_action)]),
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

        import asyncio
        return asyncio.run(run_turn())

    @with_cooldown(
        "resolving another player action",
        duration=lambda tools: tools.get_user_action_cooldown_seconds(),
    )
    def process_user_action(self, user_action: str) -> Dict[str, Any]:
        """Queue a non-blocking authoritative resolution of an orator action.

        The final reaction is delivered through ``on_scene_reaction``. Callers
        receive immediately so a slow planner cannot stall the Live session.
        """
        action = str(user_action or "").strip()
        if not action:
            return {"error": "User action cannot be empty."}
        if len(action) > MAX_PLAYER_ACTION_CHARS:
            return {"error": f"Player actions must be {MAX_PLAYER_ACTION_CHARS} characters or fewer."}

        def resolve_and_notify() -> None:
            try:
                result = self._resolve_user_action(action)
            except Exception as exc:
                logger.exception("[StoryPlanningTools] Scene reaction failed")
                result = {"error": f"Story planner failed: {exc}"}
            callback = self.on_scene_reaction
            if callback:
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

    def _resolve_user_action(self, action: str) -> Dict[str, Any]:
        """Run and commit one planner turn in a background worker."""
        if not self.adventure_mode:
            return {"error": "Adventure Mode is not enabled for this theater."}
        if self.nodes_ahead <= 0:
            raise ValueError("nodes_ahead must be positive.")

        parsed = self._run_planner_agent(action)
        if not isinstance(parsed, dict):
            raise ValueError("Story planner must return a JSON object.")

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
