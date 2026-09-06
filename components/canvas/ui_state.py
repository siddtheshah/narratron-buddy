"""Canvas UI, collaboration, and A2UI surface state."""

from collections.abc import Callable
from components.canvas_state_utils import clamp_surface_placement


class UIState:
    def __init__(self, persist: Callable[[], None], notify_changed: Callable[..., None]) -> None:
        self._persist, self._notify_changed = persist, notify_changed
        self.viewer_collab_enabled = False
        self.interactive_surfaces: dict[str, dict[str, object]] = {}

    def set_viewer_collab_enabled(self, enabled: bool) -> None:
        self.viewer_collab_enabled = bool(enabled); self._persist(); self._notify_changed("latest")

    def update(self, surface: dict[str, object], max_surfaces: int = 5) -> None:
        """Canonical UI mutation entry point for theater-scoped callers."""
        self.upsert_surface(surface, max_surfaces)

    def upsert_surface(self, surface: dict[str, object], max_surfaces: int = 5) -> None:
        surface_id = str(surface.get("surface_id") or "")
        if not surface_id: raise ValueError("Interactive surface requires a surface_id.")
        self.interactive_surfaces[surface_id] = dict(surface)
        while len(self.interactive_surfaces) > max(1, max_surfaces): self.interactive_surfaces.pop(next(iter(self.interactive_surfaces)))
        self._persist(); self._notify_changed("latest")

    def delete_surface(self, surface_id: str = "all") -> int:
        removed = len(self.interactive_surfaces) if surface_id == "all" else int(self.interactive_surfaces.pop(str(surface_id), None) is not None)
        if surface_id == "all": self.interactive_surfaces.clear()
        if removed: self._persist(); self._notify_changed("latest")
        return removed

    def move_surface(self, surface_id: str, left_pct: float, top_pct: float) -> dict[str, float] | None:
        surface = self.interactive_surfaces.get(str(surface_id))
        if surface is None: return None
        placement = surface.setdefault("placement", {})
        if not isinstance(placement, dict): placement = {}; surface["placement"] = placement
        placement.update(clamp_surface_placement(left_pct, top_pct))
        self._persist(); self._notify_changed("latest")
        return {"left_pct": float(placement["left_pct"]), "top_pct": float(placement["top_pct"])}

    def load(self, data: dict[str, object]) -> None:
        self.viewer_collab_enabled = bool(data.get("viewer_collab_enabled", False))
        surfaces = data.get("interactive_surfaces", [])
        self.interactive_surfaces = {str(item["surface_id"]): item for item in surfaces if isinstance(item, dict) and item.get("surface_id")} if isinstance(surfaces, list) else {}

    def serialize(self) -> dict[str, object]:
        return {"viewer_collab_enabled": self.viewer_collab_enabled, "interactive_surfaces": list(self.interactive_surfaces.values())}
