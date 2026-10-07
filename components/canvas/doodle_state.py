"""Persisted doodle state and snapshot helpers."""

from __future__ import annotations

import base64
from collections.abc import Callable
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import unquote
from uuid import uuid4

if TYPE_CHECKING:
    from components.theater_manager import Theater

logger = logging.getLogger(__name__)

DoodleValue = str | float | int | bool | list[float] | None
DoodleAction = dict[str, DoodleValue]


def doodle_snapshot_batches(doodles: list[DoodleAction]) -> list[DoodleAction]:
    """Compact adjacent line segments with the same style into stroke paths."""
    batches: list[DoodleAction] = []
    for action in doodles:
        if action.get("type") != "draw":
            continue
        try:
            x0, y0, x1, y1 = float(action["x0"]), float(action["y0"]), float(action["x1"]), float(action["y1"])
        except (KeyError, TypeError, ValueError):
            continue
        color, size = action.get("color"), action.get("size", 3)
        if (
            batches
            and batches[-1]["color"] == color
            and batches[-1]["size"] == size
            and batches[-1]["points"][-2:] == [x0, y0]
        ):
            points = batches[-1]["points"]
            if type(points) is list:
                points.extend([x1, y1])
        else:
            batches.append({"color": color, "size": size, "points": [x0, y0, x1, y1]})
    return batches


