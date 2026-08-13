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

logger = logging.getLogger(__name__)

DEFAULT_MAX_NAMED_ELEMENTS = 5

_SCRIPT_PROMPT_TEMPLATE = Template(
"""Current scene elements:
{% if not elements -%}
(No active named elements)
{% else -%}
{% for elem in elements -%}
- {{ elem.name }}: {{ elem.content }}
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
    "Craft dramatic interactive decision points, high-stakes branching choices, and vivid adventure steps for the player.\n"
    "Respond ONLY with a valid JSON array of node objects."
)


def compute_elements_fingerprint(named_elements: List[Dict[str, str]]) -> str:
    """Compute a deterministic MD5 fingerprint for a list of named elements."""
    normalized = [
        {"name": str(elem.get("name", "")).strip(), "content": str(elem.get("content", "")).strip()}
        for elem in (named_elements or [])
    ]
    normalized.sort(key=lambda x: x["name"])
    raw_str = json.dumps(normalized, sort_keys=True)
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()


def build_script_prompt(
    elements: List[Dict[str, str]],
    reused_nodes: List[Dict[str, Any]],
    needed_count: int,
    start_idx: int,
) -> str:
    """Build prompt string for generating upcoming script nodes using Jinja2 template."""
    cleaned_elements = [
        {
            "name": str(elem.get("name", "")).strip(),
            "content": str(elem.get("content", "")).strip(),
        }
        for elem in (elements or [])
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
        reused_nodes=cleaned_reused,
        needed_count=needed_count,
        start_idx=start_idx,
    ).strip()





class StoryPlanningTools(BaseTools):
    """Maintain named elements defining scene state and predict upcoming story script pieces.

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

        self.reload_from_session_state()

    def reload_from_session_state(self) -> None:
        """Reload named elements from session state (canvas_state_service / manager) if present."""
        if not self.canvas_state_service or not self.theater_id:
            return
        try:
            state_mgr = None
            if hasattr(self.canvas_state_service, "get"):
                state_mgr = self.canvas_state_service.get(self.theater_id)
            elif hasattr(self.canvas_state_service, "get_named_elements"):
                state_mgr = self.canvas_state_service

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
            logger.warning(f"[STORY_SCRIPT] Failed to reload named elements from session state: {e}")

    def save_to_session_state(self) -> None:
        """Persist current named elements snapshot to session state."""
        if not self.canvas_state_service or not self.theater_id:
            return
        try:
            state_mgr = None
            if hasattr(self.canvas_state_service, "get"):
                state_mgr = self.canvas_state_service.get(self.theater_id)
            elif hasattr(self.canvas_state_service, "set_named_elements"):
                state_mgr = self.canvas_state_service

            if state_mgr and hasattr(state_mgr, "set_named_elements"):
                state_mgr.set_named_elements(self.get_present_elements())
        except Exception as e:
            logger.warning(f"[STORY_SCRIPT] Failed to save named elements to session state: {e}")

    @staticmethod
    def _format_script_log(nodes: List[Dict[str, Any]]) -> str:
        """Format script nodes into a clean multiline debug string."""
        if not nodes:
            return "  (No script nodes available)"
        lines = []
        for node in nodes:
            idx = node.get("node_index", "?")
            narration = node.get("narration", "")
            response = node.get("expected_user_response", "")
            lines.append(f"  [Node {idx}] Narration: {narration}\n           Expected User Response: {response}")
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
            tools.append(self.get_script_piece)
        return tools

    def _get_text_provider(self) -> TextResponseProvider:
        """Return explicit text provider or construct default provider from config."""
        if self.text_provider:
            return self.text_provider
        if self._default_text_provider is None:
            self._default_text_provider = get_text_response_provider(self.provider_id, self.provider_options)
        return self._default_text_provider

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

        if self.adventure_mode:
            self.update_script_async()

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
        """Clear every named element from the current scene before starting a new one."""
        with self._elements_lock:
            count = len(self._elements)
            self._elements.clear()

        with self._script_lock:
            self._cached_script.clear()

        self.save_to_session_state()

        logger.info(
            "[STORY_SCRIPT] Cleared %d scene element(s) and reset cached script (theater=%s).",
            count,
            self.theater_id or "default",
        )

        if self.adventure_mode:
            self.update_script_async()

        return f"Cleared {count} named element(s) from the scene."

    def get_present_elements(self) -> list[dict[str, str]]:
        """Return a stable snapshot for live-agent observability."""
        with self._elements_lock:
            return [
                {"name": name, "content": content}
                for name, content in self._elements.items()
            ]

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
    ) -> str:
        """Build prompt using the external build_script_prompt helper."""
        return build_script_prompt(
            elements=elements,
            reused_nodes=reused_nodes,
            needed_count=needed_count,
            start_idx=start_idx,
        )

    @with_cooldown("generating script piece")
    def get_script_piece(self) -> List[Dict[str, Any]]:
        """Instance method taking no arguments for generating or updating a script piece.

        A script node represents an expected response point from the user.
        Reuses matching nodes from cached script if element fingerprint matches.
        """
        if self.nodes_ahead <= 0:
            raise ValueError("nodes_ahead must be positive.")

        elements = self.get_present_elements()
        fingerprint = compute_elements_fingerprint(elements)
        script_prior = self.get_cached_script()

        reused_nodes: List[Dict[str, Any]] = []
        if script_prior:
            for item in script_prior:
                if not isinstance(item, dict):
                    continue
                item_fp = item.get("elements_fingerprint", "")
                if item_fp == fingerprint or not item_fp:
                    node_copy = dict(item)
                    node_copy["elements_fingerprint"] = fingerprint
                    reused_nodes.append(node_copy)
                    if len(reused_nodes) >= self.nodes_ahead:
                        break

        if len(reused_nodes) >= self.nodes_ahead:
            res = reused_nodes[: self.nodes_ahead]
            with self._script_lock:
                self._cached_script = list(res)
            self._log_script_update(res, source="cache_hit", fingerprint=fingerprint)
            return res

        needed_count = self.nodes_ahead - len(reused_nodes)
        start_idx = len(reused_nodes)

        prompt = self._build_script_prompt(
            elements=elements,
            reused_nodes=reused_nodes,
            needed_count=needed_count,
            start_idx=start_idx,
        )

        logger.debug(
            "[STORY_SCRIPT] Requesting %d new script node(s) from text provider (theater=%s, fingerprint=%s)",
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

        final_script = reused_nodes + new_nodes
        with self._script_lock:
            self._cached_script = list(final_script)
        self._log_script_update(final_script, source="llm_generated", fingerprint=fingerprint)
        return final_script

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
        """Asynchronously update the internal cached script."""
        import threading

        def _worker():
            try:
                updated = self.get_script_piece()
                if callback:
                    callback(updated)
            except Exception as exc:
                logger.warning(f"[STORY_SCRIPT] Asynchronous script update failed: {exc}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
