import shutil
import unittest
from pathlib import Path


class BaseTestCase(unittest.TestCase):
    """Base test fixture that automatically cleans up leftover theater directories and cached states."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_theater_directories)
        from utils.email_service import FLAGS
        FLAGS.send_emails = False
        from api_server.app import FLAGS as WEB_FLAGS
        WEB_FLAGS.allow_mock_payments = False

    def cleanup_theater_directories(self):
        # 1. Clear the shared canvas-state cache if loaded
        try:
            from api_server.app import canvas_states
            canvas_states.states.clear()
        except Exception:
            pass

        # 2. Automatically clean up all subdirectories inside theaters/
        project_root = Path(__file__).parent.parent.resolve()
        theaters_dir = project_root / "theaters"
        if theaters_dir.exists():
            for item in theaters_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
