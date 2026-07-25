"""Tests for runtime session-root selection."""

from pathlib import Path
import unittest

from absl.testing import flagsaver

from utils import session_paths


class SessionPathsTest(unittest.TestCase):
    def test_local_root_defaults_to_workspace_sessions_directory(self):
        self.assertEqual(
            session_paths.get_sessions_root().resolve(),
            (Path(__file__).parent.parent / "sessions").resolve(),
        )

    @flagsaver.flagsaver(use_cloud_session_storage=True)
    def test_cloud_root_uses_tmp_sessions(self):
        self.assertEqual(session_paths.get_sessions_root(), Path("/tmp/sessions"))


if __name__ == "__main__":
    unittest.main()
