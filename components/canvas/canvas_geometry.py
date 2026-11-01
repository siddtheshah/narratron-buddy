"""Canonical image-plane geometry shared by canvas observations and rendering."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, ROUND_HALF_UP
import math

from game_engine.canvas_contract import Bounds, Point


def rounded(value: float | Decimal) -> int:
    """Round halves away from zero, including negative bounds origins."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalized(value: str | int | float | bool | list[float] | None) -> int:
    if type(value) not in (int, float, str):
        raise ValueError("Canvas coordinates must be finite numbers.")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("Canvas points must stay inside the image.")
    return rounded(Decimal(str(value)) * 10_000)


def stamp_dimensions(size: float, asset_width: int, asset_height: int, page_width: int, page_height: int) -> tuple[int, int]:
    """Migrate the old longest-side size on a 1000-unit image-width plane."""
    if min(asset_width, asset_height, page_width, page_height) <= 0 or not math.isfinite(size) or size <= 0:
        raise ValueError("Canvas image and stamp dimensions must be positive.")
    width = rounded(Decimal(str(size)) * 10 * min(Decimal(1), Decimal(asset_width) / asset_height))
    height = rounded(Decimal(width) * asset_height * page_width / (asset_width * page_height))
    return max(1, width), max(1, height)


def stamp_bounds(center: Point, width: int, height: int, rotation: int, page_width: int, page_height: int) -> Bounds:
    """Rotate in image pixels, then project the AABB to the normalized plane.

    Rotating the normalized coordinates directly distorts non-square pages.
    """
    if min(width, height, page_width, page_height) <= 0:
        raise ValueError("Canvas stamp and page dimensions must be positive.")
    angle = rotation * math.pi / 180_000
    cosine, sine = abs(math.cos(angle)), abs(math.sin(angle))
    # Snap exact right-angle transforms instead of leaking trig error into ties.
    if rotation % 90_000 == 0:
        cosine, sine = ((1.0, 0.0) if rotation % 180_000 == 0 else (0.0, 1.0))
    rotated_width = width * cosine + height * page_height / page_width * sine
    rotated_height = height * cosine + width * page_width / page_height * sine
    return Bounds(
        x=rounded(center.x - rotated_width / 2), y=rounded(center.y - rotated_height / 2),
        width=rounded(rotated_width), height=rounded(rotated_height),
    )


def path_bounds(points: Sequence[Point]) -> Bounds:
    if not points:
        raise ValueError("Completed canvas strokes require at least one point.")
    left, top = min(point.x for point in points), min(point.y for point in points)
    right, bottom = max(point.x for point in points), max(point.y for point in points)
    return Bounds(x=left, y=top, width=right - left, height=bottom - top)


def text_dimensions(text: str, size: float, page_width: int, page_height: int) -> tuple[int, int, int]:
    """Use a fixed image-plane text box; renderers wrap/clip to this same box.

    The compatibility text size becomes pixels on a fixed 1000-pixel-wide plane.
    A box describes text layout, not opaque glyph contact or semantic intent.
    """
    if min(page_width, page_height) <= 0 or not math.isfinite(size) or size <= 0:
        raise ValueError("Canvas text and page dimensions must be positive.")
    lines = text.split("\n")[:4]
    font_size = max(1, rounded(Decimal(str(size)) * 10 * page_width / page_height))
    width = max(1, rounded(Decimal(str(size)) * 10 * Decimal("0.75") * max(1, max(map(len, lines), default=0))))
    height = max(1, rounded(Decimal(font_size) * Decimal("1.15") * max(1, len(lines))))
    return width, height, font_size


def required_integer(action: Mapping[str, str | float | int | bool | list[float] | None], field: str) -> int:
    value = action.get(field)
    if type(value) is not int:
        raise ValueError(f"Canvas object has no canonical {field}; migrate its geometry before observing it.")
    return value
