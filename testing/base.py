import unittest


class BaseTestCase(unittest.TestCase):
    """Base fixture for tests that use isolated, test-owned theater workspaces."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(self.cleanup_theater_directories)
        from absl import flags
        from utils.email_service import FLAGS
        if "send_emails" not in flags.FLAGS:
            flags.DEFINE_boolean("send_emails", False, "Whether to actually send emails via SMTP.")
        else:
            FLAGS.send_emails = False

        import object_registry
        object_registry.FLAGS.allow_mock_payments = False

    def cleanup_theater_directories(self) -> None:
        # 1. Clear the shared canvas-state cache if loaded
        try:
            import object_registry
            object_registry.canvas_states.states.clear()
        except Exception:
            pass
