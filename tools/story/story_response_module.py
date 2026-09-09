"""Story response module: fast turn responder and immediate scene state owner."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import json
import logging
import math
import os
import re
import threading
from threading import Lock
import time
from typing import Any, Callable, Dict, List, Literal, Optional

from jinja2 import Template
from pydantic import BaseModel, Field
from google.adk.agents import Agent
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.runners import RunConfig, Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from tools.base_tool import BaseTools, logged_tool_call, with_cooldown
from providers import TextResponseProvider
from tools.story.character_manager import CharacterManager
from tools.story.lore_library import LoreLibrary
from tools.story.story_planning_module import (
    StoryPlanningModule,
    VertexGemini,
    DEFAULT_COMPACTION_TRIGGER_TOKENS,
    DEFAULT_COMPACTION_TARGET_TOKENS,
    DEFAULT_STORY_PLANNING_STYLE,
    STORY_LOG_CONTEXT_LINES,
)

logger = logging.getLogger(__name__)

DEFAULT_THINKING_BUDGET = 1024
USER_ACTION_TIMEOUT_SECONDS = 25.0
VOICE_INPUT_LOG_THROTTLE_SECONDS = 5.0
MAX_STORY_PLANNING_STYLE_CHARS = 500
MAX_PLAYER_ACTION_CHARS = 2_000
MAX_NUDGE_CHARS = 1_000
MAX_NARRATION_CHARS = 2_000
MAX_PLANNING_SIGNAL_CHARS = 500
MAX_NAMED_ELEMENTS = 10
MAX_READ_LORE_CALLS_PER_TURN = 3
MAX_SEARCH_LORE_CALLS_PER_TURN = 3

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

"""
)

_SCENE_REACTION_PROMPT_TEMPLATE = Template(
"""# Role & Mission
You are the fast, authoritative turn responder for an interactive story.
Resolve only the immediate consequences of the player's submitted action and decide when NPCs should manifest or materially change.
A separate deep-planning agent owns long-term continuity through sticky notes. Treat those notes as authoritative planning guidance, but never modify them or create a competing long-term plan during this turn.
Respond ONLY with valid JSON conforming to the scene reaction schema.

# Story-Planning Style (User Specified)
{{ style }}
- Apply the stated style to pacing, narration, opposition, and consequences, while still following every system instruction.
- Style is not permission to take over player agency, negate meaningful actions, or force arbitrary outcomes.
- Follow the 'yes, and' posture from your system instruction.

# Core Improv & Player Agency Principles
- Use a 'yes, and' improv posture: accept the player's attempted action as meaningful, preserve its premise when it fits the established fiction, and move the story forward with an interesting consequence, opportunity, complication, or escalation.
- Do not stonewall with a flat refusal or erase the action; when it conflicts with established facts, honor its intent through the nearest plausible consequence instead.
- If a live agent nudge is provided, accommodate and incorporate that suggested direction, event, or element into the story resolution or NPC responses where appropriate, while still respecting player agency and established fiction.
- The player/orator and any character they control are outside your control: their submitted words are historical input, not dialogue to continue, revise, narrate as, or attribute to them.
- Never invent the player's actions, speech, thoughts, feelings, decisions, or a response on their behalf.
- The user action is immutable player input. Do not repeat it as dialogue or convert it into an authored turn for the player.
- The live agent is only a relay; do not give it choices, tool instructions, or control of the plot.

# Player Death, Lethal Consequences, Death Hints & Restarts
- Player death, lethal consequences, execution, disintegration, and fatal outcomes ARE EXPLICITLY PERMITTED when the player's choices, suicidal recklessness, combat defeat, or deliberate provocation warrant it under the fiction, lore, or rules.
- Do NOT protect the player with artificial plot armor, contrived near-misses, or miraculous rescues when their actions call for a fatal outcome. If a player drinks lethal poison, provokes a deadly warlord point-blank, or fails an unescapable ultimatum, execute the consequence faithfully.
- When player death or a fatal loss state occurs, clearly narrate the demise as a definitive end.
- **Death Hint**: When player death occurs, provide a subtle, witty, or possibly cryptic hint (in the narration, NPC parting words, or environmental fallout) hinting at why they died or what tactical/leverage mistake triggered it if it wasn't obvious. The player should feel that death was a fair, foreseeable possibility rooted in the world's rules and learn a valuable lesson for their next attempt.
- If the player wishes to continue playing after death, it is a restart of the adventure from the beginning (e.g., as a fresh reset, a new incarnation, or the next candidate).

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
- **Lore Search & Reading (`search_lore`, `read_lore`)**: Ground the narrative, characters, factions, and setting in established theater lore. You may call `search_lore` to perform a keyword search across all lore files and find the most relevant documents by relevance score, and `read_lore` to read full lore documents or directories. `search_lore` and `read_lore` are capped separately: you may call search_lore at most 3 times and read_lore at most 3 times in a single turn. Once you have sufficient context, proceed immediately to return the scene reaction. If no lore is available, invent the lore freely without calling search_lore or read_lore.
- **Dice Rolling (`roll_dice`)**: When an action's outcome is genuinely uncertain, call roll_dice and use the returned result to decide the consequence; do not fabricate a roll.
- **Character Lookup (`lookup_character`)**: Call `lookup_character` to list all known session characters or search for a specific NPC by name, role, or trait to view their full profile, personality, motivation, and quirk when encountering or referencing characters created earlier in the story.
- **Character Generation (`generate_character_profile`)**: You may call generate_character_profile to enrich a proposed NPC, then include its returned profile in character_updates.
- Those tools only provide information: the scene delta is the sole source of changes.

# Scene Reaction Output Requirements
- **Narration**: Write narration only about the world and the consequences of the submitted action. Keep responses focused: narration should normally be 20-50 words that also describe the visual resolution and immediate outcome of the character's action rather than just scenery alone. Return one complete scene delta that leaves the player's next action, speech, thoughts, and choices entirely open.
- **Dialogue**: `dialogue` is optional and must contain NPC speech only (at most three short lines). Dialogue may be spoken only by NPCs; never emit dialogue for a speaker called Player, User, Orator, You, or for the player-controlled character.
- **Planning Signals**: Briefly record facts established by this resolution, threads affected, and consequences the background planning system should consider. These signals are internal and must describe what actually happened, not invent future events.
- **Character Updates**: Character updates are for NPCs only. Include character_updates only for NPCs that should enter or materially change; never create or update the player-controlled character. When creating or updating characters, define voice_tags as a list containing 'male' or 'female' to guide speech synthesis.

# Character Generation
Do not expose secret character information via the character name when creating a character. Everything else is otherwise private.
If a character is disguised, make sure you give them an alias that hides their nature, rather than using their real name.

# Scene Labeling
Ensure the scene has a label. The location name is generally a good choice. Keep using that label until a major shift occurs.

# Reference Usage
If the lore documents mention reference images for characters and images, communicate them via the reference_images field.
"""
)


