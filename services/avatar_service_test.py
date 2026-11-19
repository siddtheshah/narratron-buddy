"""Unit tests for services/avatar_service.py."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from absl.testing import flagsaver
from google.cloud import storage
from pydantic import JsonValue
import pytest

import services.avatar_service as avatars
from services.avatar_service import (
    AvatarService,
    FLAGS,
    get_avatars_root,
)


@pytest.mark.parametrize("prefix", ["avatars", "/custom/avatars/", ""])
def test_cloud_avatar_download_uses_python_transfer_manager(tmp_path: Path, prefix: str) -> None:
    mount = tmp_path / "mount"
    folder = mount / "Light Cube"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text('{"id": "light-cube"}', encoding="utf-8")
    assets = {
        "avatar.json": b'{"id": "light-cube", "title": "Cube of Light"}',
        "model.glb": b"glb-binary",
        "preview.webp": b"webp-binary",
        "shaders/dust.vert": b"void main() {}",
        "shaders/dust.frag": b"void main() {}",
        "metadata.json": b'{"id": "light-cube"}',
        "README.md": b"# Avatar readme",
    }
    bucket = MagicMock(spec=storage.Bucket)
    bucket.name = "custom-bucket"
    root = prefix.strip("/")
    object_prefix = f"{root}/Light Cube/" if root else "Light Cube/"
    bucket.list_blobs.return_value = [SimpleNamespace(name=object_prefix + name) for name in assets]
    bucket.list_blobs.return_value.append(SimpleNamespace(name=object_prefix + "shaders/"))
    temporary_paths: list[Path] = []

    def download(
        source: storage.Bucket,
        names: list[str],
        *,
        destination_directory: str,
        blob_name_prefix: str,
        worker_type: str,
        max_workers: int,
        raise_exception: bool,
    ) -> list[None]:
        assert source is bucket
        assert blob_name_prefix == object_prefix
        assert worker_type == avatars.transfer_manager.THREAD
        assert max_workers == 8
        assert raise_exception
        destination = Path(destination_directory)
        temporary_paths.append(destination)
        for name in names:
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(assets[name])
        return [None for _ in names]

    service = AvatarService(mount)
    with (
        flagsaver.flagsaver(testing_use_local=False),
        patch.object(avatars, "get_avatars_root", return_value=mount),
        patch.dict("os.environ", {"GCS_BUCKET": "custom-bucket", "GCS_AVATARS_PREFIX": prefix}),
        patch.object(avatars.storage, "Client", autospec=True) as client,
        patch.object(avatars.transfer_manager, "download_many_to_path", side_effect=download) as transfer,
    ):
        client.return_value.bucket.return_value = bucket
        loaded = service.load_avatar_assets("light-cube")
        client.return_value.bucket.assert_called_once_with("custom-bucket")
        bucket.list_blobs.assert_called_once_with(prefix=object_prefix)
        transfer.assert_called_once()

    assert loaded == {
        "avatars/light-cube/avatar.json": b'{"id": "light-cube", "title": "Cube of Light"}',
        "avatars/light-cube/model.glb": b"glb-binary",
        "avatars/light-cube/preview.webp": b"webp-binary",
        "avatars/light-cube/shaders/dust.vert": b"void main() {}",
        "avatars/light-cube/shaders/dust.frag": b"void main() {}",
    }
    assert not temporary_paths[0].exists()


@pytest.mark.parametrize("local_mode", [True, False])
def test_explicit_local_avatar_directory_does_not_contact_gcs(tmp_path: Path, local_mode: bool) -> None:
    folder = tmp_path / "light-cube"
    folder.mkdir(parents=True)
    (folder / "avatar.json").write_bytes(b'{"id": "light-cube", "title": "Cube of Light"}')
    with (
        flagsaver.flagsaver(testing_use_local=local_mode),
        patch.object(avatars.storage, "Client", autospec=True) as client,
    ):
        assert AvatarService(tmp_path).load_avatar_assets("light-cube") == {
            "avatars/light-cube/avatar.json": b'{"id": "light-cube", "title": "Cube of Light"}',
        }
        client.assert_not_called()


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute.txt", "shaders/../../outside.txt", "C:/outside.txt"])
def test_cloud_avatar_rejects_unsafe_names_before_downloading(tmp_path: Path, name: str) -> None:
    bucket = MagicMock(spec=storage.Bucket)
    bucket.list_blobs.return_value = [SimpleNamespace(name="avatars/stage/" + name)]
    with (
        patch.dict("os.environ", {"GCS_AVATARS_PREFIX": "avatars"}),
        patch.object(avatars.storage, "Client", autospec=True) as client,
        patch.object(avatars.transfer_manager, "download_many_to_path") as transfer,
    ):
        client.return_value.bucket.return_value = bucket
        with pytest.raises(ValueError):
            AvatarService._download_avatar_folder("stage", tmp_path)
        transfer.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_cloud_avatar_download_failure_cleans_up_temporary_package(tmp_path: Path) -> None:
    folder = tmp_path / "stage"
    folder.mkdir()
    service = AvatarService(tmp_path)
    temporary_paths: list[Path] = []

    def fail_download(folder_name: str, destination: Path) -> None:
        assert folder_name == "stage"
        temporary_paths.append(destination)
        (destination / "partial.txt").write_bytes(b"partial")
        raise OSError("Download failed")

    with (
        flagsaver.flagsaver(testing_use_local=False),
        patch.object(avatars, "get_avatars_root", return_value=tmp_path),
        patch.object(service, "_download_avatar_folder", side_effect=fail_download),
        pytest.raises(OSError, match="Download failed"),
    ):
        service.load_avatar_assets("stage")
    assert not temporary_paths[0].exists()


def test_cloud_avatar_empty_prefix_is_reported(tmp_path: Path) -> None:
    with (
        patch.object(avatars.storage, "Client", autospec=True) as client,
        patch.object(avatars.transfer_manager, "download_many_to_path") as transfer,
    ):
        client.return_value.bucket.return_value.list_blobs.return_value = []
        with pytest.raises(FileNotFoundError, match="No avatar files"):
            AvatarService._download_avatar_folder("stage", tmp_path)
        transfer.assert_not_called()


class TestAvatarService(unittest.TestCase):
    """Tests for AvatarService listing, sorting, cover loading, and asset extraction."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.local_dir = Path(self.temp_dir.name)

        self.avatar1_dir = self.local_dir / "light-cube"
        (self.avatar1_dir / "shaders").mkdir(parents=True)
        (self.avatar1_dir / "metadata.json").write_text(
            json.dumps({
                "id": "light-cube",
                "title": "Cube of Light",
                "description": "Radiant floating cube avatar.",
                "created_at": "2026-08-10T10:00:00Z",
                "cover_image": "preview.webp",
                "tags": ["Avatar", "Minimal"],
            }),
            encoding="utf-8",
        )
        (self.avatar1_dir / "avatar.json").write_text(
            json.dumps({
                "id": "light-cube",
                "title": "Cube of Light",
                "model": "model.glb",
                "poster": "preview.webp",
                "actions": [{"id": "idle", "label": "Gold"}, {"id": "ember", "label": "Ember"}],
            }),
            encoding="utf-8",
        )
        (self.avatar1_dir / "model.glb").write_bytes(b"glb-cube")
        (self.avatar1_dir / "preview.webp").write_bytes(b"RIFFwebp-cube")
        (self.avatar1_dir / "shaders" / "dust.vert").write_text("void main() {}", encoding="utf-8")
        (self.avatar1_dir / "shaders" / "dust.frag").write_text("void main() {}", encoding="utf-8")
        (self.avatar1_dir / "helper.py").write_text("print('skip')\n", encoding="utf-8")

        self.avatar2_dir = self.local_dir / "narratron"
        self.avatar2_dir.mkdir(parents=True)
        (self.avatar2_dir / "metadata.json").write_text(
            json.dumps({
                "id": "narratron",
                "title": "Narratron",
                "description": "Cosmic storyteller companion.",
                "created_at": "2026-08-20T12:00:00Z",
                "tags": ["Avatar", "Cosmic"],
            }),
            encoding="utf-8",
        )
        (self.avatar2_dir / "avatar.json").write_text(
            json.dumps({
                "id": "narratron",
                "title": "Narratron",
                "model": "model.glb",
                "poster": "preview.webp",
                "actions": [{"id": "idle"}, {"id": "greeting"}, {"id": "amused"}],
            }),
            encoding="utf-8",
        )
        (self.avatar2_dir / "preview.webp").write_bytes(b"RIFFwebp-narratron")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_avatars_sorted_newest_first(self) -> None:
        service = AvatarService(base_dir=self.local_dir)
        listed = service.list_avatars(force_refresh=True)

        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0]["id"], "narratron")
        self.assertEqual(listed[1]["id"], "light-cube")
        self.assertEqual(listed[0]["manifest_file"], "avatar.json")
        self.assertEqual(listed[0]["has_model"], False)
        self.assertEqual(listed[0]["action_count"], 3)
        self.assertEqual(listed[0]["shader_count"], 0)
        self.assertEqual(listed[0]["cover_image"], "preview.webp")
        self.assertEqual(listed[1]["manifest_file"], "avatar.json")
        self.assertEqual(listed[1]["has_model"], True)
        self.assertEqual(listed[1]["action_count"], 2)
        self.assertEqual(listed[1]["shader_count"], 2)

    def test_get_avatar_and_cover(self) -> None:
        service = AvatarService(base_dir=self.local_dir)
        avatar = service.get_avatar("light-cube")
        self.assertIsNotNone(avatar)
        assert avatar is not None
        self.assertEqual(avatar["title"], "Cube of Light")

        cover_res = service.get_avatar_cover("light-cube")
        self.assertIsNotNone(cover_res)
        assert cover_res is not None
        cover_data, ctype = cover_res
        self.assertEqual(cover_data, b"RIFFwebp-cube")
        self.assertIn("webp", ctype.lower())

    def test_load_avatar_assets_excludes_python_and_metadata(self) -> None:
        service = AvatarService(base_dir=self.local_dir)
        assets = service.load_avatar_assets("light-cube")
        self.assertEqual(set(assets.keys()), {
            "avatars/light-cube/avatar.json",
            "avatars/light-cube/model.glb",
            "avatars/light-cube/preview.webp",
            "avatars/light-cube/shaders/dust.vert",
            "avatars/light-cube/shaders/dust.frag",
        })

    def test_cache_invalidation_and_refresh(self) -> None:
        service = AvatarService(base_dir=self.local_dir)
        self.assertEqual(len(service.list_avatars()), 2)

        avatar3_dir = self.local_dir / "orb"
        avatar3_dir.mkdir(parents=True)
        (avatar3_dir / "metadata.json").write_text(
            json.dumps({
                "id": "orb",
                "title": "Mystic Orb",
                "created_at": "2026-08-25T00:00:00Z",
            }),
            encoding="utf-8",
        )
        (avatar3_dir / "avatar.json").write_text('{"id": "orb", "title": "Mystic Orb"}', encoding="utf-8")

        self.assertEqual(len(service.list_avatars()), 2)
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(len(service.list_avatars()), 3)
        self.assertEqual(service.list_avatars()[0]["id"], "orb")

        service.invalidate_cache()
        self.assertEqual(len(service.list_avatars()), 3)

    def test_get_avatars_root_flags(self) -> None:
        orig_local = FLAGS["testing_use_local"].value
        try:
            FLAGS["testing_use_local"].value = False
            self.assertEqual(get_avatars_root(), Path("/mnt/storage/avatars"))

            FLAGS["testing_use_local"].value = True
            self.assertEqual(get_avatars_root().name, "avatars")
        finally:
            FLAGS["testing_use_local"].value = orig_local

    def test_warm_listing_does_not_access_storage(self) -> None:
        service = AvatarService(self.local_dir)
        expected = service.list_avatars()
        with (
            patch.object(service, "_get_cache_key", side_effect=AssertionError("Unexpected storage scan")),
            patch.object(service, "_fetch_avatars", side_effect=AssertionError("Unexpected reload")),
        ):
            self.assertEqual(service.list_avatars(), expected)

    def test_refresh_serves_previous_snapshot_until_complete(self) -> None:
        service = AvatarService(self.local_dir)
        expected = service.list_avatars()
        original_fetch = service._fetch_avatars

        def fetch_during_refresh() -> list[dict[str, JsonValue]]:
            self.assertIs(service.list_avatars(), expected)
            return original_fetch()

        with patch.object(service, "_fetch_avatars", side_effect=fetch_during_refresh):
            self.assertTrue(service.refresh_if_changed(force_refresh=True))


