from __future__ import annotations

import json
from pathlib import Path

from components.canvas.canvas_state_manager import CanvasStateManager


class FakeTheater:
    def __init__(self, theater_id: str, root: Path, manager: FakeTheaterManager | None = None) -> None:
        self.theater_id, self.root = theater_id, root / theater_id
        self.manager = manager

    def directory(self) -> Path:
        return self.root

    def output_dir(self) -> Path:
        return self.root / "output"

    def chats_dir(self) -> Path:
        return self.root / "chats"

    def image_artifacts_dir(self) -> Path:
        return self.root / "images"

    def references_dir(self) -> Path:
        return self.root / "references"

    def config(self) -> dict:
        return {}

    def get_url_for_path(self, file_path: str) -> str:
        return f"/theaters/{self.theater_id}/{Path(file_path).name}"


class FakeTheaterManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def theater(self, theater_id: str) -> FakeTheater:
        return FakeTheater(theater_id, self.root, manager=self)


def test_manager_serializes_components_and_bundles_their_canvas_payload(tmp_path: Path) -> None:
    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("moon"))
    manager.visual.shown_image_path = "C:/art/moon.png"
    manager.visual.shown_image_prompt = "Moonlit harbor"
    manager.audio.update_music("night", ["night.mp3"])
    manager.ui.upsert_surface({"surface_id": "hud", "messages": []})
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

    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("restored"))

    assert manager.visual.shown_image_path == "saved.png"
    assert manager.audio.current_playlist == "score"
    assert manager.doodles.enabled is False
    assert manager.ui.viewer_collab_enabled is True
    assert manager.story.narration == "Previously."
    assert manager.chat.get_messages() == [{"author": "a", "text": "hi"}]


def test_manager_hydrates_from_legacy_theater_state_json(tmp_path: Path) -> None:
    directory = tmp_path / "legacy"; directory.mkdir()
    (directory / "theater_state.json").write_text(json.dumps({"canvas_state": {
        "shown_image_path": "legacy.png",
        "narration": "From legacy file.",
    }}), encoding="utf-8")

    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("legacy"))
    assert manager.visual.shown_image_path == "legacy.png"
    assert manager.story.narration == "From legacy file."


def test_manager_handles_corrupt_json_gracefully(tmp_path: Path) -> None:
    directory = tmp_path / "corrupted"; directory.mkdir()
    (directory / "theater.json").write_text("{invalid json corrupt content...", encoding="utf-8")

    # Should not raise exception
    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("corrupted"))
    assert manager.visual.shown_image_path is None
    assert manager.story.narration == ""


def test_manager_notify_changed_delegates_to_connections(tmp_path: Path) -> None:
    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("notify_test"))
    initial_rev = manager.connections.state_revision

    manager.notify_changed("visual", "story")
    assert manager.connections.state_revision == initial_rev + 1


def test_manager_persist_writes_theater_json(tmp_path: Path) -> None:
    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("persist_test"))
    manager.story.narration = "Persisted narration"
    manager.persist()

    target = tmp_path / "persist_test" / "theater.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["canvas_state"]["narration"] == "Persisted narration"

