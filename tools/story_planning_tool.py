"""Session-scoped story planning context and predictive script generation tool.

Logging & Script Inspection:
----------------------------
All script node updates, scene element mutations, and cache updates emit formatted log records
tagged with ``[STORY_SCRIPT]``.

To inspect story script outputs over time during server execution, filter console logging
using the existing ``--log_prefix`` flag when running ``main.py``:

    python main.py --log_prefix="[STORY_SCRIPT]"
"""

from collections import OrderedDict
import hashlib
import json
import logging
import re
from threading import Lock
import time
from typing import Any, Callable, List, Dict, Optional

from jinja2 import Template

from tools.base_tool import BaseTools, with_cooldown
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseRequest,
    TextResponseProviderError,
)
from providers.registry import get_text_response_provider
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

_SCRIPT_PROMPT_TEMPLATE = Template(
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
Prior narrative nodes already established:
{% for node in reused_nodes -%}
Node {{ node.node_index }}: Narration: {{ node.narration }} | Expected user response: {{ node.expected_user_response }}
{% endfor -%}
{% endif -%}
Generate exactly {{ needed_count }} new upcoming script node(s) starting at node_index {{ start_idx }}.
Return a JSON array of objects with keys 'node_index' (integer), 'narration' (string), and 'expected_user_response' (string)."""
)

SYSTEM_INSTRUCTION = (
    "You are a narrative script planner for an interactive audio/text story experience.\n"
    "You write script nodes that progress the narrative towards expected user response points, where the user makes a choice or takes action.\n"
    "Crucially, drive narrative beats, dramatic tension, and decision points using character personalities, internal motivations, and their distinct quirks alongside scene elements.\n"
    "Craft dramatic interactive decision points, high-stakes branching choices, and vivid adventure steps for the player.\n"
    "Respond ONLY with a valid JSON array of node objects."
)


def compute_elements_fingerprint(
    named_elements: List[Dict[str, str]],
    characters: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Compute a deterministic MD5 fingerprint for scene elements and active characters."""
    normalized_elems = [
        {"name": str(elem.get("name", "")).strip(), "content": str(elem.get("content", "")).strip()}
        for elem in (named_elements or [])
    ]
    normalized_elems.sort(key=lambda x: x["name"])

    normalized_chars = [
        {
            "name": str(c.get("name", "")).strip(),
            "personality": str(c.get("personality", "")).strip(),
            "motivation": str(c.get("motivation", "")).strip(),
            "quirk": str(c.get("quirk", "")).strip(),
        }
        for c in (characters or [])
    ]
    normalized_chars.sort(key=lambda x: x["name"])

    raw_str = json.dumps({"elements": normalized_elems, "characters": normalized_chars}, sort_keys=True)
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()


def build_script_prompt(
    elements: List[Dict[str, str]],
    reused_nodes: List[Dict[str, Any]],
    needed_count: int,
    start_idx: int,
    characters: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build prompt string for generating upcoming script nodes using Jinja2 template."""
    cleaned_elements = [
        {
            "name": str(elem.get("name", "")).strip(),
            "content": str(elem.get("content", "")).strip(),
        }
        for elem in (elements or [])
    ]
    cleaned_chars = [
        {
            "name": str(c.get("name", "")).strip(),
            "description": str(c.get("description", "")).strip(),
            "personality": str(c.get("personality", "")).strip(),
            "motivation": str(c.get("motivation", "")).strip(),
            "quirk": str(c.get("quirk", "")).strip(),
        }
        for c in (characters or [])
    ]
    cleaned_reused = [
        {
            "node_index": node.get("node_index", 0),
            "narration": node.get("narration", ""),
            "expected_user_response": node.get("expected_user_response", ""),
        }
        for node in (reused_nodes or [])
    ]

    return _SCRIPT_PROMPT_TEMPLATE.render(
        elements=cleaned_elements,
        characters=cleaned_chars,
        reused_nodes=cleaned_reused,
        needed_count=needed_count,
        start_idx=start_idx,
    ).strip()


class StoryPlanningTools(BaseTools):
    """Maintain named elements and character motivations defining scene state, predicting upcoming story script pieces.

    Emits formatted logger records prefixed with ``[STORY_SCRIPT]`` whenever scene elements
    or narrative script nodes are updated. Filter output to story script logs using:
        python main.py --log_prefix="[STORY_SCRIPT]"
    """

    def __init__(
        self,
        config: dict | None = None,
        theater_id: str = "",
        canvas_state_service: Any = None,
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
        self._cached_script: List[Dict[str, Any]] = []
        self._script_lock = Lock()

        self.nodes_ahead: int = int(self.config.get("nodes_ahead", 3))
        self.adventure_mode: bool = bool(self.config.get("adventure_mode", False))
        self.max_named_elements: int = int(self.config.get("max_named_elements", DEFAULT_MAX_NAMED_ELEMENTS))
        self.cooldown_duration: float = float(self.config.get("cooldown_duration", 0.0))
        self.text_provider: Optional[TextResponseProvider] = self.config.get("text_provider")
        self.provider_id: str = str(self.config.get("provider") or "gemini-2-5").strip()
        provider_options = self.config.get("provider_options") or {}
        self.provider_options: dict = dict(provider_options) if isinstance(provider_options, dict) else {}
        self._default_text_provider: Optional[TextResponseProvider] = None

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
        """Export full story planning state as a unified dict containing elements, characters, and cached script."""
        with self._elements_lock:
            elements_list = [
                {"name": name, "content": content}
                for name, content in self._elements.items()
            ]
        with self._characters_lock:
            chars_list = [dict(c) for c in self._characters.values()]
        with self._script_lock:
            script_list = list(self._cached_script)

        return {
            "named_elements": elements_list,
            "characters": chars_list,
            "cached_script": script_list,
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

        with self._script_lock:
            script = state.get("cached_script", [])
            self._cached_script = list(script) if isinstance(script, list) else []

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

    def _format_script_log(self, nodes: List[Dict[str, Any]]) -> str:
        """Format active characters and script nodes into a clean multiline debug string."""
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

        if not nodes:
            lines.append("  (No script nodes available)")
        else:
            lines.append("  Upcoming Script Nodes:")
            for node in nodes:
                idx = node.get("node_index", "?")
                narration = node.get("narration", "")
                response = node.get("expected_user_response", "")
                lines.append(f"    [Node {idx}] Narration: {narration}\n             Expected User Response: {response}")
        return "\n".join(lines)

    def _log_script_update(self, nodes: List[Dict[str, Any]], source: str, fingerprint: str = "") -> None:
        """Emit formatted logger output for story script tracking over time."""
        theater = self.theater_id or "default"
        logger.info(
            "[STORY_SCRIPT] Script nodes active (source=%s, theater=%s, fingerprint=%s, count=%d):\n%s",
            source,
            theater,
            fingerprint,
            len(nodes),
            self._format_script_log(nodes),
        )

    def get_tools(self) -> List[Any]:
        """Return bound tool methods exposed to the agent based on configuration."""
        tools: List[Any] = [
            self.update_or_insert_named_element,
            self.clear_scene,
        ]
        if self.adventure_mode:
            tools.extend([
                self.generate_character,
                self.get_script_piece,
            ])
        return tools

    def _get_text_provider(self) -> TextResponseProvider:
        """Return explicit text provider or construct default provider from config."""
        if self.text_provider:
            return self.text_provider
        if self._default_text_provider is None:
            self._default_text_provider = get_text_response_provider(self.provider_id, self.provider_options)
        return self._default_text_provider

    @with_cooldown("generating character")
    def generate_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
    ) -> str:
        """Generate or update a character's motivation, personality, and distinct quirk for story planning.

        Stored in story_planning and used to update upcoming script nodes in Adventure Mode.
        If personality or motivation are left blank, dynamic descriptions will be generated using LLM text provider.
        If quirk is left blank, a distinct random quirk will be assigned from QuirkGeneratorService.
        """
        clean_name = str(name or "").strip()
        clean_desc = str(description or "").strip()
        clean_pers = str(personality or "").strip()
        clean_motiv = str(motivation or "").strip()
        clean_quirk = str(quirk or "").strip()

        if not clean_name:
            return "Error: Character name cannot be empty."

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
                provider = self._get_text_provider()
                req = TextResponseRequest(
                    prompt=prompt,
                    system_instruction="You are a character design assistant for an adventure story. Respond ONLY with valid JSON.",
                    temperature=0.7,
                )
                resp = provider.generate(req)
                resp_text = resp.text.strip()
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

        char_data = {
            "name": clean_name,
            "description": clean_desc,
            "personality": clean_pers,
            "motivation": clean_motiv,
            "quirk": clean_quirk,
        }

        with self._characters_lock:
            self._characters[clean_name] = char_data
            self._characters.move_to_end(clean_name)

        self.save_to_session_state()

        self._log_script_update(self.get_cached_script(), source="character_added", fingerprint="")

        if self.adventure_mode:
            self.update_script_async()

        return f"Created/Updated character '{clean_name}'. Personality: {clean_pers}. Motivation: {clean_motiv}. Quirk: {clean_quirk}."

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

        # Snapshot fingerprint before mutation so we can decide whether to rewrite the script
        pre_fp = compute_elements_fingerprint(self.get_present_elements(), self.get_present_characters())

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

        if self.adventure_mode:
            post_fp = compute_elements_fingerprint(self.get_present_elements(), self.get_present_characters())
            if post_fp != pre_fp:
                # Scene changed — immediately discard stale cached nodes so get_script_piece
                # cannot pop an outdated node before the async refill completes.
                with self._script_lock:
                    self._cached_script.clear()
                logger.info(
                    "[STORY_SCRIPT] Scene fingerprint changed (%s→%s); script cache invalidated (theater=%s).",
                    pre_fp[:8],
                    post_fp[:8],
                    self.theater_id or "default",
                )
                self.update_script_async()

        return f"{action} named element '{clean_name}'."

    def clear_scene(self) -> str:
        """Clear every named element and character from the current scene before starting a new one."""
        with self._elements_lock:
            elem_count = len(self._elements)
            self._elements.clear()

        with self._characters_lock:
            char_count = len(self._characters)
            self._characters.clear()

        with self._script_lock:
            self._cached_script.clear()

        self.save_to_session_state()

        logger.info(
            "[STORY_SCRIPT] Cleared %d scene element(s), %d character(s), and reset cached script (theater=%s).",
            elem_count,
            char_count,
            self.theater_id or "default",
        )

        if self.adventure_mode:
            self.update_script_async()

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

    def get_cached_script(self) -> List[Dict[str, Any]]:
        """Return a copy of the current cached script nodes."""
        with self._script_lock:
            return list(self._cached_script)

    def _build_script_prompt(
        self,
        elements: List[Dict[str, str]],
        reused_nodes: List[Dict[str, Any]],
        needed_count: int,
        start_idx: int,
        characters: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build prompt using the external build_script_prompt helper."""
        return build_script_prompt(
            elements=elements,
            reused_nodes=reused_nodes,
            needed_count=needed_count,
            start_idx=start_idx,
            characters=characters,
        )

    def _refill_cache(self) -> None:
        """Generate new script nodes and append them to the cache to maintain the nodes_ahead buffer.

        Validates existing cached nodes against the current element+character fingerprint and
        discards stale ones before topping up. Safe to call from background threads.
        """
        if self.nodes_ahead <= 0:
            raise ValueError("nodes_ahead must be positive.")

        elements = self.get_present_elements()
        characters = self.get_present_characters()
        fingerprint = compute_elements_fingerprint(elements, characters)

        with self._script_lock:
            valid_nodes = [
                n for n in self._cached_script
                if not n.get("elements_fingerprint") or n.get("elements_fingerprint") == fingerprint
            ]
            needed_count = self.nodes_ahead - len(valid_nodes)
            start_idx = len(valid_nodes)

        if needed_count <= 0:
            return

        prompt = self._build_script_prompt(
            elements=elements,
            reused_nodes=valid_nodes,
            needed_count=needed_count,
            start_idx=start_idx,
            characters=characters,
        )

        logger.debug(
            "[STORY_SCRIPT] Refilling cache: generating %d node(s) (theater=%s, fingerprint=%s)",
            needed_count,
            self.theater_id or "default",
            fingerprint,
        )

        provider = self._get_text_provider()
        request = TextResponseRequest(
            prompt=prompt,
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.7,
        )
        response_result = provider.generate(request)
        new_nodes = self._parse_script_nodes(
            response_result.text,
            needed_count=needed_count,
            start_index=start_idx,
            fingerprint=fingerprint,
        )

        with self._script_lock:
            # Re-filter in case fingerprint changed while we were generating
            self._cached_script = [
                n for n in self._cached_script
                if not n.get("elements_fingerprint") or n.get("elements_fingerprint") == fingerprint
            ]
            self._cached_script.extend(new_nodes)

        self._log_script_update(self.get_cached_script(), source="cache_refill", fingerprint=fingerprint)

    @with_cooldown("generating script piece")
    def get_script_piece(self) -> Dict[str, Any]:
        """Pop and return the next script node from the cache.

        Each call consumes one node from the front of the buffer.  An async refill is
        triggered when the buffer drops to 1, so a fresh node is being generated while
        the last buffered one is still available — avoiding a sync wait on the next call.
        If the cache is empty at call time a synchronous refill runs first as a safety net.
        """
        if self.nodes_ahead <= 0:
            raise ValueError("nodes_ahead must be positive.")

        with self._script_lock:
            cache_empty = len(self._cached_script) == 0

        if cache_empty:
            # Nothing buffered yet — generate synchronously so we can return immediately
            self._refill_cache()

        with self._script_lock:
            if self._cached_script:
                node = self._cached_script.pop(0)
                remaining = len(self._cached_script)
            else:
                raise ValueError("Script cache is empty and could not be refilled.")

        if remaining == 1:
            self.update_script_async()

        return node

    @classmethod
    def _parse_script_nodes(
        cls,
        text: str,
        needed_count: int,
        start_index: int,
        fingerprint: str,
    ) -> List[Dict[str, Any]]:
        """Parse text into structured script node dicts."""
        nodes: List[Dict[str, Any]] = []

        cleaned_text = text.strip()
        if cleaned_text.startswith("```"):
            cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text)
            cleaned_text = re.sub(r"\s*```$", "", cleaned_text)

        parsed_json: Any = None
        try:
            parsed_json = json.loads(cleaned_text)
        except Exception:
            pass

        if isinstance(parsed_json, list):
            for idx, item in enumerate(parsed_json):
                if isinstance(item, dict):
                    narration = str(item.get("narration") or item.get("text") or item.get("description") or "").strip()
                    expected_resp = str(
                        item.get("expected_user_response")
                        or item.get("user_response")
                        or item.get("response_point")
                        or ""
                    ).strip()
                    if narration or expected_resp:
                        nodes.append({
                            "node_index": start_index + len(nodes),
                            "narration": narration or "Narrator continues the story...",
                            "expected_user_response": expected_resp or "What do you do next?",
                            "elements_fingerprint": fingerprint,
                        })
                        if len(nodes) >= needed_count:
                            break

        if not nodes:
            # Fallback parsing for non-JSON or raw text
            lines = [l.strip() for l in cleaned_text.splitlines() if l.strip()]
            narr = " ".join(lines[:2]) if lines else "The narrative unfolds as scene elements align."
            resp = lines[2] if len(lines) > 2 else "What action do you take?"
            for i in range(needed_count):
                nodes.append({
                    "node_index": start_index + i,
                    "narration": f"{narr} (Step {i + 1})",
                    "expected_user_response": f"{resp} (Option {i + 1})",
                    "elements_fingerprint": fingerprint,
                })

        # Ensure we return exactly needed_count nodes
        while len(nodes) < needed_count:
            next_idx = start_index + len(nodes)
            nodes.append({
                "node_index": next_idx,
                "narration": f"The story advances to key decision point {next_idx}.",
                "expected_user_response": f"How do you react at step {next_idx}?",
                "elements_fingerprint": fingerprint,
            })

        return nodes[:needed_count]

    def update_script_async(
        self,
        callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> None:
        """Asynchronously refill the internal cached script buffer."""
        import threading

        def _worker():
            try:
                self._refill_cache()
                if callback:
                    callback(self.get_cached_script())
            except Exception as exc:
                logger.warning(f"[STORY_SCRIPT] Asynchronous script update failed: {exc}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
