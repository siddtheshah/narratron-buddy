"""Canvas UI, collaboration, and A2UI surface state."""

from collections.abc import Callable

from pydantic import BaseModel

from game_engine.game_runtime import GameAction


class CanvasGameAction(BaseModel):
    """A game command captured from a rendered game control."""

    definition_hash: str
    command: GameAction
    confirmation: str = ""


def clamp_surface_placement(left_pct: float, top_pct: float) -> dict[str, float]:
    return {"left_pct": round(max(2.0, min(98.0, float(left_pct))), 2), "top_pct": round(max(2.0, min(98.0, float(top_pct))), 2)}


class UIState:
    def __init__(self, persist: Callable[[], None], notify_changed: Callable[..., None]) -> None:
        self._persist, self._notify_changed = persist, notify_changed
        self.viewer_collab_enabled = False
        self.interactive_surfaces: dict[str, dict[str, object]] = {}

    def set_viewer_collab_enabled(self, enabled: bool) -> None:
        self.viewer_collab_enabled = bool(enabled)
        self._persist()
        self._notify_changed("latest")

    def upsert_surface(self, surface: dict[str, object], max_surfaces: int = 5) -> None:
        surface_id = str(surface.get("surface_id") or "")
        if not surface_id:
            raise ValueError("Interactive surface requires a surface_id.")
        self.interactive_surfaces[surface_id] = dict(surface)
        while len(self.interactive_surfaces) > max(1, max_surfaces):
            self.interactive_surfaces.pop(next(iter(self.interactive_surfaces)))
        self._persist()
        self._notify_changed("latest")

    def delete_surface(self, surface_id: str = "all") -> int:
        removed = len(self.interactive_surfaces) if surface_id == "all" else int(self.interactive_surfaces.pop(str(surface_id), None) is not None)
        if surface_id == "all":
            self.interactive_surfaces.clear()
        if removed:
            self._persist()
            self._notify_changed("latest")
        return removed

    def move_surface(self, surface_id: str, left_pct: float, top_pct: float) -> dict[str, float] | None:
        surface = self.interactive_surfaces.get(str(surface_id))
        if surface is None:
            return None
        placement = surface.setdefault("placement", {})
        if not isinstance(placement, dict):
            placement = {}
            surface["placement"] = placement
        placement.update(clamp_surface_placement(left_pct, top_pct))
        self._persist()
        self._notify_changed("latest")
        return {"left_pct": float(placement["left_pct"]), "top_pct": float(placement["top_pct"])}

    def get_interactive_action(self, surface_id: str, component_id: str, action_name: str) -> dict[str, object] | None:
        """Resolve an action against the authoritative generated component tree."""
        surface = self.interactive_surfaces.get(str(surface_id))
        components: dict[str, dict[str, object]] = {}
        for message in (surface or {}).get("messages", []):
            if type(message) is not dict:
                continue
            if message.get("deleteSurface"):
                return None
            if message.get("createSurface"):
                components.clear()
            payload = message.get("createSurface") or message.get("updateComponents") or {}
            updates = payload.get("components") if type(payload) is dict else None
            if type(updates) is not list:
                continue
            for component in updates:
                if type(component) is dict and component.get("id"):
                    components[str(component["id"])] = component
        # Older saved surfaces can lack a root. For generated trees, only a
        # control reachable from the current root can submit an action.
        if "root" in components:
            pending = ["root"]
            visited: set[str] = set()
            while pending:
                current_id = pending.pop()
                if current_id == component_id:
                    break
                if current_id in visited:
                    continue
                visited.add(current_id)
                node = components.get(current_id, {})
                child = node.get("child")
                children = node.get("children")
                if type(child) is str:
                    pending.append(child)
                if type(children) is list:
                    pending.extend(item for item in children if type(item) is str)
            else:
                return None
        component = components.get(str(component_id), {})
        action = component.get("action")
        event = action.get("event") if type(action) is dict else None
        if type(event) is dict and event.get("name") == action_name:
            return dict(event)
        return None

    def load(self, data: dict[str, object]) -> None:
        self.viewer_collab_enabled = bool(data.get("viewer_collab_enabled", False))
        surfaces = data.get("interactive_surfaces", [])
        self.interactive_surfaces = {str(item["surface_id"]): item for item in surfaces if isinstance(item, dict) and item.get("surface_id")} if isinstance(surfaces, list) else {}

    def serialize(self) -> dict[str, object]:
        return {"viewer_collab_enabled": self.viewer_collab_enabled, "interactive_surfaces": list(self.interactive_surfaces.values())}