class ResponseDialogue(BaseModel):
    speaker: str = "Narrator"
    text: str
    kind: str = "speech"


class ResponseCharacter(BaseModel):
    name: str
    description: Optional[str] = None
    personality: Optional[str] = None
    motivation: Optional[str] = None
    quirk: Optional[str] = None
    voice_type: Optional[str] = None
    voice_tags: Optional[List[str]] = None


class SceneReaction(BaseModel):
    narration: str = Field(description="Scene narration focusing on visual consequence and immediate narrative outcome.")
    dialogue: List[ResponseDialogue] = Field(default_factory=list, description="At most three NPC lines; never for the player.")
    manifested_characters: List[str] = Field(default_factory=list, description="Names of NPCs that entered or became prominent.")
    character_updates: List[ResponseCharacter] = Field(default_factory=list, description="NPCs added or updated.")
    planning_signals: List[str] = Field(default_factory=list, description="Factual story signals for deep planning.")
    scene_label: Optional[str] = Field(default=None, description="Current scene label/location.")
    reference_images: Optional[List[str]] = Field(default=None, description="Referenced lore images.")


class StoryLogDieRoll(BaseModel):
    sides: int
    count: int
    rolls: List[int]
    modifier: int
    total: int
    reason: Optional[str] = None

    def format_prompt_summary(self) -> str:
        mod_str = f" + {self.modifier}" if self.modifier > 0 else f" - {abs(self.modifier)}" if self.modifier < 0 else ""
        reason_str = f" for '{self.reason}'" if self.reason else ""
        return f"{self.count}d{self.sides}{mod_str}{reason_str} -> rolls: {self.rolls}, total: {self.total}"

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class StoryResponseOutput(BaseModel):
    narration: str
    dialogue: List[Dict[str, str]] = Field(default_factory=list)
    die_rolls: List[Dict[str, Any]] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class StoryLogEntry(BaseModel):
    type: Literal["user_action", "story_response", "story_plan"]
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action: Optional[str] = None
    output: Optional[StoryResponseOutput] = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


def build_story_context_prompt(
    elements: list[dict[str, str]],
    characters: list[dict[str, Any]],
    total_characters: Optional[int] = None,
) -> str:
    return _STORY_CONTEXT_PROMPT_TEMPLATE.render(
        elements=elements,
        characters=characters,
        total_characters=total_characters,
    ).strip()


def build_scene_reaction_prompt(
    context: str,
    style: str,
    lore_context: str = "",
    max_sticky_notes: int = 5,
) -> str:
    return _SCENE_REACTION_PROMPT_TEMPLATE.render(
        context=context,
        style=style,
        lore_context=lore_context,
        max_sticky_notes=max_sticky_notes,
    ).strip()


