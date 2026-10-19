import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from absl.testing import flagsaver

from storage.theater_repository import TheaterRepository, get_theaters_root


class TestTheaterRootSelection(unittest.TestCase):
    def test_cloud_root_defaults_to_mnt_storage_theaters(self):
        self.assertEqual(get_theaters_root(), Path("/mnt/storage/theaters"))

    @flagsaver.flagsaver(testing_use_local=True)
    def test_local_root_uses_workspace_theaters_directory(self):
        self.assertEqual(
            get_theaters_root().resolve(),
            (Path(__file__).parent.parent / "theaters").resolve(),
        )


class TheaterRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.work_dir = Path(self.temp_dir.name) / "work"
        self.repo_dir.mkdir()
        self.work_dir.mkdir()
        self.repo = TheaterRepository(base_dir=self.repo_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_export_and_reconstruct(self):
        theater_id = "test-theater-1"
        source_theater = self.work_dir / theater_id
        source_theater.mkdir()
        (source_theater / "output").mkdir()
        (source_theater / "references").mkdir()
        (source_theater / "theater.json").write_text(json.dumps({"theater_id": theater_id, "name": "Test Theater"}), encoding="utf-8")
        (source_theater / "output" / "img.png").write_bytes(b"image-bytes-123")
        (source_theater / "references" / "ref.jpg").write_bytes(b"ref-bytes-456")

        # Export
        ok = self.repo.export_theater(theater_id, source_theater)
        self.assertTrue(ok)
        self.assertTrue(self.repo.theater_exists(theater_id))
        self.assertTrue((self.repo.theater_path(theater_id) / "references" / "characters").is_dir())
        self.assertTrue((self.repo.theater_path(theater_id) / "references" / "scenes").is_dir())

        # Metadata
        meta = self.repo.get_theater_metadata(theater_id)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "Test Theater")

        # Reconstruct into target
        target_dir = self.work_dir / "restored-theater"
        recon_ok = self.repo.reconstruct_theater(theater_id, target_dir)
        self.assertTrue(recon_ok)
        self.assertTrue((target_dir / "theater.json").exists())
        self.assertTrue((target_dir / "references" / "characters").is_dir())
        self.assertTrue((target_dir / "references" / "scenes").is_dir())
        self.assertEqual((target_dir / "output" / "img.png").read_bytes(), b"image-bytes-123")
        self.assertEqual((target_dir / "references" / "ref.jpg").read_bytes(), b"ref-bytes-456")

        # List
        theaters = self.repo.list_theaters()
        self.assertEqual(len(theaters), 1)
        self.assertEqual(theaters[0]["theater_id"], theater_id)

        # Delete
        del_ok = self.repo.delete_theater(theater_id)
        self.assertTrue(del_ok)
        self.assertFalse(self.repo.theater_exists(theater_id))

    def test_theater_path_rejects_unsafe_identifiers_and_escapes(self) -> None:
        for unsafe_id in ["", "  ", ".", "..", "/outside", "C:outside", "nested/name", "nested\\name", "CON", "%2e%2e"]:
            with self.assertRaises(ValueError):
                self.repo.theater_path(unsafe_id)

    def test_delete_theater_rejects_root_parent_and_symlink_escapes(self) -> None:
        with patch("shutil.rmtree") as mock_rmtree:
            with self.assertRaises(ValueError):
                self.repo.delete_theater("..")
            with self.assertRaises(ValueError):
                self.repo.delete_theater(".")
            with self.assertRaises(ValueError):
                self.repo.delete_theater("%2e%2e")
            with self.assertRaises(ValueError):
                self.repo.delete_theater("C:outside")
            mock_rmtree.assert_not_called()

        # Symlink escape simulation
        outside = self.repo_dir.parent / "escaped_repo"
        target_requested = (self.repo_dir / "linked_theater").resolve()
        original_resolve = Path.resolve

        def mock_resolve(path: Path, strict: bool = False) -> Path:
            return outside if path == target_requested else original_resolve(path, strict=strict)

        with patch.object(Path, "resolve", mock_resolve), patch("shutil.rmtree") as mock_rmtree:
            with self.assertRaises(ValueError):
                self.repo.delete_theater("linked_theater")
            mock_rmtree.assert_not_called()


if __name__ == "__main__":
    unittest.main()
