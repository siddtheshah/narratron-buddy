from components.canvas.doodle_state import DoodleState


def test_doodle_state_compacts_adjacent_segments_and_persists() -> None:
    writes: list[bool] = []
    state = DoodleState(lambda: writes.append(True))
    state.add([{ "type": "draw", "x0": 0, "y0": 0, "x1": .5, "y1": .5, "color": "#fff", "size": 3 },
               { "type": "draw", "x0": .5, "y0": .5, "x1": 1, "y1": 1, "color": "#fff", "size": 3 }])
    assert state.snapshot_batches() == [{"color": "#fff", "size": 3, "points": [0, 0, .5, .5, 1, 1]}]
    assert writes == [True]
