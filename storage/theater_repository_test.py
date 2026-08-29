import json
from pathlib import Path
import tempfile
import unittest

from absl.testing import flagsaver

from storage.theater_repository import TheaterRepository, ensure_theaters_root, get_theaters_root


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

        # Metadata
        meta = self.repo.get_theater_metadata(theater_id)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "Test Theater")

        # Reconstruct into target
        target_dir = self.work_dir / "restored-theater"
        recon_ok = self.repo.reconstruct_theater(theater_id, target_dir)
        self.assertTrue(recon_ok)
        self.assertTrue((target_dir / "theater.json").exists())
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


if __name__ == "__main__":
    unittest.main()
