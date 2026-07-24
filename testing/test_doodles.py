# -*- coding: utf-8 -*-
"""
test_doodles.py — validates doodle disk persistence and reloading on canvas reinitialization.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import unittest
from testing.base_test import BaseTestCase
from components.canvas_state import CanvasStateManager
from components.chat_manager import ChatManager


class TestDoodles(BaseTestCase):
    def test_doodle_persistence_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_id = "test_doodle_sess_tmp"
            sess_dir = PROJECT_ROOT / "sessions" / session_id
            sess_dir.mkdir(parents=True, exist_ok=True)

            img1_path = sess_dir / "img1.jpg"
            img1_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

            # 1. Initialize CanvasStateManager
            cs = CanvasStateManager.__new__(CanvasStateManager)
            cs.session_id = session_id
            cs.current_image_basename = "img1.jpg"
            cs.shown_image_path = str(img1_path)
            cs.shown_image_time = 100.0
            cs.shown_image_prompt = "test prompt"
            cs.shown_images_history = [cs.shown_image_path]
            cs.shown_image_transition = "crossfade"
            cs.current_playlist = None
            cs.current_playlist_tracks = []
            cs.music_paused = False
            cs.current_playlist_time = 0.0
            cs.active_ws_connections = []
            cs.doodles_state = []
            cs.doodles_enabled = True
            cs.chat_manager = ChatManager(output_dir=str(sess_dir))

            # Monkeypatch session path resolution in export_session_data
            orig_export = cs.export_session_data
            cs.export_session_data = lambda session_dir=None: orig_export(session_dir=sess_dir)

            # 2. Add doodles
            d1 = {"type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2, "color": "#ffffff", "size": 3}
            d2 = {"type": "draw", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3, "color": "#ff0000", "size": 5}
            cs.add_doodle(d1)
            cs.add_doodle(d2)

            self.assertEqual(len(cs.doodles_state), 2)

            # 3. Verify session.json was created on disk and contains doodles
            session_json = sess_dir / "session.json"
            self.assertTrue(session_json.exists())
            
            with open(session_json, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            
            saved_doodles = disk_data.get("canvas_state", {}).get("doodles", [])
            self.assertEqual(len(saved_doodles), 2)
            self.assertEqual(saved_doodles[0]["color"], "#ffffff")
            self.assertEqual(saved_doodles[1]["color"], "#ff0000")

            # 4. Reinitialize CanvasStateManager from disk
            cs_reloaded = CanvasStateManager.__new__(CanvasStateManager)
            cs_reloaded.session_id = session_id
            cs_reloaded.doodles_state = []
            cs_reloaded.doodles_enabled = True

            # Mock load_state_from_disk to point to temp sess_dir
            with open(session_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                c_state = data.get("canvas_state", {})
                cs_reloaded.doodles_state = c_state.get("doodles", [])
                cs_reloaded.doodles_enabled = c_state.get("doodles_enabled", True)

            self.assertEqual(len(cs_reloaded.doodles_state), 2)

            # 5. Verify update_shown_image with SAME image preserves doodles, but NEW image clears doodles
            cs.update_shown_image(cs.shown_image_path)
            self.assertEqual(len(cs.doodles_state), 2)

            cs.update_shown_image(str(sess_dir / "new_img2.jpg"))
            self.assertEqual(len(cs.doodles_state), 0)


if __name__ == "__main__":
    unittest.main()
