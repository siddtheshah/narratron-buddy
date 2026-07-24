"""Integration test for CanvasStateManager image history capping and paging payload."""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from testing.base_test import BaseTestCase
from components.canvas_state import CanvasStateManager


class TestImageHistoryPaging(BaseTestCase):

    def test_image_history_capping_to_100(self):
        """Test that canvas state retains at most 100 recent images."""
        manager = CanvasStateManager(session_id="test_history_cap_session")
        
        # Add 105 images
        for i in range(105):
            fake_path = f"/virtual/path/to/image_{i}.png"
            manager.update_shown_image(fake_path)

        state = manager.get_latest_state()
        history = state.get("history", [])

        self.assertEqual(len(manager.shown_images_history), 100)
        self.assertEqual(len(history), 100)
        
        # Oldest images (0-4) should be dropped; items 5 to 104 should remain
        self.assertIn("image_5.png", history[0]["path"])
        self.assertIn("image_104.png", history[-1]["path"])

    def test_image_history_payload_structure(self):
        """Test that history elements contain path, url, prompt, time, and transition keys."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            img1 = Path(tmp_dir) / "scene1.png"
            img2 = Path(tmp_dir) / "scene2.jpg"
            img1.write_bytes(b"scene1_data")
            img2.write_bytes(b"scene2_data")

            manager = CanvasStateManager(session_id="test_history_structure_session")
            manager.update_shown_image(str(img1), transition="fade")
            manager.update_shown_image(str(img2), transition="crossfade")

            state = manager.get_latest_state()
            history = state["history"]

            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["path"], str(img1))
            self.assertEqual(history[0]["transition"], "fade")
            self.assertEqual(history[1]["path"], str(img2))
            self.assertEqual(history[1]["transition"], "crossfade")
            self.assertIn("url", history[0])
            self.assertIn("prompt", history[0])

    def test_history_persistence_and_reload(self):
        """Test exporting session data and reloading canvas state from disk preserves history."""
        session_id = "test_persist_history"
        sess_dir = (PROJECT_ROOT / "sessions" / session_id).resolve()
        try:
            manager = CanvasStateManager(session_id=session_id)
            manager.update_shown_image("/path/a.png", transition="fade")
            manager.update_shown_image("/path/b.png", transition="crossfade")

            # Export state to session.json
            manager.export_session_data(session_dir=sess_dir)

            # Reload manager from session.json
            reloaded_manager = CanvasStateManager(session_id=session_id)

            self.assertEqual(len(reloaded_manager.shown_images_history), 2)
        finally:
            if sess_dir.exists():
                shutil.rmtree(sess_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
