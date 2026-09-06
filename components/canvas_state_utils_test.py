from components.canvas_state_utils import clamp_surface_placement


def test_clamp_surface_placement_bounds_coordinates() -> None:
    assert clamp_surface_placement(-1, 120) == {"left_pct": 2.0, "top_pct": 98.0}
