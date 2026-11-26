import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from absl.testing import flagsaver

from storage.local_theater_repository import LocalTheaterRepository
from storage.theater_repository import get_theaters_root


def test_local_catalog_export_keeps_filesystem_backend_when_flags_change(tmp_path: Path) -> None:
    changed = []
    repository = LocalTheaterRepository(tmp_path / "repository", storage_changed=changed.append)
    source = tmp_path / "checkout"
    source.mkdir()
    (source / "theater.json").write_text('{"theater_id":"quest"}', encoding="utf-8")
    with flagsaver.flagsaver(testing_use_local=False):
        assert repository.export_catalog_theater("quest", source, {})
    assert repository.get_theater_metadata("quest") == {"theater_id": "quest"}
    assert changed == ["quest"]


def test_export_and_restore_without_filesystem_metadata_support(tmp_path: Path) -> None:
    repository = LocalTheaterRepository(tmp_path / "repository")
    source = tmp_path / "checkout"
    files = {
        "theater.json": b'{"theater_id":"quest","name":"Quest"}',
        "theater.yaml": b"live_agent: {}\n",
        "references/characters/Hero/1.webp": b"hero-image",
        "lore/story.md": b"Story lore",
        "output/artifacts/images/scene.webp": b"generated-image",
    }
    for relative, content in files.items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (source / "playlists" / "empty").mkdir(parents=True)
    (source / "editor.json").write_text("private editor state", encoding="utf-8")
    (source / "editor.tmp").write_text("pending editor state", encoding="utf-8")
    saved = repository.theater_path("quest")
    (saved / "lore").mkdir(parents=True)
    (saved / "lore" / "obsolete.md").write_text("old lore", encoding="utf-8")
    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "keep.txt").write_text("existing checkout file", encoding="utf-8")

    # Bucket mounts support file contents, but can reject chmod/utime. This
    # also catches copytree's directory metadata writes, even with copyfile.
    with (
        patch("os.chmod", side_effect=PermissionError("Operation not permitted")),
        patch("os.utime", side_effect=PermissionError("Operation not permitted")),
    ):
        assert repository.export_theater("quest", source)
        assert repository.reconstruct_theater("quest", restored)

    for root in (saved, restored):
        for relative, content in files.items():
            assert (root / relative).read_bytes() == content
        assert (root / "playlists" / "empty").is_dir()
        assert not (root / "editor.json").exists()
        assert not (root / "editor.tmp").exists()
        assert not (root / "lore" / "obsolete.md").exists()
    assert (restored / "keep.txt").read_text(encoding="utf-8") == "existing checkout file"


def test_failed_content_copy_reports_failure_without_pruning_saved_assets(tmp_path: Path) -> None:
    repository = LocalTheaterRepository(tmp_path / "repository")
    source = tmp_path / "checkout"
    source.mkdir()
    (source / "theater.json").write_text('{"theater_id":"quest","name":"Quest"}', encoding="utf-8")
    saved = repository.theater_path("quest")
    (saved / "references").mkdir(parents=True)
    image = saved / "references" / "hero.webp"
    image.write_bytes(b"saved-image")

    with patch("shutil.copyfile", side_effect=OSError("Bucket write failed")):
        assert not repository.export_theater("quest", source)
        assert not repository.reconstruct_theater("quest", tmp_path / "restored")

    assert image.read_bytes() == b"saved-image"


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
        self.repo = LocalTheaterRepository(base_dir=self.repo_dir)

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
