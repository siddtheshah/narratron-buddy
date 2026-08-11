"""Session-scoped structured context for the live Narratron agent."""

from collections import OrderedDict
import logging
from threading import Lock
from typing import Any

from tools.base_tool import BaseTools

logger = logging.getLogger(__name__)

MAX_NAMED_ELEMENTS = 5


class NamedElementTools(BaseTools):
    """Maintain the small set of named elements that define the current scene."""

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

        initial_config = self.config.get("named_elements", {})
        if isinstance(initial_config, dict):
            for k, v in initial_config.items():
                self._elements[str(k)] = str(v)
        elif isinstance(initial_config, list):
            for elem in initial_config:
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
            logger.warning(f"Failed to reload named elements from session state: {e}")

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
            logger.warning(f"Failed to save named elements to session state: {e}")

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
                if len(self._elements) >= MAX_NAMED_ELEMENTS:
                    self._elements.popitem(last=False)
                self._elements[clean_name] = clean_content

        self.save_to_session_state()

        action = "Updated" if is_update else "Added"
        return f"{action} named element '{clean_name}'."

    def clear_scene(self) -> str:
        """Clear every named element from the current scene before starting a new one."""
        with self._elements_lock:
            count = len(self._elements)
            self._elements.clear()

        self.save_to_session_state()

        return f"Cleared {count} named element(s) from the scene."

    def get_present_elements(self) -> list[dict[str, str]]:
        """Return a stable snapshot for live-agent observability."""
        with self._elements_lock:
            return [
                {"name": name, "content": content}
                for name, content in self._elements.items()
            ]
