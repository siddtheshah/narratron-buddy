"""Fixtures shared by UI tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from components.canvas_state import CanvasStateManager
from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager


class UITestCase(unittest.TestCase):
    """Provide isolated filesystem and application state for UI tests."""

    def setUp(self):
        super().setUp()
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)
        self.theaters_dir = self.workspace / "theaters"
        self.theaters_dir.mkdir()
        self.addCleanup(self._temporary_directory.cleanup)

    def make_canvas_state(self, theater_id: str) -> CanvasStateManager:
        return CanvasStateManager(
            theater_id=theater_id,
            theater_manager=TheaterManager(base_theaters_dir=self.theaters_dir),
        )

    def isolate_canvas_state_service(self) -> CanvasStateService:
        """Replace the registry's shared state service with a temporary one."""
        import object_registry

        service = CanvasStateService(TheaterManager(base_theaters_dir=self.theaters_dir))
        patcher = patch.object(object_registry, "canvas_states", service)
        patcher.start()
        self.addCleanup(patcher.stop)
        return service
