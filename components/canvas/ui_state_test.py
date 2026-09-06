from components.canvas.ui_state import UIState


def test_ui_state_owns_surface_collection() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.interactive_surfaces["hud"] = {"surface_id": "hud"}
    assert list(state.interactive_surfaces) == ["hud"]
