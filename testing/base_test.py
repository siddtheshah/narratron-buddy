import os
import shutil
import unittest
from pathlib import Path


class BaseTestCase(unittest.TestCase):
    """Base test fixture that automatically cleans up leftover session directories and cached states."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_session_directories)
        from utils.email_service import FLAGS
        FLAGS.send_emails = False

    def cleanup_session_directories(self):
        # 1. Clear web_viewer_app canvas states cache if loaded
        try:
            from web_viewer_app import _canvas_states
            _canvas_states.clear()
        except Exception:
            pass

        # 2. Automatically clean up all subdirectories inside sessions/
        project_root = Path(__file__).parent.parent.resolve()
        sessions_dir = project_root / "sessions"
        if sessions_dir.exists():
            for item in sessions_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
