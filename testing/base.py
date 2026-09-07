import unittest


class BaseTestCase(unittest.TestCase):
    """Base fixture for tests that use isolated, test-owned theater workspaces."""

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
