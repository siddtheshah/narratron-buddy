from __future__ import annotations

import asyncio
import json
from shutil import copytree
from typing import cast
from fastapi import WebSocket
from PIL import Image
from pydantic import JsonValue
import pytest
from pathlib import Path

from components.canvas.canvas_state_manager import CanvasStateManager
from game_engine.game_runtime import GameAction
from game_engine.game_state import StaleGameAction
from testing.base import CanvasTestCase


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

    def characters(self) -> list[dict[str, object]]:
        return [{"id": "c1", "name": "Mira", "description": "Hero"}]

    def scenes(self) -> list[dict[str, object]]:
        return [{"id": "s1", "name": "Castle", "description": "High tower"}]

    def animations_dir(self) -> Path:
        anim_dir = self.root / "animations"
        anim_dir.mkdir(parents=True, exist_ok=True)
        return anim_dir


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
    page = manager.get_latest_state()["history"][0]
    assert page["page_id"] == manager.doodles.page_id
    page_doodles = manager.doodles_for_page(page["page_id"])
    assert page_doodles is not None and not page_doodles.enabled
    reloaded = CanvasStateManager(theater_manager.theater("restored"))
    assert reloaded.get_latest_state()["history"][0]["page_id"] == page["page_id"]


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


def test_game_page_round_trips_with_media_history_and_its_own_doodles(tmp_path: Path) -> None:
    theater = FakeTheaterManager(tmp_path).theater("relay")
    example = Path(__file__).resolve().parents[2] / "games" / "canvas_scene_demo" / "game"
    copytree(example, theater.directory() / "game")
    manager = CanvasStateManager(theater)
    manager.visual.show_image("cover.png")
    manager.visual.show_triframe(["one.png", "two.png", "three.png"])
    animation_page = manager.visual.current_page()
    assert animation_page is not None
    manager.doodles.set_enabled(False)

    manager.show_game_page()
    game_page = manager.visual.current_page()
    assert game_page is not None
    assert game_page.kind == "game"
    assert manager.visual.preview_image_url() is None
    assert manager.doodles.enabled
    manager.doodles.add({"type": "draw", "x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.4, "size": 3})
    observed = manager.canvas_observation()
    assert observed.page is not None
    assert (observed.page.media_kind, observed.page.width, observed.page.height) == ("game", 960, 540)
    assert manager.game is not None
    manager.game.act(GameAction(name="advance", arguments={}, revision=0), expected_page_id=game_page.id)

    assert manager.visual.previous_image()
    assert manager.visual.shown_animation_frames == ["one.png", "two.png", "three.png"]
    assert not manager.doodles.enabled
    with pytest.raises(StaleGameAction, match="shared game page changed"):
        manager.game.act(GameAction(name="charge", arguments={}, revision=1), expected_page_id=game_page.id)
    assert manager.visual.previous_image()
    assert manager.visual.shown_image_path == "cover.png"
    assert manager.visual.next_image()
    assert manager.doodles.page_id == animation_page.id
    assert manager.visual.select_history_page(game_page.id)
    manager.show_game_page()

    reloaded = CanvasStateManager(theater)
    assert [page.kind for page in reloaded.visual.shown_images_history] == ["image", "animation", "game"]
    assert reloaded.doodles.page_id == game_page.id
    assert len(reloaded.doodles.snapshot()["batches"]) == 1
    assert reloaded.game is not None
    assert reloaded.game.revision == 1


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


def test_notify_characters_updated_broadcasts_and_invokes_callback(tmp_path: Path) -> None:
    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("chars_test"))
    received: list[list[dict[str, JsonValue]]] = []
    manager.on_characters_updated = received.append
    manager.notify_characters_updated()
    assert len(received) == 1
    assert received[0][0]["name"] == "Mira"


def test_notify_scenes_updated_broadcasts_and_invokes_callback(tmp_path: Path) -> None:
    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("scenes_test"))
    received: list[list[dict[str, JsonValue]]] = []
    manager.on_scenes_updated = received.append
    manager.notify_scenes_updated()
    assert len(received) == 1
    assert received[0][0]["name"] == "Castle"


