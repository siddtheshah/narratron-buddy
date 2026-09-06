import io
import tempfile
from pathlib import Path
from PIL import Image

from components.canvas.doodle_state import DoodleState, doodle_snapshot_batches


def test_doodle_state_compacts_adjacent_segments_and_persists() -> None:
    writes: list[bool] = []
    state = DoodleState(lambda: writes.append(True))
    state.add([{ "type": "draw", "x0": 0, "y0": 0, "x1": .5, "y1": .5, "color": "#fff", "size": 3 },
               { "type": "draw", "x0": .5, "y0": .5, "x1": 1, "y1": 1, "color": "#fff", "size": 3 }])
    assert state.snapshot_batches() == [{"color": "#fff", "size": 3, "points": [0, 0, .5, .5, 1, 1]}]
    assert writes == [True]


def test_doodle_state_add_single_dict_and_clear() -> None:
    writes: list[bool] = []
    state = DoodleState(lambda: writes.append(True))
    state.add({"type": "draw", "x0": 0, "y0": 0, "x1": 0.2, "y1": 0.2, "color": "#ff0000", "size": 5})
    assert len(state.doodles) == 1
    assert len(state.snapshot_batches()) == 1


    state.add({"type": "clear"})
    assert state.doodles == []
    assert len(writes) == 2


def test_doodle_state_set_enabled_persists() -> None:
    writes: list[bool] = []
    state = DoodleState(lambda: writes.append(True))
    state.set_enabled(False)
    assert state.enabled is False
    assert writes == [True]

    state.set_enabled(True)
    assert state.enabled is True
    assert len(writes) == 2


def test_doodle_state_snapshot_png_renders_strokes() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        img_path = Path(temp_dir) / "test_scene.png"
        img = Image.new("RGBA", (200, 200), (0, 0, 0, 255))
        img.save(img_path)

        state = DoodleState(lambda: None)
        assert state.snapshot_png(None) is None
        assert state.snapshot_png(str(img_path)) is None

        state.add([
            {"type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.9, "color": "#ff0000", "size": 10},
        ])

        png_bytes = state.snapshot_png(str(img_path))
        assert png_bytes is not None
        assert png_bytes.startswith(b"\x89PNG")

        with Image.open(io.BytesIO(png_bytes)) as rendered:
            assert rendered.size[0] > 0
            # Red stroke drawn across diagonal
            pixel = rendered.getpixel((100, 100))
            assert pixel[0] > 100  # Red channel should be bright


def test_doodle_state_load_and_serialize() -> None:
    state = DoodleState(lambda: None)
    serialized = {"doodles": [{"type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}], "doodles_enabled": False}
    state.load(serialized)
    assert state.enabled is False
    assert len(state.doodles) == 1
    assert state.serialize() == serialized


def test_doodle_snapshot_batches_styling_and_filtering() -> None:
    doodles = [
        {"type": "draw", "x0": 0, "y0": 0, "x1": 1, "y1": 1, "color": "#ff0000", "size": 2},
        # Same end coordinate, but different color -> separate batch
        {"type": "draw", "x0": 1, "y0": 1, "x1": 2, "y1": 2, "color": "#00ff00", "size": 2},
        # Same color, but different size -> separate batch
        {"type": "draw", "x0": 2, "y0": 2, "x1": 3, "y1": 3, "color": "#00ff00", "size": 5},
        # Non-draw action -> filtered out
        {"type": "text", "text": "hello"},
        # Missing coordinates -> ignored without error
        {"type": "draw", "x0": 0},
    ]

    batches = doodle_snapshot_batches(doodles)
    assert len(batches) == 3
    assert batches[0] == {"color": "#ff0000", "size": 2, "points": [0, 0, 1, 1]}
    assert batches[1] == {"color": "#00ff00", "size": 2, "points": [1, 1, 2, 2]}
    assert batches[2] == {"color": "#00ff00", "size": 5, "points": [2, 2, 3, 3]}


def test_doodle_state_snapshot_png_handles_malformed_stroke_coordinates() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        img_path = Path(temp_dir) / "base.png"
        Image.new("RGBA", (100, 100), (255, 255, 255, 255)).save(img_path)

        state = DoodleState(lambda: None)
        state.add([
            # Valid stroke with default color and size
            {"type": "draw", "x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0},
            # Malformed stroke with unparseable coordinates
            {"type": "draw", "x0": "invalid", "y0": 0.0, "x1": 1.0, "y1": 1.0},
            # Non-draw stroke
            {"type": "comment", "content": "note"},
        ])

        png = state.snapshot_png(str(img_path))
        assert png is not None
        assert png.startswith(b"\x89PNG")


def test_doodle_state_load_handles_malformed_payload() -> None:
    state = DoodleState(lambda: None)

    # Empty payload defaults enabled to True and doodles to []
    state.load({})
    assert state.enabled is True
    assert state.doodles == []

    # Non-list doodles and missing enabled flag
    state.load({"doodles": "not_a_list"})
    assert state.enabled is True
    assert state.doodles == []