def test_cloud_avatar_refresh_checks_only_gcs_sentinel(tmp_path: Path) -> None:
    mount = tmp_path / "avatars"
    avatar_dir = mount / "light-cube"
    avatar_dir.mkdir(parents=True)
    (avatar_dir / "metadata.json").write_text(
        json.dumps({"id": "light-cube", "title": "Cube of Light", "created_at": "2026-08-10T00:00:00Z"}),
        encoding="utf-8",
    )
    (avatar_dir / "avatar.json").write_text('{"id": "light-cube", "title": "Cube of Light"}', encoding="utf-8")

    sentinel_generation: list[int | None] = [None]
    requested_blobs: list[str] = []

    def fake_get_blob(name: str) -> SimpleNamespace | None:
        requested_blobs.append(name)
        gen = sentinel_generation[0]
        return SimpleNamespace(generation=gen) if gen is not None else None

    bucket = MagicMock(spec=storage.Bucket)
    bucket.get_blob.side_effect = fake_get_blob
    bucket.list_blobs.side_effect = AssertionError("Sentinel check must not list bucket objects")
    client = MagicMock(spec=storage.Client)
    client.bucket.return_value = bucket

    with (
        flagsaver.flagsaver(testing_use_local=False),
        patch.object(avatars, "get_avatars_root", return_value=mount),
        patch.object(avatars.storage, "Client", return_value=client),
        patch("services.catalog_sentinel.local_directory_cache_key", side_effect=AssertionError("Must not scan FUSE directory")),
        patch.dict("os.environ", {"GCS_BUCKET": "test-bucket", "GCS_AVATARS_PREFIX": "avatars"}),
    ):
        service = AvatarService(mount)
        listed = service.list_avatars(force_refresh=True)
        assert [item["id"] for item in listed] == ["light-cube"]
        assert requested_blobs == ["avatars/.catalog_sentinel.json"]

        with patch.object(service, "_fetch_avatars", side_effect=AssertionError("Unexpected reload")):
            assert service.refresh_if_changed() is False

        sentinel_generation[0] = 7
        (avatar_dir / "metadata.json").write_text(
            json.dumps({"id": "light-cube", "title": "Cube of Light v2", "created_at": "2026-08-10T00:00:00Z"}),
            encoding="utf-8",
        )
        assert service.refresh_if_changed() is True
        avatar = service.get_avatar("light-cube")
        assert avatar is not None
        assert avatar["title"] == "Cube of Light v2"

        with patch.object(service, "_fetch_avatars", side_effect=AssertionError("Unexpected reload")):
            assert service.refresh_if_changed() is False
        client.bucket.assert_called_with("test-bucket")
