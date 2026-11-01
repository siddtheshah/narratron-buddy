"""Canonical bounds rotate in actual image space, independent of viewer layout."""
from decimal import Decimal

import pytest

from components.canvas.canvas_geometry import normalized, rounded, stamp_bounds, stamp_dimensions, text_dimensions
from game_engine.canvas_contract import Point


@pytest.mark.parametrize("value,expected", [(0.5, 1), (-0.5, -1), (2.5, 3), (-2.5, -3)])
def test_rounding_halves_go_away_from_zero(value: float, expected: int) -> None:
    assert rounded(value) == expected


def test_coordinate_conversion_does_not_use_binary_rounding_ties() -> None:
    assert normalized("0.00005") == 1
    assert normalized("0.99995") == 10000
    assert rounded(Decimal("-12.5")) == -13


@pytest.mark.parametrize("rotation,expected", [
    (0, {"x": 4500, "y": 4750, "width": 1000, "height": 500}),
    (90_000, {"x": 4875, "y": 4000, "width": 250, "height": 2000}),
    (180_000, {"x": 4500, "y": 4750, "width": 1000, "height": 500}),
    (270_000, {"x": 4875, "y": 4000, "width": 250, "height": 2000}),
])
def test_non_square_page_rotation_uses_the_renderer_image_plane(rotation: int, expected: dict[str, int]) -> None:
    assert stamp_bounds(Point(x=5000, y=5000), 1000, 500, rotation, 2000, 1000).model_dump() == expected


def test_stamp_dimensions_preserve_aspect_on_non_square_pages() -> None:
    assert stamp_dimensions(80, 100, 200, 2000, 1000) == (400, 1600)
    assert stamp_dimensions(80, 200, 100, 2000, 1000) == (800, 800)
    assert text_dimensions("Hello", 32, 2000, 1000) == (1200, 736, 640)


def test_rotated_bounds_can_extend_outside_the_image() -> None:
    bounds = stamp_bounds(Point(x=0, y=0), 1000, 1000, 0, 1000, 1000)
    assert bounds.x == -500 and bounds.y == -500
