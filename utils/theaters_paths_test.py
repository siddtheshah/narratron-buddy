"""Tests for runtime theater-root selection."""

from pathlib import Path
import unittest

from absl.testing import flagsaver

from utils import theaters_paths


class TheaterPathsTest(unittest.TestCase):
    def test_local_root_defaults_to_workspace_theaters_directory(self):
        self.assertEqual(
            theaters_paths.get_theaters_root().resolve(),
            (Path(__file__).parent.parent / "theaters").resolve(),
        )

    @flagsaver.flagsaver(use_cloud_theater_storage=True)
    def test_cloud_root_uses_tmp_theaters(self):
        self.assertEqual(theaters_paths.get_theaters_root(), Path("/tmp/theaters"))


if __name__ == "__main__":
    unittest.main()
