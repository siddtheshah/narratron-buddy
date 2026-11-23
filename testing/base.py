import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from components.canvas_state import CanvasStateManager
from components.canvas.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager


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


class CanvasTestCase(unittest.TestCase):
    """Provide isolated filesystem and canvas state."""

    def setUp(self) -> None:
        super().setUp()
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)
        self.theaters_dir = self.workspace / "theaters"
        self.theaters_dir.mkdir()
        self.addCleanup(self._temporary_directory.cleanup)

    def make_canvas_state(self, theater_id: str) -> CanvasStateManager:
        mgr = TheaterManager(base_theaters_dir=self.theaters_dir)
        return CanvasStateManager(mgr.theater(theater_id))

    def isolate_canvas_state_service(self) -> CanvasStateService:
        """Replace the registry's shared state service with a temporary one."""
        import object_registry

        service = CanvasStateService(TheaterManager(base_theaters_dir=self.theaters_dir))
        patcher = patch.object(object_registry, "canvas_states", service)
        patcher.start()
        self.addCleanup(patcher.stop)
        return service