def test_notify_animations_updated_broadcasts_and_invokes_callback(tmp_path: Path) -> None:
    theater_manager = FakeTheaterManager(tmp_path)
    theater = theater_manager.theater("anims_test")
    manager = CanvasStateManager(theater)
    received: list[list[dict[str, JsonValue]]] = []
    manager.on_animations_updated = received.append
    manager.notify_animations_updated()
    assert len(received) == 1


@pytest.mark.asyncio
async def test_broadcast_doodle_dispatches_to_websocket(tmp_path: Path) -> None:
    theater_manager = FakeTheaterManager(tmp_path)
    manager = CanvasStateManager(theater_manager.theater("ws_test"))
    sent: list[dict[str, JsonValue]] = []

    class FakeWebSocket:
        async def send_json(self, data: dict[str, JsonValue]) -> None:
            sent.append(data)

    fake_ws = cast(WebSocket, FakeWebSocket())
    manager.connections.active_ws_connections.append(fake_ws)
    manager.connections.state_ws_loop = asyncio.get_running_loop()
    manager.notify_characters_updated()
    await asyncio.sleep(0.01)
    assert len(sent) == 1
    assert sent[0]["type"] == "theater_characters_updated"
    assert sent[0]["characters"][0]["name"] == "Mira"


class TestDoodles(CanvasTestCase):
    def test_doodles_persist_and_reset_when_cleared(self) -> None:
        theater_id = "doodle_persistence"
        first_image = self.workspace / "img1.jpg"
        second_image = self.workspace / "img2.jpg"
        first_image.write_bytes(b"image one")
        second_image.write_bytes(b"image two")
        manager = self.make_canvas_state(theater_id)
        manager.visual.show_image(str(first_image))

        first_doodle = {
            "type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2,
            "color": "#ffffff", "size": 3,
        }
        second_doodle = {
            "type": "draw", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3,
            "color": "#ff0000", "size": 5,
        }
        manager.doodles.add([first_doodle])
        manager.doodles.add([second_doodle])

        theater_file = self.theaters_dir / theater_id / "theater.json"
        saved = json.loads(theater_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["canvas_state"]["doodles"], [first_doodle, second_doodle])
        reloaded = self.make_canvas_state(theater_id)
        self.assertEqual(reloaded.doodles.doodles, [first_doodle, second_doodle])

        manager.visual.show_image(str(second_image))
        self.assertEqual(manager.doodles.doodles, [])
        manager.visual.previous_image()
        self.assertEqual(manager.doodles.doodles, [first_doodle, second_doodle])

        manager.doodles.add([{"type": "clear"}])
        self.assertEqual(manager.doodles.doodles, [])
        saved = json.loads(theater_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["canvas_state"]["doodles"], [])
        self.assertEqual(self.make_canvas_state(theater_id).doodles.doodles, [])