class StoryResponseModule(BaseTools):
    """Fast, authoritative turn responder with dependencies supplied by ``StoryTool``."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        text_response_provider: TextResponseProvider,
        planning_module: StoryPlanningModule,
        lore_library: LoreLibrary,
        character_manager: CharacterManager,
        session_service: InMemorySessionService,
        session_id: str,
    ):
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")
        if planning_module is None:
            raise ValueError("planning_module is required.")
        if lore_library is None:
            raise ValueError("lore_library is required.")
        if character_manager is None:
            raise ValueError("character_manager is required.")
        if session_service is None:
            raise ValueError("session_service is required.")
        if not session_id:
            raise ValueError("session_id is required.")

        super().__init__(
            theater=theater,
            canvas_manager=canvas_manager,
        )

        subconfig = self.config.get("story_planning", self.config) if "story_planning" in self.config else self.config
        self.config = subconfig if isinstance(subconfig, dict) else {}
        self.text_response_provider = text_response_provider

        self.lore_library = lore_library
        self.character_manager = character_manager

        # Lore budgets and activity belong to the immediate-response turn.
        self._read_lore_calls_this_turn = 0
        self._read_lore_lock = Lock()
        self._search_lore_calls_this_turn = 0
        self._search_lore_lock = Lock()
        self._lore_activity_this_turn: List[Dict[str, Any]] = []
        self._lore_activity_lock = Lock()

        # Dice rolls
        self._die_rolls_this_turn: List[Dict[str, Any]] = []
        self._die_rolls_lock = Lock()

        # Turn ID & Scene Reaction tracking
        self._turn_id: int = 0
        self._turn_id_lock = Lock()
        self._last_scene_reaction: Dict[str, Any] = {}
        self._last_action_response_word_count: int = 0

        # User input gate
        self.require_user_input: bool = bool(self.config.get("require_user_input", False))
        self._user_input_detected: bool = not self.require_user_input
        self._user_input_lock: Lock = Lock()
        self._last_voice_input_log_time: float = -float("inf")

        # Cooldowns
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

        # Session & ADK
        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        configured_style = self.config.get("style", DEFAULT_STORY_PLANNING_STYLE)
        self.style: str = (
            str(configured_style).strip()[:MAX_STORY_PLANNING_STYLE_CHARS]
            or DEFAULT_STORY_PLANNING_STYLE
        )
        self.session_id = str(session_id)
        self.session_service = session_service
        self.compaction_config: Optional[EventsCompactionConfig] = self._build_compaction_config()
        self._run_compression_config: Optional[types.ContextWindowCompressionConfig] = (
            self._build_run_compression_config()
        )
        self.responder_model: str = str(
            self.config.get("responder_model")
            or self.config.get("model")
            or "gemini-3.7-flash"
        )
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

        # Callbacks
        self.on_scene_reaction: Optional[Callable[[Dict[str, Any]], None]] = (
            self.config.get("on_scene_reaction")
        )
        self.on_story_response_completed: Optional[Callable[[], None]] = (
            self.config.get("on_story_response_completed")
        )

        # Story log
        self._story_log_lock = Lock()
        self._recent_story_log = self._read_recent_story_log()

        self.planning_module = planning_module

        # Fast responder runner
        self._responder_agent: Agent = self._create_responder_agent()
        self._responder_app: App = App(
            name="narratron_story_responder",
            root_agent=self._responder_agent,
            events_compaction_config=self.compaction_config,
        )
        self._responder_runner: Runner = Runner(
            app=self._responder_app,
            session_service=self.session_service,
            auto_create_session=True,
        )

        self.reload_from_session_state()

    # Sticky Notes & Deep Planning delegations to planning_module
    @property
    def max_sticky_notes(self) -> int:
        return self.planning_module.max_sticky_notes

    @max_sticky_notes.setter
    def max_sticky_notes(self, value: int) -> None:
        self.planning_module.max_sticky_notes = value

    @property
    def max_named_elements(self) -> int:
        return self.planning_module.max_named_elements

    @property
    def _sticky_notes(self) -> OrderedDict[str, str]:
        return self.planning_module._sticky_notes

    @property
    def _sticky_notes_lock(self) -> Lock:
        return self.planning_module._sticky_notes_lock

    @property
    def _elements(self) -> OrderedDict[str, str]:
        return self.planning_module._sticky_notes

    @property
    def _elements_lock(self) -> Lock:
        return self.planning_module._sticky_notes_lock

    @property
    def _deep_plan(self) -> Dict[str, Any]:
        return self.planning_module._deep_plan

    @property
    def _deep_plan_lock(self) -> Lock:
        return self.planning_module._deep_plan_lock

    @property
    def deep_planning_enabled(self) -> bool:
        return self.planning_module.deep_planning_enabled

    @logged_tool_call
    def update_sticky_note(self, topic: str, info: str) -> str:
        return self.planning_module.update_sticky_note(topic, info)

    def update_or_insert_named_element(self, name: str, content: str) -> str:
        return self.planning_module.update_or_insert_named_element(name, content)

    def get_present_sticky_notes(self) -> list[dict[str, str]]:
        return self.planning_module.get_present_sticky_notes()

    def get_present_elements(self) -> list[dict[str, str]]:
        return self.planning_module.get_present_elements()

    def get_present_structured_sticky_notes(self) -> Dict[str, Dict[str, Any]]:
        return self.planning_module.get_present_structured_sticky_notes()

    def get_required_sticky_notes(self) -> list[str]:
        return self.planning_module.get_required_sticky_notes()

    def get_deep_plan(self) -> Dict[str, Any]:
        return self.planning_module.get_deep_plan()

    def wait_for_deep_planning(
        self, timeout: float = 5.0, target_turn_id: Optional[int] = None
    ) -> bool:
        return self.planning_module.wait_for_deep_planning(timeout, target_turn_id)

    def _queue_deep_planning(
        self, turn_id: int, action: str, result: Dict[str, Any]
    ) -> threading.Thread:
        return self.planning_module.queue_deep_planning(turn_id, action, result)

    # Shared Lore delegations to lore_library
    @logged_tool_call
    def read_lore(self, document: str = "") -> str:
        with self._read_lore_lock:
            if self._read_lore_calls_this_turn >= MAX_READ_LORE_CALLS_PER_TURN:
                logger.warning(
                    "[StoryResponseModule] read_lore call limit reached (%d/%d) for theater=%s",
                    self._read_lore_calls_this_turn,
                    MAX_READ_LORE_CALLS_PER_TURN,
                    self.theater_id,
                )
                return (
                    f"Error: Maximum read_lore call limit ({MAX_READ_LORE_CALLS_PER_TURN}) "
                    "reached for this turn. You cannot read additional lore. "
                    "Finalize and return the scene reaction now."
                )
            self._read_lore_calls_this_turn += 1
            call_count = self._read_lore_calls_this_turn

        result = self.lore_library.read_lore(document)
        self._record_read_lore_activity(document, result)
        if call_count == MAX_READ_LORE_CALLS_PER_TURN:
            result += (
                f"\n\n[Note: You have reached the maximum limit of "
                f"{MAX_READ_LORE_CALLS_PER_TURN} read_lore calls for this turn. "
                "Do not call read_lore again. Proceed immediately to finalize and "
                "return the scene reaction JSON.]"
            )
        return result

    @logged_tool_call
    def search_lore(self, query: str) -> str:
        with self._search_lore_lock:
            if self._search_lore_calls_this_turn >= MAX_SEARCH_LORE_CALLS_PER_TURN:
                logger.warning(
                    "[StoryResponseModule] search_lore call limit reached (%d/%d) for theater=%s",
                    self._search_lore_calls_this_turn,
                    MAX_SEARCH_LORE_CALLS_PER_TURN,
                    self.theater_id,
                )
                return (
                    f"Error: Maximum search_lore call limit ({MAX_SEARCH_LORE_CALLS_PER_TURN}) "
                    "reached for this turn. You cannot search additional lore. "
                    "Finalize and return the scene reaction now."
                )
            self._search_lore_calls_this_turn += 1
            call_count = self._search_lore_calls_this_turn

        result = self.lore_library.search_lore(query)
        matched_documents = re.findall(r"(?m)^- (.+?) \(score:", result)
        self._record_lore_activity(
            "search",
            str(query or "").strip(),
            summary=(
                f"Searched lore for '{str(query or '').strip()}' "
                f"({len(matched_documents)} matches)"
            ),
            matched_documents=matched_documents[:5],
        )
        if call_count == MAX_SEARCH_LORE_CALLS_PER_TURN:
            result += (
                f"\n\n[Note: You have reached the maximum limit of "
                f"{MAX_SEARCH_LORE_CALLS_PER_TURN} search_lore calls for this turn. "
                "Do not call search_lore again. Proceed immediately to finalize and "
                "return the scene reaction JSON.]"
            )
        return result

    def deep_read_lore(self, document: str = "") -> str:
        return self.planning_module.deep_read_lore(document)

    def deep_search_lore(self, query: str) -> str:
        return self.planning_module.deep_search_lore(query)

    def clear_lore_cache(self) -> None:
        self.lore_library.clear_lore_cache()

    def reset_lore_call_counts(self) -> None:
        with self._read_lore_lock:
            self._read_lore_calls_this_turn = 0
        with self._search_lore_lock:
            self._search_lore_calls_this_turn = 0
        with self._lore_activity_lock:
            self._lore_activity_this_turn = []
        with self._die_rolls_lock:
            self._die_rolls_this_turn = []

    def get_lore_docs_browsed_this_turn(self) -> List[str]:
        with self._lore_activity_lock:
            docs: List[str] = []
            seen = set()
            for item in self._lore_activity_this_turn:
                candidates = [item.get("document")]
                candidates.extend(item.get("matching_documents", []))
                candidates.extend(item.get("matched_documents", []))
                for document in candidates:
                    if document and not document.startswith("(") and document not in seen:
                        seen.add(document)
                        docs.append(document)
            return docs

    def get_lore_activity_this_turn(self) -> List[Dict[str, Any]]:
        with self._lore_activity_lock:
            return list(self._lore_activity_this_turn)

    def _get_lore_context(self, *, record_activity: bool = True) -> str:
        context = self.lore_library.get_lore_context()
        if record_activity:
            for document in self.theater.lore_documents():
                filename = document.rsplit("/", 1)[-1]
                if filename.lower().startswith("read") or document.lower().startswith("read"):
                    self._record_lore_activity(
                        "preloaded",
                        document,
                        summary=f"Preloaded premise lore document '{document}'",
                    )
        return context

    def _record_lore_activity(self, activity_type: str, target: str, summary: str = "", **kwargs: Any) -> None:
        with self._lore_activity_lock:
            entry = {
                "type": activity_type,
                "document": target,
                "summary": summary or target,
                **kwargs,
            }
            if not any(
                item.get("type") == activity_type and item.get("document") == target
                for item in self._lore_activity_this_turn
            ):
                self._lore_activity_this_turn.append(entry)

    def _record_read_lore_activity(self, document: str, result: str) -> None:
        clean_document = str(document or "").strip().replace("\\", "/")
        if not clean_document:
            documents = self.theater.lore_documents()
            self._record_lore_activity(
                "list",
                "(all lore documents)",
                summary=f"Listed {len(documents)} lore files",
            )
        elif result.startswith("Lore documents in '"):
            prefix = clean_document.rstrip("/") + "/"
            matching = [
                item for item in self.theater.lore_documents() if item.startswith(prefix)
            ]
            self._record_lore_activity(
                "read_dir",
                clean_document,
                summary=(
                    f"Browsed lore directory '{clean_document}' "
                    f"({len(matching)} matching docs)"
                ),
                matching_documents=matching[:10],
            )
        elif result.startswith("Lore document:"):
            self._record_lore_activity(
                "read_file",
                clean_document,
                summary=f"Read lore document '{clean_document}'",
                excerpt=result.partition("\n\n")[2][:300],
            )

    # Input detection & Rate limiting
    @property
    def is_user_input_detected(self) -> bool:
        with self._user_input_lock:
            return self._user_input_detected

    def record_user_input(self) -> None:
        with self._user_input_lock:
            self._user_input_detected = True
            now = time.monotonic()
            if now - self._last_voice_input_log_time >= VOICE_INPUT_LOG_THROTTLE_SECONDS:
                self._last_voice_input_log_time = now
                logger.debug("[StoryResponseModule] User input detected; process_user_action is re-enabled.")

    @property
    def is_action_in_flight(self) -> bool:
        return self.is_in_flight("process_user_action")

    def get_user_action_cooldown_seconds(self) -> float:
        extra_seconds = math.ceil(
            self._last_action_response_word_count / self.action_cooldown_words_per_second
        )
        return min(self.action_cooldown_max_seconds, self.action_cooldown_base_seconds + extra_seconds)

    # Exposed Agent tools
    def get_tools(self) -> List[Any]:
        if self.adventure_mode:
            return [self.process_user_action]
        return [self.update_sticky_note]

    # Dice Rolling
    def reset_die_roll_counts(self) -> None:
        with self._die_rolls_lock:
            self._die_rolls_this_turn = []

    def get_die_rolls_this_turn(self) -> List[Dict[str, Any]]:
        with self._die_rolls_lock:
            return [dict(r) for r in self._die_rolls_this_turn]

    @logged_tool_call
    def roll_dice(
        self,
        sides: int = 20,
        count: int = 1,
        modifier: int = 0,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Roll dice to resolve a genuinely uncertain story outcome."""
        safe_sides = max(2, min(100, int(sides)))
        safe_count = max(1, min(10, int(count)))
        safe_modifier = max(-100, min(100, int(modifier)))
        clean_reason = str(reason or "").strip()[:100]

        import random
        rolls = [random.randint(1, safe_sides) for _ in range(safe_count)]
        total = sum(rolls) + safe_modifier

        result = {
            "sides": safe_sides,
            "count": safe_count,
            "rolls": rolls,
            "modifier": safe_modifier,
            "total": total,
            "reason": clean_reason,
        }
        with self._die_rolls_lock:
            self._die_rolls_this_turn.append(result)

        logger.debug(
            "[StoryResponseModule] Dice roll (theater=%s, reason=%s): %s",
            self.theater_id or "default",
            clean_reason or "general",
            result,
        )
        return result

    # Character Management
    @property
    def _characters(self) -> OrderedDict[str, Dict[str, Any]]:
        return self.character_manager._characters

    @property
    def _characters_lock(self) -> Lock:
        return self.character_manager._characters_lock

    @property
    def max_active_characters(self) -> int:
        return self.character_manager.max_active_characters

    @max_active_characters.setter
    def max_active_characters(self, value: int) -> None:
        self.character_manager.max_active_characters = value

    def get_present_characters(self) -> list[dict[str, Any]]:
        return self.character_manager.get_present_characters()

    @logged_tool_call
    def lookup_character(self, query: str = "") -> str:
        return self.character_manager.lookup_character(query)

    @logged_tool_call
    def generate_character_profile(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Any = None,
    ) -> Dict[str, Any]:
        return self.character_manager.generate_character_profile(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
        )

    def generate_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Any = None,
    ) -> str:
        return self.character_manager.generate_character(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
        )

    @logged_tool_call
    def clear_scene(self) -> str:
        """Remove characters from the current scene while preserving durable story context."""
        char_count = self.character_manager.clear_scene()
        logger.debug(
            "[StoryResponseModule] Cleared %d character(s); preserved sticky notes and story context (theater=%s).",
            char_count,
            self.theater_id or "default",
        )
        return f"Cleared {char_count} character(s) from the scene; sticky notes and story context were preserved."

    # Story Log
    def _read_recent_story_log(self) -> List[StoryLogEntry]:
        if not self.theater or not self.theater_id:
            return []
        try:
            if hasattr(self.theater, "read_output_file_lines"):
                raw_lines = self.theater.read_output_file_lines("story_log.jsonl")
            elif hasattr(self.theater, "output_dir"):
                path = self.theater.output_dir() / "story_log.jsonl"
                if path.is_file():
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        raw_lines = f.readlines()
                else:
                    raw_lines = []
            else:
                raw_lines = []
            entries: List[StoryLogEntry] = []
            for line in raw_lines[-STORY_LOG_CONTEXT_LINES:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(StoryLogEntry.model_validate_json(line))
                except Exception as e:
                    logger.warning("[StoryResponseModule] Failed to validate story log line: %s", e)
            return entries
        except Exception as exc:
            logger.warning("[StoryResponseModule] Failed to read theater story log: %s", exc)
            return []

    def _append_story_log_entry(self, entry: StoryLogEntry | Dict[str, Any]) -> Optional[StoryLogEntry]:
        if not isinstance(entry, StoryLogEntry):
            try:
                entry = StoryLogEntry.model_validate(entry)
            except Exception as exc:
                logger.warning("[StoryResponseModule] Invalid StoryLogEntry: %s", exc)
                return None

        with self._story_log_lock:
            self._recent_story_log.append(entry)
            if len(self._recent_story_log) > STORY_LOG_CONTEXT_LINES:
                self._recent_story_log = self._recent_story_log[-STORY_LOG_CONTEXT_LINES:]

        if self.theater and self.theater_id:
            try:
                if hasattr(self.theater, "append_output_file"):
                    self.theater.append_output_file("story_log.jsonl", entry.model_dump_json() + "\n")
                elif hasattr(self.theater, "output_dir"):
                    out_path = self.theater.output_dir() / "story_log.jsonl"
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_path, "a", encoding="utf-8") as f:
                        f.write(entry.model_dump_json() + "\n")
            except Exception:
                logger.exception("[StoryResponseModule] Failed to append theater story log")
        return entry

    def _format_recent_story_log(self) -> str:
        with self._story_log_lock:
            entries = list(self._recent_story_log)
        lines: List[str] = []
        for entry in entries:
            if entry.type == "user_action":
                lines.append(f"Player: {entry.action}")
            elif entry.type == "story_plan" and entry.output:
                for roll in entry.output.die_rolls:
                    lines.append(f"Roll: {roll.get('count', 1)}d{roll.get('sides', 20)} -> {roll.get('total', 0)}")
                if entry.output.narration:
                    lines.append(f"Narration: {entry.output.narration}")
                for dlg in entry.output.dialogue:
                    speaker = dlg.get("speaker") if isinstance(dlg, dict) else getattr(dlg, "speaker", "")
                    text = dlg.get("text") if isinstance(dlg, dict) else getattr(dlg, "text", "")
                    if text:
                        lines.append(f"Dialogue — {speaker}: {text}")
        return "\n".join(lines)

    # ADK Responder Agent
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

    def _build_responder_instruction(self, ctx: Any = None) -> str:
        snapshot = {
            "elements": self.get_present_elements(),
            "characters": self.get_present_characters(),
            "total_characters": self.character_manager.count(),
            "style": self.style,
        }
        lore_context = self._get_lore_context()
        responder_context = build_story_context_prompt(
            elements=snapshot["elements"],
            characters=snapshot["characters"],
            total_characters=snapshot["total_characters"],
        )
        recent_story_log = self._format_recent_story_log()
        if recent_story_log:
            responder_context += (
                f"\n\nRecent Story Log (last {STORY_LOG_CONTEXT_LINES} entries):\n"
                f"{recent_story_log}"
            )
        return build_scene_reaction_prompt(
            context=responder_context,
            style=self.style,
            lore_context=lore_context,
            max_sticky_notes=self.max_sticky_notes,
        )

    def _create_responder_agent(self) -> Agent:
        generate_content_config = None
        if self.thinking_budget is not None:
            generate_content_config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    thinking_budget=self.thinking_budget
                )
            )
        return Agent(
            name="story_responder",
            description="Authoritative responder for interactive story turns.",
            model=VertexGemini(
                model=self.responder_model,
                project_id=self.vertex_project,
                location=self.vertex_location,
            ),
            instruction=self._build_responder_instruction,
            tools=[
                self.search_lore,
                self.read_lore,
                self.lookup_character,
                self.generate_character_profile,
                self.roll_dice,
            ],
            output_schema=SceneReaction,
            output_key="scene_reaction",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
            generate_content_config=generate_content_config,
        )

    def restart_responder_agent(self) -> None:
        logger.info(
            "[StoryResponseModule] Resetting and restarting story responder agent for theater=%s",
            self.theater_id,
        )
        self._responder_runner = None
        self._responder_agent = None
        self._responder_app = None
        self._recent_story_log = self._read_recent_story_log()
        self._get_or_create_responder_runner()

    def _get_or_create_responder_runner(self) -> Runner:
        if self._responder_runner is None:
            self._responder_agent = self._create_responder_agent()
            self._responder_app = App(
                name="narratron_story_responder",
                root_agent=self._responder_agent,
                events_compaction_config=self.compaction_config,
            )
            self._responder_runner = Runner(
                app=self._responder_app,
                session_service=self.session_service,
                auto_create_session=True,
            )
        return self._responder_runner

    def _run_responder_agent(self, user_action: str, nudge: str = "") -> Dict[str, Any]:
        self.reset_lore_call_counts()
        runner = self._get_or_create_responder_runner()
        session_id = self.session_id
        theater = self.theater_id or "default"
        logger.debug(
            "[StoryResponseModule] Running responder agent (theater=%s): user_action=%r, nudge=%r",
            theater,
            user_action,
            nudge,
        )

        async def run_turn() -> Dict[str, Any]:
            run_config = (
                RunConfig(context_window_compression=self._run_compression_config)
                if self._run_compression_config
                else None
            )
            prompt_input = user_action
            if nudge:
                prompt_input = f"{user_action}\n\n[Live Agent Nudge to Accommodate]: {nudge}"

            previous_session = await self.session_service.get_session(
                app_name="narratron_story_responder",
                user_id="story_responder",
                session_id=session_id,
            )
            if previous_session and previous_session.state:
                previous_session.state.pop("scene_reaction", None)

            final_text = ""
            async for event in runner.run_async(
                user_id="story_responder",
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt_input)]),
                run_config=run_config,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = "".join(part.text or "" for part in event.content.parts)

            session = await self.session_service.get_session(
                app_name="narratron_story_responder",
                user_id="story_responder",
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
                    reaction = {}
            except json.JSONDecodeError as exc:
                raise ValueError("Story responder returned invalid structured output.") from exc
            if not isinstance(reaction, dict):
                raise ValueError("Story responder returned an invalid scene reaction.")
            if not reaction:
                state_keys = sorted((session.state or {}).keys()) if session else []
                raise ValueError(
                    "Story responder returned no fresh scene delta for this action "
                    f"(final_response_chars={len(final_text)}, state_keys={state_keys})."
                )
            try:
                scene_delta = SceneReaction.model_validate(reaction)
            except Exception as exc:
                raise ValueError("Story responder returned an invalid scene delta.") from exc
            return scene_delta.model_dump()

        try:
            return asyncio.run(
                asyncio.wait_for(run_turn(), timeout=self.user_action_timeout_seconds)
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.error(
                "[StoryResponseModule] Story responder timed out after %.1f seconds for user action: %r",
                self.user_action_timeout_seconds,
                user_action,
            )
            if self.canvas_manager and hasattr(self.canvas_manager, "tool_response"):
                self.canvas_manager.tool_response.set_activity("user_action", active=False)
            self.restart_responder_agent()
            return {
                "error": (
                    f"Story responder timed out after {self.user_action_timeout_seconds} seconds. "
                    "The story responder agent was killed and restarted. "
                    "Please re-try the action or guide the story."
                )
            }

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

    def _publish_scene(self, narration: str, dialogue: List[Dict[str, str]]) -> None:
        try:
            if self.canvas_manager and hasattr(self.canvas_manager, "story"):
                self.canvas_manager.story.set_scene(narration, dialogue)
        except Exception as exc:
            logger.warning("[StoryResponseModule] Failed to publish scene: %s", exc)

    def _apply_character_updates(self, updates: Any) -> List[Dict[str, str]]:
        return self.character_manager.apply_character_updates(updates)

    def process_user_action(self, user_action: str, nudge: str = "") -> Dict[str, Any]:
        return self._process_user_action(user_action, nudge=nudge)

    def process_system_action(
        self, user_action: str, message_type: str, nudge: str = ""
    ) -> Dict[str, Any]:
        return self._process_user_action(
            user_action,
            nudge=nudge,
            message_type=message_type,
        )

    @with_cooldown(
        action_desc="resolving story update",
        duration=lambda tools: tools.get_user_action_cooldown_seconds(),
        tool_name="process_user_action",
    )
    def _process_user_action(
        self, user_action: str, nudge: str = "", message_type: str = ""
    ) -> Dict[str, Any]:
        with self._user_input_lock:
            if self.require_user_input and not self._user_input_detected:
                return {
                    "error": (
                        "Cannot process user action: No input from the orator was detected. "
                        "Please wait for the orator to speak or submit a text command."
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
                    "'[Story Result]' notification before submitting another action."
                )
            }

        log_action = f"<{str(message_type).strip()}>" if str(message_type).strip() else action
        self._append_story_log_entry(StoryLogEntry(type="user_action", action=log_action))

        with self._user_input_lock:
            if self.require_user_input:
                self._user_input_detected = False
                self._last_voice_input_log_time = -float("inf")

        def resolve_and_notify() -> None:
            result = None
            try:
                result = self._resolve_user_action(action, nudge=clean_nudge)
            except Exception as exc:
                logger.exception("[StoryResponseModule] Scene reaction failed")
                result = {"error": f"Story responder failed: {exc}"}
            finally:
                self.release_in_flight("process_user_action")
                if self.canvas_manager and hasattr(self.canvas_manager, "tool_response"):
                    self.canvas_manager.tool_response.set_activity("user_action", active=False)

            plan_output = None
            if isinstance(result, dict) and "error" not in result:
                plan_output = StoryResponseOutput(
                    narration=str(result.get("narration") or "").strip(),
                    dialogue=result.get("dialogue") if isinstance(result.get("dialogue"), list) else [],
                    die_rolls=result.get("die_rolls") if isinstance(result.get("die_rolls"), list) else [],
                )

            self._append_story_log_entry(StoryLogEntry(type="story_response", output=plan_output))

            callback = self.on_scene_reaction
            if callback and result is not None:
                try:
                    callback(result)
                except Exception:
                    logger.exception("[StoryResponseModule] Scene reaction callback failed")

        if self.canvas_manager and hasattr(self.canvas_manager, "tool_response"):
            self.canvas_manager.tool_response.set_activity("user_action", active=True)

        threading.Thread(target=resolve_and_notify, daemon=True).start()
        return {"status": "processing", "message": "Story responder is resolving the action."}

    def _resolve_user_action(self, action: str, nudge: str = "") -> Dict[str, Any]:
        if not self.adventure_mode:
            return {"error": "Adventure Mode is not enabled for this theater."}

        parsed = self._run_responder_agent(action, nudge=nudge)
        if not isinstance(parsed, dict):
            raise ValueError("Story responder must return a JSON object.")
        if "error" in parsed:
            return parsed

        manifested_characters = self._apply_character_updates(parsed.get("character_updates"))
        narration = str(
            parsed.get("narration") or "The scene shifts in response to your action."
        ).strip()[:MAX_NARRATION_CHARS]
        dialogue = self._clean_dialogue(parsed.get("dialogue"))
        planning_signals = [
            str(signal).strip()[:MAX_PLANNING_SIGNAL_CHARS]
            for signal in parsed.get("planning_signals", [])
            if str(signal).strip()
        ][:10]
        die_rolls = self.get_die_rolls_this_turn()
        with self._turn_id_lock:
            self._turn_id += 1
            turn_id = self._turn_id

        deep_plan_revision_used = self.get_deep_plan().get("revision", 0)
        scene_name = str(parsed.get("scene_label") or "").strip()
        reference_images = parsed.get("reference_images") or []
        result = {
            "turn_id": turn_id,
            "deep_plan_revision_used": deep_plan_revision_used,
            "narration": narration,
            "dialogue": dialogue,
            "manifested_characters": manifested_characters,
            "planning_signals": planning_signals,
            "scene_label": scene_name,
            "reference_images": reference_images,
            "lore_activity": self.get_lore_activity_this_turn(),
            "lore_docs_browsed": self.get_lore_docs_browsed_this_turn(),
            "die_rolls": die_rolls,
        }
        self._last_scene_reaction = result
        self._last_action_response_word_count = self._count_response_words(result)
        last_action_time = self._last_call_times.get("process_user_action")
        if last_action_time is not None:
            remaining = self.get_user_action_cooldown_seconds() - (time.time() - last_action_time)
            self._schedule_cooldown_timer("process_user_action", remaining)
        self._publish_scene(narration, dialogue)
        self.save_to_session_state()
        logger.debug(
            "[StoryResponseModule] Scene name: %s | Reference images: %s",
            scene_name or "(unspecified)",
            reference_images,
        )
        callback = self.on_story_response_completed
        if callback:
            try:
                callback()
            except Exception:
                logger.exception("[StoryResponseModule] Story response usage callback failed")

        # Communicate resolved turn to the background planning module!
        self.planning_module.queue_deep_planning(turn_id, action, result)
        return result

    @staticmethod
    def _count_response_words(result: Dict[str, Any]) -> int:
        text_parts = [
            str(result.get("narration") or ""),
        ]
        text_parts.extend(
            str(item.get("text") or "")
            for item in result.get("dialogue", [])
            if isinstance(item, dict)
        )
        return len(re.findall(r"\b[\w'-]+\b", " ".join(text_parts)))

    # Session State
    def export_story_planning_state(self) -> Dict[str, Any]:
        planning_state = self.planning_module.export_planning_state()
        with self._turn_id_lock:
            turn_id = self._turn_id

        result = dict(planning_state)
        result.update({
            "characters": self.character_manager.export_characters(),
            "turn_id": turn_id,
            "last_scene_reaction": dict(self._last_scene_reaction),
        })
        return result

    def import_story_planning_state(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return

        self.planning_module.import_planning_state(state)
        self.character_manager.import_characters(state.get("characters", []))

        with self._turn_id_lock:
            try:
                self._turn_id = max(0, int(state.get("turn_id", 0)))
            except (TypeError, ValueError):
                self._turn_id = 0
        self._last_scene_reaction = state.get("last_scene_reaction", {}) if isinstance(state.get("last_scene_reaction", {}), dict) else {}

    def reload_from_session_state(self) -> None:
        try:
            if not self.canvas_manager or not hasattr(self.canvas_manager, "story"):
                return
            sp_state = self.canvas_manager.story.get_story_planning_state()
            if sp_state:
                self.import_story_planning_state(sp_state)
                return

            saved_notes = self.canvas_manager.story.get_sticky_notes()
            if self.planning_module._sticky_definitions:
                return
            if saved_notes:
                self.planning_module.import_planning_state({"sticky_notes": saved_notes})
        except Exception as e:
            logger.warning(
                "[StoryResponseModule] Failed to reload story planning state from session state: %s",
                e,
            )

    def save_to_session_state(self) -> None:
        try:
            if self.canvas_manager and hasattr(self.canvas_manager, "story"):
                self.canvas_manager.story.set_story_planning_state(self.export_story_planning_state())
        except Exception as e:
            logger.warning(
                "[StoryResponseModule] Failed to save story planning state to session state: %s",
                e,
            )


# Backward-compatible alias
