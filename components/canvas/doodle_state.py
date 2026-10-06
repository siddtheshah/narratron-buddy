"""Persisted doodle state and snapshot helpers."""

from collections.abc import Callable
import io
import logging
from uuid import uuid4

logger = logging.getLogger(__name__)


def doodle_snapshot_batches(doodles: list[dict[str, object]]) -> list[dict[str, object]]:
    """Compact adjacent line segments with the same style into stroke paths."""
    batches: list[dict[str, object]] = []
    for action in doodles:
        if action.get("type") != "draw":
            continue
        try:
            x0, y0, x1, y1 = action["x0"], action["y0"], action["x1"], action["y1"]
        except KeyError:
            continue
        color, size = action.get("color"), action.get("size", 3)
        if batches and batches[-1]["color"] == color and batches[-1]["size"] == size and batches[-1]["points"][-2:] == [x0, y0]:
            batches[-1]["points"].extend([x1, y1])
        else:
            batches.append({"color": color, "size": size, "points": [x0, y0, x1, y1]})
    return batches


class DoodleState:
    def __init__(self, persist: Callable[[], None]) -> None:
        self._persist = persist
        self.doodles: list[dict[str, object]] = []
        self.enabled = True
        self.persistent = False

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

    def text_annotations(self) -> list[dict[str, object]]:
        """Return persisted text actions for websocket snapshot replay."""
        changed = False
        for action in self.doodles:
            if action.get("type") == "text" and not action.get("id"):
                action["id"] = uuid4().hex
                changed = True
        if changed:
            self._persist()
        return [dict(action) for action in self.doodles if action.get("type") == "text"]

    def stamp_annotations(self) -> list[dict[str, str | float | int | None]]:
        """Return persisted stamp actions for websocket snapshot replay."""
        changed = False
        for action in self.doodles:
            if action.get("type") == "stamp" and not action.get("id"):
                action["id"] = uuid4().hex
                changed = True
        if changed:
            self._persist()
        return [dict(action) for action in self.doodles if action.get("type") == "stamp"]

    def save_text(self, action: dict[str, str | float]) -> None:
        """Replace a text annotation in place, preserving its drawing order."""
        for index, existing in enumerate(self.doodles):
            if existing.get("type") == "text" and existing.get("id") == action["id"]:
                self.doodles[index] = dict(action)
                self._persist()
                return
        self.doodles.append(dict(action))
        self._persist()

    def save_stamp(self, action: dict[str, str | float | int | None]) -> None:
        """Replace or add a stamp on the canvas.

        Enforces that a user can only have one stamp of a kind on the canvas at a time.
        Pulling another of the same kind replaces the existing one.
        Stamps are layered under doodles, with the most recent stamp at the front of the stamp layer.
        """
        stamp_uuid = action.get("id")
        user_id = action.get("user_id")
        stamp_id = action.get("stamp_id")

        for index, existing in enumerate(self.doodles):
            if existing.get("type") == "stamp":
                if stamp_uuid and existing.get("id") == stamp_uuid:
                    self.doodles.pop(index)
                    break
                elif (
                    user_id is not None
                    and stamp_id is not None
                    and existing.get("user_id") == user_id
                    and existing.get("stamp_id") == stamp_id
                ):
                    self.doodles.pop(index)
                    break

        first_non_stamp = next(
            (idx for idx, item in enumerate(self.doodles) if item.get("type") != "stamp"),
            len(self.doodles),
        )
        self.doodles.insert(first_non_stamp, dict(action))
        self._persist()

    def select_stamp(self, stamp_annotation_id: str) -> bool:
        """Move a stamp to the front of stamps (under doodles) when selected."""
        for index, action in enumerate(self.doodles):
            if action.get("type") == "stamp" and action.get("id") == stamp_annotation_id:
                stamp = self.doodles.pop(index)
                first_non_stamp = next(
                    (idx for idx, item in enumerate(self.doodles) if item.get("type") != "stamp"),
                    len(self.doodles),
                )
                self.doodles.insert(first_non_stamp, stamp)
                self._persist()
                return True
        return False

    def remove_stamp(self, stamp_annotation_id: str) -> None:
        """Remove a stamp annotation by its unique annotation ID."""
        original_len = len(self.doodles)
        self.doodles = [
            action
            for action in self.doodles
            if not (action.get("type") == "stamp" and action.get("id") == stamp_annotation_id)
        ]
        if len(self.doodles) != original_len:
            self._persist()

    def has_visible_annotations(self) -> bool:
        """Return whether the canvas has a stroke, non-empty text, or stamp annotation."""
        return any(
            action.get("type") == "draw"
            or (action.get("type") == "text" and bool(str(action.get("text", "")).strip()))
            or action.get("type") == "stamp"
            for action in self.doodles
        )

    def snapshot_png(self, image_path: str | None) -> bytes | None:
        """Render normalized strokes over the current scene for agent vision."""
        if not image_path or not self.doodles:
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont

            with Image.open(image_path) as source:
                image = source.convert("RGBA")
            image.thumbnail((1600, 1600))
            draw = ImageDraw.Draw(image)
            width, height = image.size
            scale = max(1.0, min(width, height) / 900)
            for action in self.doodles:
                try:
                    if action.get("type") == "draw":
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
                    elif action.get("type") == "text":
                        font_size = max(12, round(float(action.get("size", 32)) * scale))
                        try:
                            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
                        except OSError:
                            font = ImageFont.load_default()
                        draw.multiline_text(
                            (float(action["x"]) * width, float(action["y"]) * height),
                            str(action.get("text", "")),
                            fill=str(action.get("color", "#ffffff")),
                            font=font,
                            stroke_width=max(1, round(font_size / 10)),
                            stroke_fill="#000000",
                            spacing=max(2, round(font_size * 0.15)),
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
        self.persistent = self.enabled and bool(data.get("doodles_persistent", False))

    def serialize(self) -> dict[str, object]:
        return {"doodles": self.doodles, "doodles_enabled": self.enabled,
                "doodles_persistent": self.persistent}
