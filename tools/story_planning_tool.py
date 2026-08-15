"""Session-scoped story planning and planner-owned plot-beat tools.

Logging & Script Inspection:
----------------------------
All plot-beat updates, character mutations, and scene element mutations emit formatted log records
tagged with ``[STORY_SCRIPT]``.

To inspect story script outputs over time during server execution, filter console logging
using the existing ``--log_prefix`` flag when running ``main.py``:

    python main.py --log_prefix="[STORY_SCRIPT]"
"""

from collections import OrderedDict
from functools import cached_property
import json
import logging
import os
import re
from threading import Lock
import time
from typing import Any, Callable, List, Dict, Optional

from jinja2 import Template
from pydantic import BaseModel, Field
from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google import genai
from google.genai import types

from components.theater_manager import TheaterManager
from tools.base_tool import BaseTools, with_cooldown
from services.quirk_service import get_quirk_generator_service

logger = logging.getLogger(__name__)

DEFAULT_MAX_NAMED_ELEMENTS = 5

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

Generate a compelling personality description and core motivation for this character in an adventure story experience.
Return ONLY a JSON object with keys 'personality' (string) and 'motivation' (string)."""
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
- {{ char.name }}: Personality: {{ char.personality }} | Motivation: {{ char.motivation }} | Distinct Quirk: {{ char.quirk }}{% if char.description %} (Description: {{ char.description }}){% endif %}
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

SCENE_REACTION_SYSTEM_INSTRUCTION = (
    "You are the authoritative narrative script engine for an interactive story. "
    "Resolve the consequences of the player's submitted action, decide when NPCs should manifest or change, and update future beats. "
    "The player/orator and any character they control are outside your control: their submitted words are historical input, not dialogue to continue, revise, "
    "narrate as, or attribute to them. Never invent the player's actions, speech, thoughts, feelings, decisions, or a response on their behalf. "
    "Write narration only about the world and the consequences of the submitted action. Dialogue may be spoken only by NPCs; never emit dialogue for a speaker "
    "called Player, User, Orator, You, or for the player-controlled character. Character updates are for NPCs only. "
    "The live agent is only a relay; do not give it choices, tool instructions, or control of the plot. Respond ONLY with valid JSON."
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


class SceneReaction(BaseModel):
    """Complete, typed scene delta emitted by the ephemeral ADK story planner."""
    narration: str
    dialogue: List[PlannerDialogue] = Field(default_factory=list)
    # These are a structured fallback when the model finalizes directly
    # instead of issuing the equivalent staged ADK tool call.
    plot_beats: List[str] = Field(default_factory=list)
    character_updates: List[PlannerCharacter] = Field(default_factory=list)


class VertexGemini(Gemini):
    """ADK Gemini model with an explicit Vertex AI client, independent of env defaults."""

    project_id: Optional[str] = None
    location: str = "global"

    @cached_property
    def api_client(self):
        return genai.Client(vertexai=True, project=self.project_id, location=self.location)


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


class StoryPlanningTools(BaseTools):
    """Maintain scene context, characters, and planner-owned durable plot beats.

    Emits formatted logger records prefixed with ``[STORY_SCRIPT]`` whenever scene elements
    or plot beats are updated. Filter output to story script logs using:
        python main.py --log_prefix="[STORY_SCRIPT]"
    """

    def __init__(
        self,
        config: dict | None = None,
        theater_id: str = "",
        canvas_state_service: Any = None,
        theater_manager: Optional[TheaterManager] = None,
    ):
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
        self.theater_manager = theater_manager or TheaterManager()

        self.nodes_ahead: int = int(self.config.get("nodes_ahead", 3))
        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        self.max_named_elements: int = int(self.config.get("max_named_elements", DEFAULT_MAX_NAMED_ELEMENTS))
        self.cooldown_duration: float = float(self.config.get("cooldown_duration", 0.0))
        self.planner_model: str = str(self.config.get("planner_model") or "gemini-3.7-flash")
        self.vertex_project: Optional[str] = (
            self.config.get("vertex_project")
            or self.config.get("gcloud", {}).get("project_id")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        self.vertex_location: str = str(
            self.config.get("vertex_location") or os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
        )
        self._vertex_client: Optional[Any] = None
        # Test/dev seam. Production uses a fresh ADK agent run for each action.
        self.planner_executor: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = self.config.get("planner_executor")
        # Bound by AgentSession after its live queue is running. The callback
        # receives the completed planner result from a background worker.
        self.on_scene_reaction: Optional[Callable[[Dict[str, Any]], None]] = self.config.get("on_scene_reaction")
        # Bound by AgentSession so completed planner turns can be billed using
        # the same idempotent usage path as image and music generation.
        self.on_story_plan_completed: Optional[Callable[[], None]] = self.config.get("on_story_plan_completed")

        initial_elements = self.config.get("initial_elements", {})
        if isinstance(initial_elements, dict):
            for k, v in initial_elements.items():
                self._elements[str(k)] = str(v)
        elif isinstance(initial_elements, list):
            for elem in initial_elements:
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
                    }

        self.reload_from_session_state()

    def export_story_planning_state(self) -> Dict[str, Any]:
        """Export the scene elements, characters, durable plot beats, and latest reaction."""
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
            logger.warning(f"[STORY_SCRIPT] Failed to reload story planning state from session state: {e}")

    def save_to_session_state(self) -> None:
        """Persist story planning snapshot to session state."""
        if not self.canvas_state_service or not self.theater_id:
            return
        try:
            state_mgr = None
            if hasattr(self.canvas_state_service, "get"):
                state_mgr = self.canvas_state_service.get(self.theater_id)
            elif hasattr(self.canvas_state_service, "set_story_planning_state") or hasattr(self.canvas_state_service, "set_named_elements"):
                state_mgr = self.canvas_state_service

            if state_mgr and hasattr(state_mgr, "set_story_planning_state"):
                state_mgr.set_story_planning_state(self.export_story_planning_state())
            elif state_mgr and hasattr(state_mgr, "set_named_elements"):
                state_mgr.set_named_elements(self.get_present_elements())
        except Exception as e:
            logger.warning(f"[STORY_SCRIPT] Failed to save story planning state to session state: {e}")

    def _format_story_log(self, plot_beats: List[Dict[str, Any]]) -> str:
        """Format active characters and durable plot beats into a clean debug string."""
        lines = []
        chars = self.get_present_characters()
        if chars:
            lines.append("  Active Characters:")
            for c in chars:
                desc_str = f" ({c['description']})" if c.get("description") else ""
                lines.append(
                    f"    - {c['name']}{desc_str}: Personality: {c.get('personality', 'N/A')} | "
                    f"Motivation: {c.get('motivation', 'N/A')} | Quirk: {c.get('quirk', 'N/A')}"
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
        logger.info(
            "[STORY_SCRIPT] Plot beats active (source=%s, theater=%s, count=%d):\n%s",
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

    def _get_vertex_client(self) -> Any:
        """Return the explicitly configured Vertex client used for all planner calls."""
        if self._vertex_client is None:
            self._vertex_client = genai.Client(
                vertexai=True,
                project=self.vertex_project,
                location=self.vertex_location,
            )
        return self._vertex_client

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
            return (
                "Available lore documents:\n" + "\n".join(f"- {path}" for path in documents)
                if documents
                else "No lore documents are available for this theater."
            )
        try:
            content = self.theater_manager.read_lore_document(self.theater_id, document)
        except ValueError as error:
            return f"Error: {error}"
        return f"Lore document: {document}\n\n{content}"

    def _get_initial_lore_context(self) -> str:
        """Load every lore document for the first, otherwise context-free planner turn."""
        documents = self.theater_manager.get_lore_documents(self.theater_id)
        if not documents:
            return ""
        sections = []
        for document in documents:
            try:
                content = self.theater_manager.read_lore_document(self.theater_id, document)
            except ValueError as error:
                logger.warning("[STORY_SCRIPT] Could not inject lore document '%s': %s", document, error)
                continue
            sections.append(f"Lore document: {document}\n{content}")
        return "\n\n".join(sections)

    def generate_character_profile(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
    ) -> Dict[str, str]:
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

        if not clean_name:
            return {"error": "Character name cannot be empty."}

        # Assign random quirk from QuirkGeneratorService if not specified
        if not clean_quirk:
            active_quirks = [c.get("quirk", "") for c in self.get_present_characters() if c.get("quirk")]
            quirk_service = get_quirk_generator_service()
            clean_quirk = quirk_service.get_random_quirk(exclude=active_quirks)

        # If personality or motivation are missing, generate them via text provider
        if not clean_pers or not clean_motiv:
            elements = self.get_present_elements()
            prompt = _CHARACTER_GEN_PROMPT_TEMPLATE.render(
                name=clean_name,
                description=clean_desc,
                elements=elements,
            ).strip()

            try:
                response = self._get_vertex_client().models.generate_content(
                    model=self.planner_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction="You are a character design assistant for an adventure story. Respond ONLY with valid JSON.",
                        response_mime_type="application/json",
                        temperature=0.7,
                    ),
                )
                resp_text = str(getattr(response, "text", "") or "").strip()
                if resp_text.startswith("```"):
                    resp_text = re.sub(r"^```(?:json)?\s*", "", resp_text)
                    resp_text = re.sub(r"\s*```$", "", resp_text)
                parsed = json.loads(resp_text)
                if isinstance(parsed, dict):
                    if not clean_pers:
                        clean_pers = str(parsed.get("personality") or "").strip()
                    if not clean_motiv:
                        clean_motiv = str(parsed.get("motivation") or "").strip()
            except Exception as exc:
                logger.warning(f"[STORY_SCRIPT] LLM character generation fallback for '{clean_name}': {exc}")

            if not clean_pers:
                clean_pers = "Resourceful and determined character with distinct flair."
            if not clean_motiv:
                clean_motiv = "To navigate scene challenges and drive the narrative forward."

        return {
            "name": clean_name,
            "description": clean_desc,
            "personality": clean_pers,
            "motivation": clean_motiv,
            "quirk": clean_quirk,
        }

    @with_cooldown("generating character")
    def generate_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
    ) -> str:
        """Generate or update a persisted character outside a planner turn."""
        char_data = self.generate_character_profile(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
        )
        if "error" in char_data:
            return f"Error: {char_data['error']}"
        clean_name = char_data["name"]

        with self._characters_lock:
            self._characters[clean_name] = char_data
            self._characters.move_to_end(clean_name)

        self.save_to_session_state()

        self._log_story_update(self.get_plot_beats(), source="character_added")

        return (
            f"Created/Updated character '{clean_name}'. Personality: {char_data['personality']}. "
            f"Motivation: {char_data['motivation']}. Quirk: {char_data['quirk']}."
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
        logger.info(
            "[STORY_SCRIPT] %s named element '%s' (theater=%s). Active elements count: %d",
            action,
            clean_name,
            self.theater_id or "default",
            len(self._elements),
        )

        return f"{action} named element '{clean_name}'."

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

        self.save_to_session_state()

        logger.info(
            "[STORY_SCRIPT] Cleared %d scene element(s), %d character(s), and plot beats (theater=%s).",
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
                {"name": name, "content": content}
                for name, content in self._elements.items()
            ]

    def get_present_characters(self) -> list[dict[str, str]]:
        """Return a stable snapshot of active characters with personalities and motivations."""
        with self._characters_lock:
            return [dict(char) for char in self._characters.values()]

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
            state_mgr = self.canvas_state_service.get(self.theater_id) if hasattr(self.canvas_state_service, "get") else self.canvas_state_service
            if hasattr(state_mgr, "set_scene_dialogue"):
                state_mgr.set_scene_dialogue(dialogue)
        except Exception as exc:
            logger.warning("[STORY_SCRIPT] Failed to publish scene dialogue: %s", exc)

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
            )
            manifested.extend([character for character in self.get_present_characters() if character["name"] == name])
        return manifested

    def _run_planner_agent(self, user_action: str) -> Dict[str, Any]:
        """Run one stateless ADK planner turn against a CanvasState snapshot."""
        snapshot = {
            "elements": self.get_present_elements(),
            "characters": self.get_present_characters(),
            "plot_beats": [node.get("plot_beat", "") for node in self.get_plot_beats()],
            "nodes_ahead": self.nodes_ahead,
        }
        if self.planner_executor:
            return self.planner_executor(user_action, snapshot)

        initial_lore_context = self._get_initial_lore_context() if not snapshot["plot_beats"] else ""
        initial_lore_section = (
            "Complete theater lore for this initial planning turn:\n" + initial_lore_context
            if initial_lore_context
            else ""
        )

        planner_context = build_story_context_prompt(
            elements=snapshot["elements"],
            characters=snapshot["characters"],
            reused_nodes=[
                {"node_index": index, "plot_beat": beat}
                for index, beat in enumerate(snapshot["plot_beats"])
            ],
        )
        instruction = f"""{SCENE_REACTION_SYSTEM_INSTRUCTION}

