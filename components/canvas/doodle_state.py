"""Persisted doodle state and snapshot helpers."""

from collections.abc import Callable
import io
import logging
from typing import Any, Optional

from components.canvas_state_utils import doodle_snapshot_batches

logger = logging.getLogger(__name__)


class DoodleState:
    def __init__(self, persist: Callable[[], None]) -> None:
        self._persist = persist
        self.doodles: list[dict[str, object]] = []
        self.enabled = True

    def add(self, doodles: list[dict[str, object]] | dict[str, object]) -> None:
        if isinstance(doodles, dict):
            doodles = [doodles]
        if any(doodle.get("type") == "clear" for doodle in doodles):
            self.doodles.clear()
        else:
            self.doodles.extend(doodles)
        self._persist()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._persist()

    def snapshot_batches(self) -> list[dict[str, object]]:
        return doodle_snapshot_batches(self.doodles)

    def snapshot_png(self, image_path: str | None) -> bytes | None:
        """Render normalized strokes over the current scene for agent vision."""
        if not image_path or not self.doodles:
            return None
        try:
            from PIL import Image, ImageDraw

            with Image.open(image_path) as source:
                image = source.convert("RGBA")
            image.thumbnail((1600, 1600))
            draw = ImageDraw.Draw(image)
            width, height = image.size
            scale = max(1.0, min(width, height) / 900)
            for action in self.doodles:
                if action.get("type") != "draw":
                    continue
                try:
                    points = (
                        float(action["x0"]) * width,
                        float(action["y0"]) * height,
                        float(action["x1"]) * width,
                        float(action["y1"]) * height,
                    )
                    draw.line(
                        points,
                        fill=str(action.get("color", "#ffffff")),
                        width=max(1, round(float(action.get("size", 3)) * scale)),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG", compress_level=1)
            return output.getvalue()
        except Exception as exc:
            logger.warning("Failed to render viewer doodle snapshot: %s", exc)
            return None

    def load(self, data: dict[str, object]) -> None:
        saved = data.get("doodles", [])
        self.doodles = [item for item in saved if isinstance(item, dict)] if isinstance(saved, list) else []
        self.enabled = bool(data.get("doodles_enabled", True))

    def serialize(self) -> dict[str, object]:
        return {"doodles": self.doodles, "doodles_enabled": self.enabled}