class TestImageHistoryPaging(CanvasTestCase):
    def test_image_history_is_capped_at_100_entries(self) -> None:
        manager = self.make_canvas_state("history_cap")

        for index in range(105):
            manager.visual.show_image(f"/virtual/path/to/image_{index}.png")

        history = manager.get_latest_state()["history"]
        self.assertEqual(len(manager.visual.shown_images_history), 100)
        self.assertEqual(len(history), 100)
        self.assertIn("image_5.png", history[0]["path"])
        self.assertIn("image_104.png", history[-1]["path"])

    def test_image_history_payload_contains_presentation_details(self) -> None:
        first_image = self.workspace / "scene1.png"
        second_image = self.workspace / "scene2.jpg"
        first_image.write_bytes(b"scene1_data")
        second_image.write_bytes(b"scene2_data")

        manager = self.make_canvas_state("history_payload")
        manager.visual.show_image(str(first_image), transition="fade", url_for_path=manager.theater.get_url_for_path)
        manager.visual.show_image(str(second_image), transition="crossfade", url_for_path=manager.theater.get_url_for_path)

        history = manager.get_latest_state()["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["path"], str(first_image))
        self.assertEqual(history[0]["transition"], "fade")
        self.assertEqual(history[0]["effect"], "gleam3")
        self.assertEqual(history[1]["path"], str(second_image))
        self.assertEqual(history[1]["transition"], "crossfade")
        self.assertEqual(history[1]["effect"], "gleam3")
        self.assertTrue(history[0]["url"])
        self.assertIn("prompt", history[0])

    def test_history_persists_in_the_test_theater_directory(self) -> None:
        theater_id = "history_persistence"
        manager = self.make_canvas_state(theater_id)
        manager.visual.show_image("/path/a.png", transition="fade")
        manager.visual.show_image("/path/b.png", transition="crossfade")

        manager.save_local_theater_data(theater_dir=self.theaters_dir / theater_id)

        reloaded_manager = self.make_canvas_state(theater_id)
        self.assertEqual(len(reloaded_manager.visual.shown_images_history), 2)

    def test_animations_are_recorded_in_history_with_original_prompt_and_payload(self) -> None:
        theater_id = "anim_history_theater"
        manager = self.make_canvas_state(theater_id)

        # 1. Static image
        img_file = self.workspace / "initial_scene.png"
        img_file.write_bytes(b"initial_data")
        manager.visual.show_image(str(img_file), url_for_path=manager.theater.get_url_for_path)

        # 2. Tri-frame animation with adjacent triframe.json
        anim_dir = manager.theater.animations_dir() / "crystal_glow"
        anim_dir.mkdir(parents=True)
        frame_paths = []
        for i in range(1, 4):
            fp = anim_dir / f"frame_{i}.jpg"
            fp.write_bytes(b"frame_data")
            frame_paths.append(str(fp))
        (anim_dir / "triframe.json").write_text(json.dumps({
            "id": "crystal_glow",
            "type": "triframe",
            "scene_prompt": "A glowing mystical crystal in an ancient cavern.",
        }), encoding="utf-8")

        manager.visual.show_triframe(
            frame_paths,
            prompt="A glowing mystical crystal in an ancient cavern.",
            url_for_path=manager.theater.get_url_for_path,
        )

        # 3. Video animation
        vid_dir = manager.theater.animations_dir() / "dragon_flight"
        vid_dir.mkdir(parents=True)
        vid_file = vid_dir / "video.mp4"
        vid_file.write_bytes(b"video_bytes")
        (vid_dir / "video.json").write_text(json.dumps({
            "id": "dragon_flight",
            "type": "video",
            "scene_prompt": "A fire-breathing dragon soaring above a volcanic crater.",
        }), encoding="utf-8")

        manager.visual.show_video_animation({
            "id": "dragon_flight",
            "video_path": str(vid_file),
            "video_url": f"/theaters/{theater_id}/animations/dragon_flight/video.mp4",
            "scene_prompt": "A fire-breathing dragon soaring above a volcanic crater.",
        })

        # 4. Layered animation
        layered_dir = manager.theater.animations_dir() / "layered_lake"
        layered_dir.mkdir(parents=True)
        base_img = layered_dir / "base.png"
        layer1_img = layered_dir / "waves.png"
        base_img.write_bytes(b"base")
        layer1_img.write_bytes(b"waves")
        manager.visual.show_layered_animation({
            "id": "layered_lake",
            "scene_prompt": "Gentle moonlit waves on a calm alpine lake.",
            "base_image": str(base_img),
            "layers": [
                {"name": "base", "path": str(base_img), "effect": "none"},
                {"name": "waves", "path": str(layer1_img), "effect": "ripple"},
            ],
        })

        state = manager.get_latest_state()
        history = state["history"]

        # 4 total items in history: initial static image + 3 distinct animations
        self.assertEqual(len(history), 4)

        # Item 0: Static image
        self.assertEqual(history[0]["path"], str(img_file))
        self.assertIsNone(history[0].get("animation"))

        # Item 1: Tri-frame animation
        self.assertEqual(history[1]["animation"]["type"], "triframe")
        self.assertEqual(len(history[1]["animation"]["frames"]), 3)
        self.assertEqual(history[1]["prompt"], "A glowing mystical crystal in an ancient cavern.")
        self.assertNotIn("Image: Video", history[1]["prompt"])

        # Item 2: Video animation
        self.assertEqual(history[2]["animation"]["type"], "video")
        self.assertEqual(history[2]["animation"]["id"], "dragon_flight")
        self.assertEqual(history[2]["prompt"], "A fire-breathing dragon soaring above a volcanic crater.")
        self.assertNotEqual(history[2]["prompt"], "Image: Video")

        # Item 3: Layered animation
        self.assertEqual(history[3]["animation"]["type"], "layered")
        self.assertEqual(history[3]["prompt"], "Gentle moonlit waves on a calm alpine lake.")

        # Active prompt should reflect the latest animation's scene prompt
        self.assertEqual(state["prompt"], "Gentle moonlit waves on a calm alpine lake.")

        # Test persistence
        manager.save_local_theater_data(theater_dir=self.theaters_dir / theater_id)
        reloaded = self.make_canvas_state(theater_id)
        reloaded_history = reloaded.get_latest_state()["history"]
        self.assertEqual(len(reloaded_history), 4)
        self.assertEqual(reloaded_history[1]["animation"]["type"], "triframe")
        self.assertEqual(reloaded_history[2]["animation"]["type"], "video")
        self.assertEqual(reloaded_history[2]["prompt"], "A fire-breathing dragon soaring above a volcanic crater.")
        self.assertEqual(reloaded_history[3]["animation"]["type"], "layered")


class TestTriFrameAnimationUI(CanvasTestCase):
    def test_dummy_frames_are_exposed_and_wired_to_canvas_renderers(self) -> None:
        """Exercise the exact nested output paths the browser receives."""
        theater_id = "dummy_triframe"
        manager = self.make_canvas_state(theater_id)
        animation_dir = manager.theater.animations_dir() / "lantern_loop_123"
        animation_dir.mkdir(parents=True)
        colors = ("#b91c1c", "#15803d", "#1d4ed8")
        frame_paths = []
        for number, color in enumerate(colors, start=1):
            frame_path = animation_dir / f"frame_{number}.jpg"
            Image.new("RGB", (16, 16), color).save(frame_path, "JPEG")
            frame_paths.append(str(frame_path))

        manager.visual.show_triframe(frame_paths, url_for_path=manager.theater.get_url_for_path)
        state = manager.get_latest_state()

        self.assertEqual(state["animation"]["type"], "triframe")
        self.assertEqual(state["animation"]["crossfade_duration_ms"], 500)
        self.assertEqual(state["animation"]["frames"], [
            f"/theaters/{theater_id}/animations/lantern_loop_123/frame_{number}.jpg"
            for number in range(1, 4)
        ])
        self.assertTrue(all(Path(path).is_file() for path in frame_paths))


class TestVideoAnimationUI(CanvasTestCase):
    def test_video_animation_is_exposed_silent_and_looping_in_canvas_renderers(self) -> None:
        """Verify video animation state exposes loop/muted flags and prompt history."""
        theater_id = "dummy_video_theater"
        manager = self.make_canvas_state(theater_id)
        anim_dir = manager.theater.animations_dir() / "flight_123"
        anim_dir.mkdir(parents=True)
        fake_video_file = anim_dir / "video.mp4"
        fake_video_file.write_bytes(b"\x00\x00\x00 fake mp4 data")

        manifest = {
            "id": "flight_123",
            "type": "video",
            "scene_prompt": "A soaring griffin over snow-capped mountains.",
            "video_path": str(fake_video_file),
            "video_url": f"/theaters/{theater_id}/animations/flight_123/video.mp4",
            "loop": True,
            "muted": True,
        }
        manager.visual.show_video_animation(manifest, url_for_path=manager.theater.get_url_for_path)
        state = manager.get_latest_state()

        self.assertEqual(state["animation"]["type"], "video")
        self.assertEqual(state["animation"]["id"], "flight_123")
        self.assertEqual(state["animation"]["video_url"], f"/theaters/{theater_id}/animations/flight_123/video.mp4")
        self.assertEqual(state["animation"]["video_duration_seconds"], 5)
        self.assertTrue(state["animation"]["loop"])
        self.assertTrue(state["animation"]["muted"])

        # Prompt resolution must show the original scene prompt, not "Image: Video"
        self.assertEqual(state["prompt"], "A soaring griffin over snow-capped mountains.")
        self.assertNotEqual(state["prompt"], "Image: Video")
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["history"][0]["prompt"], "A soaring griffin over snow-capped mountains.")
        self.assertEqual(state["history"][0]["animation"]["type"], "video")
