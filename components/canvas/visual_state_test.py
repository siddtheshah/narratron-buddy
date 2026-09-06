from components.canvas.visual_state import VisualState


def test_visual_state_starts_with_a_crossfade_presentation() -> None:
    state = VisualState()
    assert state.shown_image_path is None
    assert (state.shown_image_transition, state.shown_image_effect) == ("crossfade", "gleam3")
