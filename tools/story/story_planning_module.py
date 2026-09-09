"""Story planning module: owns sticky notes and background deep planning."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
import threading
from threading import Lock
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from jinja2 import Template
from pydantic import BaseModel, PrivateAttr
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
from tools.story.notepad import (
    Notepad,
)

logger = logging.getLogger(__name__)

DEFAULT_COMPACTION_TRIGGER_TOKENS = 12_000
DEFAULT_COMPACTION_TARGET_TOKENS = 6_000
DEFAULT_STORY_PLANNING_STYLE = "balanced, consequence-driven, and player-agency-first"
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
        notepad: Notepad,
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
        if notepad is None:
            raise ValueError("notepad is required.")

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

        self.notepad = notepad
        self.config = self.notepad.config
        if self.notepad.on_change is None:
            self.notepad.on_change = lambda: self.on_save_state() if self.on_save_state else None

        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        configured_style = self.config.get("style", DEFAULT_STORY_PLANNING_STYLE)
        self.style: str = str(configured_style).strip() or DEFAULT_STORY_PLANNING_STYLE

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
            output_schema=self.notepad.deep_plan_update_model,
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
        update = self.notepad.deep_plan_update_model.model_validate(raw_update)
        self.notepad.replace_from_deep_update(update)

        with self._deep_plan_lock:
            self._deep_plan_revision += 1
            self._deep_plan_through_turn_id = max(
                self._deep_plan_through_turn_id, turn_id
            )
            plan = update.model_dump(mode="json", by_alias=True)
            plan["revision"] = self._deep_plan_revision
            plan["through_turn_id"] = self._deep_plan_through_turn_id
            if not self.notepad.sticky_definitions:
                plan["sticky_notes"] = [
                    {"topic": note["topic"], "info": note["info"]}
                    for note in self.notepad.get_present_sticky_notes()
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
            current_notes=self.notepad.get_present_sticky_notes(),
            structured_sticky_json=json.dumps(
                self.notepad.get_present_structured_sticky_notes(),
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
                    for topic, definition in self.notepad.sticky_definitions.items()
                },
                ensure_ascii=False,
                indent=2,
            )
            if self.notepad.sticky_definitions
            else "",
            required_stickies=self.notepad.get_required_sticky_notes(),
            turn_events=event_payloads,
            max_sticky_notes=self.notepad.max_sticky_notes,
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
            return self.notepad.deep_plan_update_model.model_validate(raw_update).model_dump(
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

    def import_planning_state(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return

        self.notepad.import_state(state)

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