class DoodleState:
    def __init__(
        self,
        persist: Callable[[], None],
        theater: Optional[Theater] = None,
        stamp_resolver: Optional[Callable[[DoodleAction], bytes | None]] = None,
    ) -> None:
        self._persist = persist
        self.theater = theater
        self.stamp_resolver = stamp_resolver
        self.doodles: list[DoodleAction] = []
        self.enabled = True
        self.persistent = False

    def add(self, doodles: list[DoodleAction] | DoodleAction) -> None:
        if type(doodles) is dict:
            doodles = [doodles]
        if any(doodle.get("type") == "clear" for doodle in doodles):
            self.doodles.clear()
        else:
            self.doodles.extend(doodles)
        self._persist()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._persist()

    def snapshot_batches(self) -> list[DoodleAction]:
        return doodle_snapshot_batches(self.doodles)

    def text_annotations(self) -> list[DoodleAction]:
        """Return persisted text actions for websocket snapshot replay."""
        changed = False
        for action in self.doodles:
            if action.get("type") == "text" and not action.get("id"):
                action["id"] = uuid4().hex
                changed = True
        if changed:
            self._persist()
        return [dict(action) for action in self.doodles if action.get("type") == "text"]

    def stamp_annotations(self) -> list[DoodleAction]:
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

    def save_stamp(self, action: DoodleAction) -> None:
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

    def resolve_stamp_image(
        self,
        action: DoodleAction,
        image_path: str | None = None,
        stamp_resolver: Optional[Callable[[DoodleAction], bytes | None]] = None,
    ) -> bytes | None:
        """Resolve stamp image bytes from an action payload, theater storage, or user stamp storage."""
        resolver = stamp_resolver if stamp_resolver is not None else self.stamp_resolver
        if resolver is not None:
            resolved = resolver(action)
            if resolved is not None:
                return resolved

        raw_bytes = action.get("image_bytes")
        if type(raw_bytes) is bytes:
            return raw_bytes

        url = str(action.get("url") or "").strip()

        if url.startswith("data:image/") and ";base64," in url:
            b64_content = url.split(";base64,", 1)[1]
            return base64.b64decode(b64_content)

        clean_url = url.removeprefix("file://")

        if clean_url:
            path = Path(clean_url)
            try:
                if path.is_file():
                    return path.read_bytes()
            except OSError:
                pass

        if image_path and clean_url:
            img_file = Path(image_path).resolve()
            base_dir = img_file.parent
            try:
                if (base_dir / clean_url).is_file():
                    return (base_dir / clean_url).read_bytes()
            except OSError:
                pass
            ancestors = [base_dir, *img_file.parents]
            for ancestor in ancestors:
                try:
                    rel_candidate = ancestor / clean_url.lstrip("/\\")
                    if rel_candidate.is_file():
                        return rel_candidate.read_bytes()
                    stamp_name = Path(clean_url).name
                    if stamp_name and (ancestor / "stamps" / stamp_name).is_file():
                        return (ancestor / "stamps" / stamp_name).read_bytes()
                except OSError:
                    pass

        target_theater_id = ""
        target_filename = ""

        stamp_id_str = str(action.get("stamp_id") or "")
        if stamp_id_str.startswith("theater:"):
            parts = stamp_id_str.split(":", 2)
            if len(parts) == 3:
                target_theater_id = parts[1]
                target_filename = parts[2]

        if not target_filename and "/theaters/" in url and "/stamps/" in url:
            after_theaters = url.split("/theaters/", 1)[1]
            if "/stamps/" in after_theaters:
                seg_theater_id, after_stamps = after_theaters.split("/stamps/", 1)
                target_theater_id = seg_theater_id
                target_filename = unquote(after_stamps.split("?")[0])

        if target_filename:
            if self.theater is not None:
                try:
                    cand = self.theater.stamps_dir() / target_filename
                    if cand.is_file():
                        return cand.read_bytes()
                except OSError:
                    pass
                if target_theater_id and self.theater.manager is not None:
                    try:
                        other_theater = self.theater.manager.theater(target_theater_id)
                        cand = other_theater.stamps_dir() / target_filename
                        if cand.is_file():
                            return cand.read_bytes()
                    except OSError:
                        pass
            try:
                from components.theater_manager import get_ephemeral_root
                if target_theater_id:
                    cand = get_ephemeral_root() / target_theater_id / "stamps" / target_filename
                    if cand.is_file():
                        return cand.read_bytes()
            except Exception:
                pass
            if image_path:
                img_file = Path(image_path).resolve()
                for ancestor in [img_file.parent, *img_file.parents]:
                    cand = ancestor / "stamps" / target_filename
                    try:
                        if cand.is_file():
                            return cand.read_bytes()
                    except OSError:
                        pass

        numeric_stamp_id: int | None = None
        raw_stamp_id = action.get("stamp_id")
        if raw_stamp_id is not None:
            try:
                numeric_stamp_id = int(str(raw_stamp_id))
            except (ValueError, TypeError):
                pass
        if numeric_stamp_id is None and "/api/stamps/" in url:
            try:
                numeric_stamp_id = int(url.split("/api/stamps/", 1)[1].split("?")[0].split("/")[0])
            except (ValueError, TypeError):
                pass

        user_id_int: int | None = None
        raw_user = action.get("user_id")
        if raw_user is not None:
            try:
                user_id_int = int(str(raw_user))
            except (ValueError, TypeError):
                pass

        if numeric_stamp_id is not None:
            try:
                from api_server.dependencies import db, stamp_storage
                if db:
                    stamp_record = db.get_stamp_by_id(numeric_stamp_id)
                    if stamp_record is not None:
                        rec_user_id = int(str(stamp_record.get("user_id", 0)))
                        rec_filename = str(stamp_record.get("filename", ""))
                        if stamp_storage:
                            data = stamp_storage.read_stamp(rec_user_id, rec_filename)
                            if data:
                                return data
            except Exception:
                pass

        try:
            from storage.stamp_storage import get_stamps_root
            stamps_root = get_stamps_root().resolve()
            if stamps_root.is_dir():
                candidate_dirs: list[Path] = []
                if user_id_int is not None:
                    candidate_dirs.append(stamps_root / str(user_id_int))
                candidate_dirs.append(stamps_root)
                stamp_name = str(action.get("name") or "").lower()
                for cdir in candidate_dirs:
                    if not cdir.is_dir():
                        continue
                    for entry in cdir.glob("*"):
                        if entry.is_file() and entry.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                            if stamp_name and stamp_name in entry.stem.lower():
                                return entry.read_bytes()
                            if numeric_stamp_id is not None and str(numeric_stamp_id) in entry.stem:
                                return entry.read_bytes()
        except Exception:
            pass

        return None

    def snapshot_png(
        self,
        image_path: str | None,
        stamp_resolver: Optional[Callable[[DoodleAction], bytes | None]] = None,
    ) -> bytes | None:
        """Render normalized strokes and stamps over the current scene for agent vision."""
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
                    if action.get("type") == "stamp":
                        stamp_bytes = self.resolve_stamp_image(
                            action,
                            image_path=image_path,
                            stamp_resolver=stamp_resolver,
                        )
                        if stamp_bytes is None:
                            continue
                        with Image.open(io.BytesIO(stamp_bytes)) as stamp_src:
                            stamp_img = stamp_src.convert("RGBA")
                        ref_width = 1000.0
                        stamp_scale = width / ref_width
                        base_size = max(16.0, float(str(action.get("size") or 80.0)))
                        target_size = max(16, round(base_size * stamp_scale))
                        stamp_img.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)
                        rotation = float(str(action.get("rotation") or 0)) % 360
                        if rotation:
                            stamp_img = stamp_img.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
                        stamp_w, stamp_h = stamp_img.size

                        center_x = float(str(action.get("x") or 0.5)) * width
                        center_y = float(str(action.get("y") or 0.5)) * height
                        top_left_x = round(center_x - stamp_w / 2.0)
                        top_left_y = round(center_y - stamp_h / 2.0)

                        src_x0 = max(0, -top_left_x)
                        src_y0 = max(0, -top_left_y)
                        dst_x0 = max(0, top_left_x)
                        dst_y0 = max(0, top_left_y)
                        dst_x1 = min(width, top_left_x + stamp_w)
                        dst_y1 = min(height, top_left_y + stamp_h)

                        crop_w = dst_x1 - dst_x0
                        crop_h = dst_y1 - dst_y0
                        if crop_w > 0 and crop_h > 0:
                            cropped = stamp_img.crop((src_x0, src_y0, src_x0 + crop_w, src_y0 + crop_h))
                            image.paste(cropped, (dst_x0, dst_y0), mask=cropped)
                    elif action.get("type") == "draw":
                        points = (
                            float(str(action["x0"])) * width,
                            float(str(action["y0"])) * height,
                            float(str(action["x1"])) * width,
                            float(str(action["y1"])) * height,
                        )
                        draw.line(
                            points,
                            fill=str(action.get("color", "#ffffff")),
                            width=max(1, round(float(str(action.get("size") or 3)) * scale)),
                        )
                    elif action.get("type") == "text":
                        font_size = max(12, round(float(str(action.get("size") or 32)) * scale))
                        try:
                            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
                        except OSError:
                            font = ImageFont.load_default()
                        draw.multiline_text(
                            (float(str(action["x"])) * width, float(str(action["y"])) * height),
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

    def load(self, data: dict[str, DoodleValue | list[DoodleAction]]) -> None:
        saved = data.get("doodles", [])
        if type(saved) is list:
            self.doodles = [item for item in saved if type(item) is dict]
        else:
            self.doodles = []
        self.enabled = bool(data.get("doodles_enabled", True))
        self.persistent = self.enabled and bool(data.get("doodles_persistent", False))

    def serialize(self) -> dict[str, list[DoodleAction] | bool]:
        return {"doodles": self.doodles, "doodles_enabled": self.enabled,
                "doodles_persistent": self.persistent}
