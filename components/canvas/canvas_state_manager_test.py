from __future__ import annotations

import json
from pathlib import Path

from components.canvas.canvas_state_manager import CanvasStateManager


class FakeTheater:
    def __init__(self, theater_id: str, root: Path, manager: FakeTheaterManager | None = None) -> None:
        self.theater_id, self.root = theater_id, root / theater_id
        self.manager = manager
        self.storage_change_count = 0

    def storage_changed(self) -> None:
        self.storage_change_count += 1

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

    def config(self) -> dict[str, object]:
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
    directory = tmp_path / "restored"
    directory.mkdir()
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
    directory = tmp_path / "legacy"
    directory.mkdir()
    (directory / "theater_state.json").write_text(json.dumps({"canvas_state": {
        "shown_image_path": "legacy.png",
        "narration": "From legacy file.",
    }}), encoding="utf-8")

    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("legacy"))
    assert manager.visual.shown_image_path == "legacy.png"
    assert manager.story.narration == "From legacy file."


def test_manager_handles_corrupt_json_gracefully(tmp_path: Path) -> None:
    directory = tmp_path / "corrupted"
    directory.mkdir()
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


def test_doodles_are_restored_per_history_page_after_reload(tmp_path: Path) -> None:
    theater = FakeTheaterManager(tmp_path).theater("page_doodles")
    manager = CanvasStateManager(theater)
    manager.visual.show_image("first.png")
    actions = [{"type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2},
               {"type": "text", "id": "clue", "text": "A clue", "x": 0.3, "y": 0.4},
               {"type": "stamp", "id": "hero", "stamp_id": 1, "x": 0.5, "y": 0.5}]
    manager.doodles.add(actions)
    first_page_id = manager.doodles.page_id
    manager.doodles.set_enabled(False)
    manager.visual.show_image("second.png")
    assert manager.doodles.doodles == []
    assert manager.doodles.enabled is True
    assert manager.doodles.page_id != first_page_id
    manager.doodles.add([{"type": "text", "id": "second", "text": "Second clue"}])
    manager.persist()

    reloaded = CanvasStateManager(theater)
    assert reloaded.doodles.doodles[0]["id"] == "second"
    assert reloaded.visual.previous_image() is True
    assert reloaded.doodles.page_id == first_page_id
    assert reloaded.doodles.doodles == actions
    assert reloaded.doodles.enabled is False
    assert all("doodles" not in page for page in reloaded.get_latest_state()["history"])
    assert reloaded.serialized_state()["shown_images_history"][0]["doodles"] == actions
    reloaded.doodles.add([{"type": "clear"}])
    assert reloaded.visual.next_image() is True
    assert reloaded.doodles.doodles[0]["id"] == "second"
    assert reloaded.doodles.enabled is True
    assert reloaded.visual.select_history_image(0) is True
    assert reloaded.doodles.doodles == []


def test_representing_same_page_keeps_doodles_and_page_identity(tmp_path: Path) -> None:
    manager = CanvasStateManager(FakeTheaterManager(tmp_path).theater("same_page"))
    manager.visual.show_image("first.png")
    page_id = manager.doodles.page_id
    manager.doodles.add([{"type": "text", "text": "A clue"}])
    manager.visual.show_image("first.png", prompt="Updated prompt")
    manager._on_visual_changed(False)
    assert manager.doodles.page_id == page_id
    assert manager.doodles.doodles == [{"type": "text", "text": "A clue"}]


def test_legacy_global_doodles_migrate_only_to_active_history_page(tmp_path: Path) -> None:
    theater = FakeTheaterManager(tmp_path).theater("legacy_doodles")
    theater.directory().mkdir()
    (theater.directory() / "theater.json").write_text(json.dumps({"canvas_state": {
        "shown_image_path": "first.png", "orator_cursor": 0,
        "shown_images_history": [{"path": "first.png"}, {"path": "second.png"}],
        "doodles": [{"type": "text", "text": "Legacy clue"}],
        "doodles_enabled": False, "doodles_persistent": True,
    }}), encoding="utf-8")
    manager = CanvasStateManager(theater)
    assert manager.doodles.doodles == [{"type": "text", "text": "Legacy clue"}]
    assert manager.doodles.enabled is False
    assert manager.visual.next_image() is True
    assert manager.doodles.doodles == []
    assert manager.doodles.enabled is True
    assert "doodles_persistent" not in manager.serialized_state()


def test_repeated_visuals_and_history_trimming_keep_separate_doodles(tmp_path: Path) -> None:
    manager = CanvasStateManager(FakeTheaterManager(tmp_path).theater("trimmed_doodles"))
    manager.visual.show_image("repeated.png")
    manager.doodles.add([{"type": "text", "text": "First visit"}])
    manager.visual.show_image("other.png")
    manager.visual.show_image("repeated.png")
    assert manager.doodles.doodles == []
    manager.doodles.add([{"type": "text", "text": "Second visit"}])
    page_id = manager.doodles.page_id
    for index in range(99):
        manager.visual.show_image(f"scene_{index}.png")
    assert len(manager.visual.shown_images_history) == 100
    assert manager.visual.select_history_image(0) is True
    assert manager.doodles.page_id == page_id
    assert manager.doodles.doodles == [{"type": "text", "text": "Second visit"}]


def test_manager_initializes_and_persists_notepad(tmp_path: Path) -> None:
    theater = FakeTheaterManager(tmp_path).theater("notepad_theater")
    manager = CanvasStateManager(theater)
    assert manager.notepad is not None
    manager.notepad.update_sticky_note("Clue", "Golden key")
    assert manager.get_latest_state()["sticky_notes"] == [
        {"topic": "Clue", "info": "Golden key", "name": "Clue", "content": "Golden key"}
    ]
    manager.persist()

    reloaded = CanvasStateManager(theater)
    assert reloaded.notepad is not None
    present = reloaded.notepad.get_present_sticky_notes()
    assert len(present) == 1
    assert present[0]["topic"] == "Clue"
    assert present[0]["info"] == "Golden key"


def test_manager_hydrates_notepad_from_story_planning_state(tmp_path: Path) -> None:
    directory = tmp_path / "legacy_notes"
    directory.mkdir()
    (directory / "theater.json").write_text(json.dumps({
        "canvas_state": {
            "story_planning_state": {
                "sticky_notes": [{"topic": "Quest", "info": "Find the goblet"}],
            }
        }
    }), encoding="utf-8")

    theater = FakeTheaterManager(tmp_path).theater("legacy_notes")
    manager = CanvasStateManager(theater)
    assert manager.notepad is not None
    present = manager.notepad.get_present_sticky_notes()
    assert len(present) == 1
    assert present[0]["topic"] == "Quest"
    assert present[0]["info"] == "Find the goblet"


def test_latest_payload_never_transmits_history_doodle_content(tmp_path: Path) -> None:
    manager = CanvasStateManager(FakeTheaterManager(tmp_path).theater("lean_history"))
    for index in range(10):
        manager.visual.show_image(f"scene_{index}.png")
        manager.doodles.add([{"type": "text", "id": str(index), "text": f"Private page content {index}"}])
    payload = manager.get_latest_state()
    assert len(payload["history"]) == 10
    assert all("doodles" not in page for page in payload["history"])
    assert "Private page content" not in json.dumps(payload)
    assert "Private page content 0" in json.dumps(manager.serialized_state())
