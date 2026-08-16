import os
import shutil
import tempfile
import unittest
import asyncio
from pathlib import Path

from PIL import Image

from components.theater_manager import TheaterManager
from testing.base import BaseTestCase
from components.canvas_state import CanvasStateManager, MAX_AGENT_THOUGHT_LENGTH
from components.canvas_state_service import CanvasStateService


class RecordingWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)

class TestCanvasStateManager(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.theater_manager = TheaterManager()

    def test_canvas_state_manager(self):
        manager = CanvasStateManager(theater_id="test_theater", theater_manager=self.theater_manager)
        manager.update_current_music("test_playlist", ["/playlists/test/1.mp3"])
        self.assertEqual(manager.current_music_id, "test_playlist")
        self.assertFalse(manager.music_paused)

        manager.pause_current_music()
        self.assertTrue(manager.music_paused)

        manager.current_image_basename = "test.jpg"
        manager.add_chat_message("Hello from test", author="user")
        msgs = manager.chat_manager.get_messages()
        self.assertEqual(msgs[-1]["text"], "Hello from test")

        manager.set_agent_thought("I am choosing the next scene.")
        self.assertEqual(manager.get_agent_thought()["text"], "I am choosing the next scene.")
        self.assertGreater(manager.get_latest_state()["agent_thought"]["time"], 0)

        manager.set_scene_dialogue([{"speaker": "Mara", "text": "The door is open.", "kind": "speech"}])
        self.assertEqual(manager.get_latest_state()["scene_dialogue"][0]["speaker"], "Mara")

        manager.set_agent_thought("x" * (MAX_AGENT_THOUGHT_LENGTH + 50))
        self.assertEqual(len(manager.get_agent_thought()["text"]), MAX_AGENT_THOUGHT_LENGTH)
        self.assertTrue(manager.get_agent_thought()["text"].endswith("…"))

    def test_state_websocket_receives_compact_invalidations(self):
        async def exercise():
            manager = CanvasStateManager(theater_id="state_socket", theater_manager=self.theater_manager)
            socket = RecordingWebSocket()
            manager.register_state_websocket(socket)
            manager.update_current_music("test_playlist", ["/playlists/test/1.mp3"])
            await asyncio.sleep(0)
            self.assertEqual(manager.state_revision, 1)
            self.assertEqual(socket.messages, [{
                "type": "state_changed", "revision": 1, "domains": ["latest"],
            }])
            manager.unregister_state_websocket(socket)

        asyncio.run(exercise())

    def test_character_voice_assignments_persist_with_canvas_state(self):
        theater_id = "voice_state"
        manager = CanvasStateManager(theater_id=theater_id, theater_manager=self.theater_manager)
        manager.character_voice_assignments = {"mara": "dacey_en"}
        manager.export_theater_data(theater_dir=manager.theater.directory())

        restored = CanvasStateManager(theater_id=theater_id, theater_manager=self.theater_manager)
        self.assertEqual(restored.character_voice_assignments, {"mara": "dacey_en"})

    def test_update_shown_image_empty_folder(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                tmp_file.write(b"fake image data")
                tmp_path = tmp_file.name

            try:
                manager = CanvasStateManager(theater_id="test_theater", theater_manager=self.theater_manager)
                manager.update_shown_image(tmp_path)

                state = manager.get_latest_state()
                self.assertIsNotNone(state["latest"])
                self.assertIn(os.path.basename(tmp_path), state["latest"])
                self.assertGreater(state["time"], 0)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def test_doodles_enabled_state_and_persistence(self):
        manager = CanvasStateManager(theater_id="test_doodles_theater", theater_manager=self.theater_manager)
        self.assertTrue(manager.doodles_enabled)
        self.assertFalse(manager.viewer_collab_enabled)

        latest_state = manager.get_latest_state()
        self.assertTrue(latest_state.get("doodles_enabled"))

        manager.set_doodles_enabled(False)
        self.assertFalse(manager.doodles_enabled)

        latest_state = manager.get_latest_state()
        self.assertFalse(latest_state.get("doodles_enabled"))

        exported_state, _ = manager.export_theater_data()
        self.assertFalse(exported_state["canvas_state"]["doodles_enabled"])
        self.assertFalse(exported_state["canvas_state"]["viewer_collab_enabled"])

    def test_doodle_snapshot_composites_normalized_stroke(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            image_path = tmp_file.name
        try:
            Image.new("RGB", (100, 100), "black").save(image_path)
            manager = CanvasStateManager(theater_id="snapshot_theater", theater_manager=self.theater_manager)
            manager.shown_image_path = image_path
            manager.doodles_state = [{
                "type": "draw", "x0": 0, "y0": 0, "x1": 1, "y1": 1,
                "color": "#ff0000", "size": 8,
            }]
            snapshot = manager.get_doodle_snapshot_png()
            self.assertIsNotNone(snapshot)
            with Image.open(__import__("io").BytesIO(snapshot)) as rendered:
                self.assertGreater(rendered.getpixel((50, 50))[0], 150)
        finally:
                if os.path.exists(image_path):
                    os.remove(image_path)

    def test_doodle_batch_is_persisted_as_segments_and_broadcast_once(self):
        service = CanvasStateService(self.theater_manager)
        state = service.get("batched_doodles")
        sender = RecordingWebSocket()
        recipient = RecordingWebSocket()
        state.register_websocket(sender)
        state.register_websocket(recipient)

        asyncio.run(service.apply_doodle_message(state, {
            "type": "draw_batch", "color": "#ffffff", "size": 3,
            "points": [0.1, 0.1, 0.2, 0.2, 0.3, 0.3],
        }, sender))

        self.assertEqual(len(state.doodles_state), 2)
        self.assertEqual(len(recipient.messages), 1)
        self.assertEqual(recipient.messages[0]["type"], "draw_batch")
        self.assertEqual(recipient.messages[0]["points"], [0.1, 0.1, 0.2, 0.2, 0.3, 0.3])
        self.assertEqual(sender.messages, [])

    def test_doodle_batch_acknowledges_and_deduplicates_retries(self):
        service = CanvasStateService(self.theater_manager)
        state = service.get("acknowledged_doodles")
        sender = RecordingWebSocket()
        message = {
            "type": "draw_batch", "color": "#ffffff", "size": 3,
            "points": [0.1, 0.1, 0.2, 0.2], "client_message_id": "doodle-1",
        }

        asyncio.run(service.apply_doodle_message(state, message, sender))
        asyncio.run(service.apply_doodle_message(state, message, sender))

        self.assertEqual(len(state.doodles_state), 1)
        self.assertEqual(sender.messages, [
            {"type": "doodle_ack", "client_message_id": "doodle-1"},
            {"type": "doodle_ack", "client_message_id": "doodle-1"},
        ])

    def test_doodle_snapshot_groups_connected_segments_by_style(self):
        manager = CanvasStateManager(theater_id="snapshot_batches", theater_manager=self.theater_manager)
        manager.doodles_state = [
            {"type": "draw", "x0": 0, "y0": 0, "x1": 0.1, "y1": 0.1, "color": "#fff", "size": 3},
            {"type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2, "color": "#fff", "size": 3},
            {"type": "draw", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3, "color": "#f00", "size": 3},
        ]
        self.assertEqual(manager.get_doodle_snapshot_batches(), [
            {"color": "#fff", "size": 3, "points": [0, 0, 0.1, 0.1, 0.2, 0.2]},
            {"color": "#f00", "size": 3, "points": [0.2, 0.2, 0.3, 0.3]},
        ])

    def test_doodle_websocket_receives_compact_snapshot(self):
        service = CanvasStateService(self.theater_manager)
        state = service.get("compact_snapshot")
        state.doodles_state = [{
            "type": "draw", "x0": 0, "y0": 0, "x1": 1, "y1": 1,
            "color": "#fff", "size": 3,
        }]
        websocket = RecordingWebSocket()

        asyncio.run(service.connect_doodle_websocket(websocket, "compact_snapshot"))

        self.assertEqual(websocket.messages[1], {
            "type": "doodle_snapshot",
            "batches": [{"color": "#fff", "size": 3, "points": [0, 0, 1, 1]}],
        })

    def test_legacy_draw_message_still_reaches_other_clients(self):
        service = CanvasStateService(self.theater_manager)
        state = service.get("legacy_doodle")
        sender = RecordingWebSocket()
        recipient = RecordingWebSocket()
        state.register_websocket(sender)
        state.register_websocket(recipient)
        action = {
            "type": "draw", "x0": 0, "y0": 0, "x1": 1, "y1": 1,
            "color": "#fff", "size": 3,
        }

        asyncio.run(service.apply_doodle_message(state, action, sender))

        self.assertEqual(state.doodles_state, [action])
        self.assertEqual(recipient.messages, [action])

    def test_shown_images_history_capping(self):
        manager = CanvasStateManager(theater_id="test_history_theater", theater_manager=self.theater_manager)
        # Add 120 images
        for i in range(120):
            fake_path = f"/path/to/image_{i}.png"
            manager.update_shown_image(fake_path)

        self.assertEqual(len(manager.shown_images_history), 100)
        # Verify the oldest entries (0-19) rolled off, and items 20-119 remain
        last_entry = manager.shown_images_history[-1]
        first_entry = manager.shown_images_history[0]
        self.assertIn("image_119.png", last_entry["path"])
        self.assertIn("image_20.png", first_entry["path"])

    def test_get_latest_state_returns_history(self):
        manager = CanvasStateManager(theater_id="test_history_payload", theater_manager=self.theater_manager)
        manager.update_shown_image("/path/to/scene1.png")
        manager.update_shown_image("/path/to/scene2.png")

        state = manager.get_latest_state()
        self.assertIn("history", state)
        self.assertEqual(len(state["history"]), 2)
        self.assertEqual(state["history"][0]["path"], "/path/to/scene1.png")
        self.assertEqual(state["history"][1]["path"], "/path/to/scene2.png")

    def test_triframe_is_exposed_as_a_looping_animation(self):
        manager = CanvasStateManager(theater_id="triframe_payload", theater_manager=self.theater_manager)
        frames = [f"/path/to/frame_{number}.jpg" for number in range(1, 4)]
        manager.show_triframe(frames)

        state = manager.get_latest_state()
        self.assertEqual(state["latest"], "/theaters/triframe_payload/output/frame_1.jpg")
        self.assertEqual(state["animation"]["type"], "triframe")
        self.assertEqual(len(state["animation"]["frames"]), 3)
        self.assertEqual(state["effect"], "none")

    def test_newer_image_prevents_a_stale_triframe_from_being_shown(self):
        service = CanvasStateService(self.theater_manager)
        state = service.get("triframe_yield")
        expected_revision = state.image_revision
        service.show_image("/path/to/newer-scene.jpg", theater_id="triframe_yield")

        displayed = service.show_triframe_if_current(
            ["/path/to/frame_1.jpg", "/path/to/frame_2.jpg", "/path/to/frame_3.jpg"],
            expected_revision,
            theater_id="triframe_yield",
        )

        self.assertFalse(displayed)
        self.assertEqual(state.shown_image_path, "/path/to/newer-scene.jpg")

    def test_tool_activity_is_transient_and_exposed_in_latest_state(self):
        manager = CanvasStateManager(theater_id="tool_activity", theater_manager=self.theater_manager)
        manager.set_tool_activity("image", True)
        manager.set_tool_activity("live", True)
        manager.set_tool_activity("dice", True)

        self.assertTrue(manager.get_latest_state()["tool_activity"]["image_generating"])
        self.assertTrue(manager.get_latest_state()["tool_activity"]["live_ready"])
        self.assertTrue(manager.get_latest_state()["tool_activity"]["dice_rolling"])

        manager.set_tool_activity("image", False)
        manager.set_tool_activity("live", False)
        manager.set_tool_activity("dice", False)
        self.assertFalse(manager.get_latest_state()["tool_activity"]["image_generating"])
        self.assertFalse(manager.get_latest_state()["tool_activity"]["live_ready"])
        self.assertFalse(manager.get_latest_state()["tool_activity"]["dice_rolling"])

    def test_scene_description_is_exposed_and_compact(self):
        manager = CanvasStateManager(theater_id="scene_description", theater_manager=self.theater_manager)
        description = " ".join(f"word{index}" for index in range(50))

        manager.set_scene_description(description)

        self.assertEqual(len(manager.scene_description.split()), 45)
        self.assertEqual(manager.get_latest_state()["scene_description"], manager.scene_description)

if __name__ == "__main__":
    unittest.main()


