"""Fixtures shared by UI tests."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from components.canvas_state import CanvasStateManager
from components.canvas_state_service import CanvasStateService


class UITestCase(unittest.TestCase):
    """Provide isolated filesystem and application state for UI tests."""

    def setUp(self):
        super().setUp()
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)
        self.sessions_dir = self.workspace / "sessions"
        self.sessions_dir.mkdir()
        self.addCleanup(self._temporary_directory.cleanup)

    def make_canvas_state(self, session_id: str) -> CanvasStateManager:
        return CanvasStateManager(session_id=session_id, base_sessions_dir=self.sessions_dir)

    def isolate_canvas_state_service(self) -> CanvasStateService:
        """Replace the web app's shared state service with a temporary one."""
        import web_viewer_app

        deployer = SimpleNamespace(
            base_dir=self.sessions_dir,
            list_sessions=lambda: [],
        )
        service = CanvasStateService(deployer)
        patcher = patch.object(web_viewer_app, "canvas_states", service)
        patcher.start()
        self.addCleanup(patcher.stop)
        return service
