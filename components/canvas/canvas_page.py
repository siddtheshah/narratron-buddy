"""The persisted presentation and annotation state for a canvas history page."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from math import isfinite
from typing import ClassVar, Literal, TYPE_CHECKING, TypedDict, cast
from uuid import uuid4

from pydantic import JsonValue

from game_engine.canvas_contract import Page

if TYPE_CHECKING:
    from components.canvas.canvas_assets import CanvasAssetCatalog
    from components.canvas.visual_state import VisualResource, VisualState

DoodleValue = str | float | int | bool | list[float] | None
DoodleAction = dict[str, DoodleValue]


class PageAnnotations(TypedDict, total=False):
    page_id: str
    doodles: list[DoodleAction]
    doodles_enabled: bool


class CanvasPage(dict[str, JsonValue], ABC):
    """A stable page and its annotations, backed by the saved JSON record."""

    kind: ClassVar[Literal["image", "animation", "game"]]

    def __init__(self, saved: Mapping[str, JsonValue]) -> None:
        super().__init__(saved)
        self["kind"] = self.kind
        if not self.get("page_id"):
            self["page_id"] = uuid4().hex

    @staticmethod
    def from_saved(saved: Mapping[str, JsonValue]) -> CanvasPage:
        if saved.get("kind") == "game":
            return GameCanvasPage(saved)
        if type(saved.get("animation")) is dict and saved["animation"]:
            return AnimationCanvasPage(saved)
        return ImageCanvasPage(saved)

    @property
    def id(self) -> str:
        return str(self["page_id"])

    @property
    def path(self) -> str | None:
        path = self.get("path")
        return str(path) if path else None

    @property
    def annotations(self) -> PageAnnotations:
        return cast(PageAnnotations, self)

    def matches_game(self, definition_hash: str) -> bool:
        return False

    def presentation(self, resolve_url: Callable[[str], str]) -> dict[str, JsonValue]:
        return {key: value for key, value in self.items() if key != "doodles"}

    def initialize_geometry(self, catalog: CanvasAssetCatalog) -> None:
        raise ValueError("Saved canvas page geometry is unavailable.")

    @abstractmethod
    def restore(self, visual: VisualState) -> VisualResource | None:
        """Restore this page's media on a canvas whose previous media was cleared."""


class ImageCanvasPage(CanvasPage):
    kind = "image"

    def restore(self, visual: VisualState) -> VisualResource | None:
        if self.path is None:
            return None
        return {
            "type": "image",
            "path": self.path,
            "transition": visual.shown_image_transition,
            "effect": visual.shown_image_effect,
            "prompt": visual.shown_image_prompt,
            "priority": visual.PRIORITY_SHOW,
            "source": "show_image",
        }

    def initialize_geometry(self, catalog: CanvasAssetCatalog) -> None:
        asset = catalog.observe_image(self.path or "")
        page = Page(
            id=self.id, scene_id=asset.scene_id, asset_id=asset.asset_id,
            media_kind="image", animation=None, width=asset.width, height=asset.height,
        )
        self["canvas_page"] = page.model_dump(mode="json")
        self["canvas_regions"] = {key: region.model_dump(mode="json") for key, region in asset.regions.items()}
        self["path"], self["url"] = asset.path, asset.url


class AnimationCanvasPage(CanvasPage):
    kind = "animation"

    @property
    def manifest(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self["animation"])

    @property
    def frame_paths(self) -> list[str]:
        paths = self.manifest.get("frame_paths")
        return [str(path) for path in paths] if type(paths) is list else []

    def restore(self, visual: VisualState) -> VisualResource | None:
        manifest = self.manifest
        animation_type = str(manifest.get("type") or "")
        resource: VisualResource = {
            "type": animation_type,
            "manifest": manifest,
            "prompt": visual.shown_image_prompt,
            "priority": visual.PRIORITY_SHOW,
            "source": "show_animation",
        }
        if self.path is not None:
            resource["path"] = self.path
        match animation_type:
            case "triframe":
                visual.shown_animation_frames = self.frame_paths
                resource["frame_paths"] = list(visual.shown_animation_frames)
            case "layered":
                visual.shown_layered_animation = dict(manifest)
            case "video":
                visual.shown_video_animation = dict(manifest)
            case _:
                return None
        return resource

    def presentation(self, resolve_url: Callable[[str], str]) -> dict[str, JsonValue]:
        result = super().presentation(resolve_url)
        if self.manifest.get("type") == "triframe" and "frame_paths" in self.manifest:
            result["animation"] = {**self.manifest, "frames": [resolve_url(path) for path in self.frame_paths]}
        return result

    def initialize_geometry(self, catalog: CanvasAssetCatalog) -> None:
        raise ValueError("Canvas animation observations require the animation catalog adapter.")


class GameCanvasPage(CanvasPage):
    kind = "game"

    @classmethod
    def create(cls, definition_hash: str, view_box: str) -> GameCanvasPage:
        components = view_box.replace(",", " ").split() or ["0", "0", "300", "150"]
        if len(components) != 4:
            raise ValueError("The game canvas requires a valid SVG viewBox.")
        x, y, width, height = (float(value) for value in components)
        if not all(map(isfinite, (x, y, width, height))) or min(width, height) <= 0:
            raise ValueError("The game canvas requires finite, positive SVG dimensions.")
        scale = 1000 / min(width, height) if min(width, height) < 1 else 1
        page = Page(
            id=uuid4().hex, scene_id=None, asset_id=f"game:{definition_hash}", media_kind="game",
            animation=None, width=round(width * scale), height=round(height * scale),
        )
        return cls({
            "page_id": page.id,
            "game_definition_hash": definition_hash,
            "game_view_box": " ".join(components),
            "canvas_page": page.model_dump(mode="json"),
        })

    def matches_game(self, definition_hash: str) -> bool:
        return self.get("game_definition_hash") == definition_hash

    def restore(self, visual: VisualState) -> None:
        # The game scene is supplied by GameState for the requesting viewer.
        return None
