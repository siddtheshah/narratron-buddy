import json
from pathlib import Path

from components.canvas.canvas_state_manager import CanvasStateManager


class FakeTheater:
    def __init__(self, theater_id: str, root: Path) -> None:
        self.theater_id, self.root = theater_id, root / theater_id

    def directory(self) -> Path:
        return self.root

    def output_dir(self) -> Path:
        return self.root / "output"

    def get_url_for_path(self, file_path: str) -> str:
        return f"/theaters/{self.theater_id}/{Path(file_path).name}"


class FakeTheaterManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def theater(self, theater_id: str) -> FakeTheater:
        return FakeTheater(theater_id, self.root)


def test_manager_serializes_components_and_bundles_their_canvas_payload(tmp_path: Path) -> None:
    manager = CanvasStateManager("moon", FakeTheaterManager(tmp_path))
    manager.visual.shown_image_path = "C:/art/moon.png"
    manager.visual.shown_image_prompt = "Moonlit harbor"
    manager.audio.update_music("night", ["night.mp3"])
    manager.ui.update({"surface_id": "hud", "messages": []})
    manager.story.narration = "A bell rings."

    document, files = manager.save_local_theater_data()
    saved = json.loads((tmp_path / "moon" / "theater.json").read_text(encoding="utf-8"))
    latest = manager.get_latest_state()

    assert files == []
    assert document["canvas_state"] == saved["canvas_state"]
    assert saved["canvas_state"]["current_playlist"] == "night"
    assert latest["latest"] == "/theaters/moon/moon.png"
    assert latest["interactive_surfaces"] == [{"surface_id": "hud", "messages": []}]
    assert latest["narration"] == "A bell rings."


def test_manager_hydrates_each_persisted_component(tmp_path: Path) -> None:
    directory = tmp_path / "restored"; directory.mkdir()
    (directory / "theater.json").write_text(json.dumps({"canvas_state": {
        "shown_image_path": "saved.png", "current_playlist": "score",
        "doodles_enabled": False, "viewer_collab_enabled": True,
        "narration": "Previously.", "chat_messages": [{"author": "a", "text": "hi"}],
    }}), encoding="utf-8")

    manager = CanvasStateManager("restored", FakeTheaterManager(tmp_path))

    assert manager.visual.shown_image_path == "saved.png"
    assert manager.audio.current_playlist == "score"
    assert manager.doodles.enabled is False
    assert manager.ui.viewer_collab_enabled is True
    assert manager.story.narration == "Previously."
    assert manager.chat.get_messages() == [{"author": "a", "text": "hi"}]