{planner_context}

{initial_lore_section}

The user action is immutable player input. Do not repeat it as dialogue or convert it into an authored turn for the player. Return one complete scene delta that leaves the player's next action, speech, thoughts, and choices entirely open. Include exactly {self.nodes_ahead} general plot beats in plot_beats; they must not predict, request, or prescribe player actions. On an initial empty-script turn, the complete theater lore is included above; use it when planning the first NPCs and scene. On later turns, use browse_lore to inspect relevant theater lore before introducing or enriching NPCs. Include character_updates only for NPCs that should enter or materially change; never create or update the player-controlled character. You may call generate_character_profile to enrich a proposed NPC, then include its returned profile in character_updates. Those tools only provide information: the scene delta is the sole source of changes. `dialogue` is optional and must contain NPC speech only. Then return the scene reaction."""
        planner = Agent(
            name="story_planner",
            description="Authoritative planner for one interactive story turn.",
            model=VertexGemini(
                model=self.planner_model,
                project_id=self.vertex_project,
                location=self.vertex_location,
            ),
            instruction=instruction,
            tools=[self.browse_lore, self.generate_character_profile],
            output_schema=SceneReaction,
            output_key="scene_reaction",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )

        async def run_turn() -> Dict[str, Any]:
            sessions = InMemorySessionService()
            runner = Runner(app_name="narratron_story_planner", agent=planner, session_service=sessions)
            session_id = f"planner_{self.theater_id or 'default'}_{time.time_ns()}"
            await sessions.create_session(app_name="narratron_story_planner", user_id="story_planner", session_id=session_id)
            final_text = ""
            async for event in runner.run_async(
                user_id="story_planner",
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=user_action)]),
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    final_text = "".join(part.text or "" for part in event.content.parts)
            session = await sessions.get_session(
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

    @with_cooldown("resolving another player action", duration=10.0)
    def process_user_action(self, user_action: str) -> Dict[str, Any]:
        """Queue a non-blocking authoritative resolution of an orator action.

        The final reaction is delivered through ``on_scene_reaction``. Callers
        receive immediately so a slow planner cannot stall the Live session.
        """
        action = str(user_action or "").strip()
        if not action:
            return {"error": "User action cannot be empty."}

        def resolve_and_notify() -> None:
            try:
                result = self._resolve_user_action(action)
            except Exception as exc:
                logger.exception("[STORY_SCRIPT] Scene reaction failed")
                result = {"error": f"Story planner failed: {exc}"}
            callback = self.on_scene_reaction
            if callback:
                try:
                    callback(result)
                except Exception:
                    logger.exception("[STORY_SCRIPT] Scene reaction callback failed")

        import threading
        threading.Thread(target=resolve_and_notify, daemon=True).start()
        return {"status": "processing", "message": "Story planner is resolving the action."}

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
        narration = str(parsed.get("narration") or "The scene shifts in response to your action.").strip()
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
        self._publish_scene_dialogue(dialogue)
        self.save_to_session_state()
        self._log_story_update(plot_beats, source="user_action")
        callback = self.on_story_plan_completed
        if callback:
            try:
                callback()
            except Exception:
                logger.exception("[STORY_SCRIPT] Story-planning usage callback failed")
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
                normalized.append({"plot_beat": beat})
        return normalized
