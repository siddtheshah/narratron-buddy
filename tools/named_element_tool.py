"""Session-scoped structured context for the live Narratron agent."""

from collections import OrderedDict
from threading import Lock
from typing import Any

from tools.base_tool import BaseTools


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

    def upsert_named_element(self, name: str, content: str) -> str:
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

        action = "Updated" if is_update else "Added"
        return f"{action} named element '{clean_name}'."

    def clear_scene(self) -> str:
        """Clear every named element from the current scene before starting a new one."""
        with self._elements_lock:
            count = len(self._elements)
            self._elements.clear()
        return f"Cleared {count} named element(s) from the scene."

    def get_present_elements(self) -> list[dict[str, str]]:
        """Return a stable snapshot for live-agent observability."""
        with self._elements_lock:
            return [
                {"name": name, "content": content}
                for name, content in self._elements.items()
            ]
