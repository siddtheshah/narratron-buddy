"""Project the persisted canvas objects onto the CEL observation contract."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from hashlib import sha256
import io
import math
from pathlib import PurePosixPath
from uuid import uuid4

from PIL import Image

from components.canvas.canvas_geometry import (
    normalized, path_bounds, required_integer, rounded, stamp_bounds, stamp_dimensions, text_dimensions,
)
from components.canvas.doodle_state import DoodleAction
from game_engine.canvas_contract import (
    Annotation, Bounds, CanvasModel, CanvasSnapshot, Drawing, Overlay, Page, Placement, Point, Region, Size,
)


class ObservationClock(CanvasModel):
    revision: Size = 0
    fingerprint: str = ""


def canonicalize_actions(
    actions: Sequence[DoodleAction], page: Page, stamp_resolver: Callable[[DoodleAction], bytes | None],
) -> list[DoodleAction]:
    """Migrate real saved objects, without guessing geometry from a viewport.

    Callers persist the returned objects in their original page, then broadcast
    those same objects. Legacy contiguous segments get their stroke IDs once.
    """
    result: list[DoodleAction] = []
    previous: DoodleAction | None = None
    stroke_tails: dict[str, DoodleAction] = {}
    stroke_components: set[str] = set()
    for saved in actions:
        action = dict(saved)
        kind = action.get("type")
        if kind in {"draw", "stamp", "text"}:
            action["canvas_page_width"] = page.width
            action["canvas_page_height"] = page.height
        if kind == "draw":
            if not action.get("stroke_id"):
                connected = (
                    previous is not None and previous.get("type") == "draw"
                    and previous.get("x1") == action.get("x0") and previous.get("y1") == action.get("y0")
                    and previous.get("color") == action.get("color") and previous.get("size") == action.get("size")
                )
                action["stroke_id"] = previous["stroke_id"] if connected and previous is not None else uuid4().hex
            action.setdefault("id", uuid4().hex)
            size = float(str(action.get("size", 3)))
            if not math.isfinite(size) or size <= 0:
                raise ValueError("Canvas strokes require a positive finite width.")
            action["canvas_line_width"] = max(1, rounded(size * 10))
            raw_stroke_id = str(action["stroke_id"])
            tail = stroke_tails.get(raw_stroke_id)
            component = str(action.get("canvas_stroke_id") or raw_stroke_id)
            connected = (
                tail is not None and tail.get("x1") == action.get("x0") and tail.get("y1") == action.get("y0")
                and tail.get("canvas_line_width") == action["canvas_line_width"]
            )
            if connected and (
                "canvas_stroke_id" not in action or component == tail["canvas_stroke_id"] or component in stroke_components
            ):
                component = str(tail["canvas_stroke_id"])
            elif component in stroke_components:
                # Removing a middle segment splits a path. Give the remaining
                # component its own durable ID instead of drawing a false bridge.
                component = "stroke:" + sha256(f"{raw_stroke_id}:{action['id']}".encode("utf-8")).hexdigest()
            action["canvas_stroke_id"] = component
            stroke_components.add(component)
            stroke_tails[raw_stroke_id] = action
        elif kind == "stamp":
            action.setdefault("id", uuid4().hex)
            size = float(str(action.get("size", 80)))
            if "canvas_asset_id" not in action or "canvas_width" not in action or action.get("canvas_size") != size:
                data = stamp_resolver(action)
                if data is None:
                    raise ValueError("Canvas stamp media is unavailable; its bounds cannot be certified.")
                with Image.open(io.BytesIO(data)) as image:
                    width, height = image.size
                action["canvas_width"], action["canvas_height"] = stamp_dimensions(size, width, height, page.width, page.height)
                action["canvas_size"] = size
                if "canvas_asset_id" not in action:
                    stamp_id = str(action.get("stamp_id", ""))
                    action["canvas_asset_id"] = (
                        f"stamp:{PurePosixPath(stamp_id.split(':', 2)[2]).stem}"
                        if stamp_id.startswith("theater:") and len(stamp_id.split(":", 2)) == 3
                        else f"user-asset:{sha256(data).hexdigest()}"
                    )
        elif kind == "text":
            action.setdefault("id", uuid4().hex)
            text = str(action.get("text", ""))
            width, height, font_size = text_dimensions(text, float(str(action.get("size", 32))), page.width, page.height)
            action.update(canvas_width=width, canvas_height=height, canvas_font_size=font_size)
        result.append(action)
        previous = action
    return result


def observe_page(
    page: Page | None, actions: Sequence[DoodleAction], *, revision: int,
    regions: dict[str, Region] | None = None, tray: Sequence[str] = (), overlay: Overlay | None = None,
) -> CanvasSnapshot:
    """Read canonical objects only; never truncate, infer a target, or mutate them."""
    if page is None:
        empty = CanvasSnapshot.empty(revision).model_dump(mode="json")
        empty["tray"] = list(tray)
        return CanvasSnapshot.model_validate(empty)
    stamps: dict[str, Placement] = {}
    annotations: dict[str, Annotation] = {}
    paths: dict[str, list[Point]] = {}
    widths: dict[str, int] = {}
    for action in actions:
        kind = action.get("type")
        if kind == "stamp":
            center = Point(x=normalized(action.get("x")), y=normalized(action.get("y")))
            degrees = float(str(action.get("rotation", 0)))
            if not math.isfinite(degrees):
                raise ValueError("Canvas stamp rotation must be finite.")
            rotation = rounded(degrees * 1000) % 360_000
            width, height = required_integer(action, "canvas_width"), required_integer(action, "canvas_height")
            placement = Placement.model_validate({
                "id": action.get("id"), "asset_id": action.get("canvas_asset_id"),
                "managed_by": action.get("managed_by", "user"), "game_key": action.get("game_key"),
                "draggable": action.get("draggable", True), "center": center.model_dump(),
                "bounds": stamp_bounds(center, width, height, rotation, page.width, page.height).model_dump(),
                "rotation": rotation,
            })
            if placement.id in stamps:
                raise ValueError("Duplicate canvas placement ID.")
            stamps[placement.id] = placement
        elif kind == "text":
            annotation = Annotation.model_validate({
                "id": action.get("id"), "text": action.get("text"),
                "bounds": Bounds(
                    x=normalized(action.get("x")), y=normalized(action.get("y")),
                    width=required_integer(action, "canvas_width"), height=required_integer(action, "canvas_height"),
                ).model_dump(),
            })
            if annotation.id in annotations:
                raise ValueError("Duplicate canvas annotation ID.")
            annotations[annotation.id] = annotation
        elif kind == "draw":
            stroke_id = action.get("canvas_stroke_id", action.get("stroke_id"))
            if type(stroke_id) is not str or not stroke_id:
                raise ValueError("Canvas strokes need stable IDs before observation.")
            start = Point(x=normalized(action.get("x0")), y=normalized(action.get("y0")))
            end = Point(x=normalized(action.get("x1")), y=normalized(action.get("y1")))
            line_width = required_integer(action, "canvas_line_width")
            if stroke_id not in paths:
                paths[stroke_id] = [start]
                widths[stroke_id] = line_width
            if paths[stroke_id][-1] != start or widths[stroke_id] != line_width:
                raise ValueError("A canvas stroke must be a continuous path with one width.")
            paths[stroke_id].append(end)
    drawings: dict[str, Drawing] = {}
    for stroke_id, points in paths.items():
        bounds = path_bounds(points)
        line_width = widths[stroke_id]
        line_height = line_width * page.width / page.height
        drawings[stroke_id] = Drawing(
            id=stroke_id, points=points, bounds=Bounds(
                x=rounded(bounds.x - line_width / 2), y=rounded(bounds.y - line_height / 2),
                width=bounds.width + line_width, height=bounds.height + rounded(line_height),
            ),
        )
    return CanvasSnapshot(
        version=1, revision=revision, page=page, stamps=stamps, drawings=drawings,
        annotations=annotations, regions=regions or {}, tray=list(tray),
        overlay=overlay if overlay is not None else Overlay(items=[], filters=[]), event=None,
    )
