"""GCS theater saves using an explicitly injected bucket."""

import base64
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.cloud import storage
import pytest
from absl.testing import flagsaver

from storage.gcs_theater_repository import GCSTheaterRepository


@pytest.mark.parametrize("fail_upload", [False, True])
def test_cloud_editor_save_uploads_changes_and_prunes_removed_assets(tmp_path: Path, fail_upload: bool) -> None:
    checkout = tmp_path / "checkout"
    files = {
        "theater.json": b'{"theater_id":"quest","name":"Edited Quest"}',
        "theater.yaml": b"live_agent: {style: edited}",
        "references/hero.png": b"unchanged-image",
        "playlists/ambient/music.mp3": b"unchanged-music",
        "lore/new.md": b"new-lore",
    }
    previous = {
        **files,
        "theater.json": b'{"theater_id":"quest","name":"Old Quest"}',
        "theater.yaml": b"live_agent: {}",
        "lore/removed.md": b"removed-lore",
        "output/retained.png": b"runtime-output",
    }
    del previous["lore/new.md"]
    for name, content in files.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    bucket = MagicMock(spec=storage.Bucket)
    repository = GCSTheaterRepository(tmp_path / "repository", bucket)
    existing: dict[str, MagicMock] = {}
    for name, content in previous.items():
        blob = MagicMock(spec=storage.Blob, generation=42)
        blob.name = "theaters/quest/" + name
        blob.md5_hash = base64.b64encode(hashlib.md5(content).digest()).decode("ascii")
        existing[name] = blob
    bucket.list_blobs.return_value = list(existing.values())
    uploads: list[str] = []

    def destination_blob(name: str) -> MagicMock:
        result = MagicMock(spec=storage.Blob)
        relative = name.removeprefix("theaters/quest/")

        def upload(filename: str, *, if_generation_match: int) -> None:
            assert if_generation_match == (42 if relative in previous else 0)
            assert Path(filename).read_bytes() == files[relative]
            if fail_upload and relative == "theater.yaml":
                raise OSError("Upload failed")
            uploads.append(relative)

        result.upload_from_filename.side_effect = upload
        return result

    bucket.blob.side_effect = destination_blob
    with (
        patch("storage.theater_repository.shutil.copyfile", side_effect=AssertionError("No mounted copies")),
    ):
        assert repository.export_theater("quest", checkout) == (not fail_upload)

    bucket.list_blobs.assert_called_once_with(prefix="theaters/quest/")
    bucket.copy_blob.assert_not_called()
    if fail_upload:
        assert "theater.json" not in uploads
        existing["lore/removed.md"].delete.assert_not_called()
    else:
        assert set(uploads) == {"theater.yaml", "theater.json", "lore/new.md"}
        assert uploads[-1] == "theater.json"
        existing["lore/removed.md"].delete.assert_called_once_with(if_generation_match=42)
    existing["output/retained.png"].delete.assert_not_called()
    existing["references/hero.png"].delete.assert_not_called()


@pytest.mark.parametrize("local_mode", [False, True])
def test_unchanged_cloud_save_does_not_upload_files(tmp_path: Path, local_mode: bool) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    content = b'{"theater_id":"quest"}'
    (checkout / "theater.json").write_bytes(content)
    blob = MagicMock(spec=storage.Blob)
    blob.name = "theaters/quest/theater.json"
    blob.md5_hash = base64.b64encode(hashlib.md5(content).digest()).decode("ascii")
    bucket = MagicMock(spec=storage.Bucket)
    repository = GCSTheaterRepository(tmp_path / "repository", bucket)
    bucket.list_blobs.return_value = [blob]
    with flagsaver.flagsaver(testing_use_local=local_mode):
        assert repository.export_theater("quest", checkout)
    bucket.blob.assert_not_called()
    blob.delete.assert_not_called()


@pytest.mark.parametrize("fail_copy", [False, True])
def test_catalog_save_copies_assets_without_uploading_them(tmp_path: Path, fail_copy: bool) -> None:
    checkout = tmp_path / "checkout"
    files = {
        "theater.json": b'{"theater_id":"quest"}',
        "theater.yaml": b"live_agent: {style: edited}",
        "references/hero.png": b"catalog-image",
        "playlists/ambient/music.mp3": b"catalog-music",
        "lore/world.md": b"uploaded-override",
        "planning.yaml": b"HUD: {}",
        "editor.json": b"private",
    }
    for name, content in files.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    bucket = MagicMock(spec=storage.Bucket)
    repository = GCSTheaterRepository(tmp_path / "repository", bucket)
    catalog_assets: dict[str, storage.Blob] = {}
    for name, content in {
        "references/hero.png": b"catalog-image",
        "playlists/ambient/music.mp3": b"catalog-music",
        "lore/world.md": b"original-lore",
        "theater.yaml": b"live_agent: {}",
        "nested/planning.yml": b"HUD: {}",
    }.items():
        checksum = base64.b64encode(hashlib.md5(content).digest()).decode("ascii")
        catalog_assets[checksum] = storage.Blob("adventures/Actual Folder/" + name, bucket, generation=42)
    copied: list[str] = []
    uploaded: list[str] = []
    saved: list[MagicMock] = []

    def copy_blob(
        source: storage.Blob, destination: storage.Bucket, name: str, *,
        source_generation: int, if_source_generation_match: int, if_generation_match: int,
    ) -> MagicMock:
        assert destination is bucket
        assert source_generation == if_source_generation_match == source.generation == 42
        assert if_generation_match == 0
        if fail_copy and name.endswith("hero.png"):
            raise OSError("Copy failed")
        copied.append(name)
        result = MagicMock(spec=storage.Blob, generation=99)
        saved.append(result)
        return result

    def blob(name: str) -> MagicMock:
        result = MagicMock(spec=storage.Blob, generation=99)

        def upload(filename: str, *, if_generation_match: int) -> None:
            assert if_generation_match == 0
            assert Path(filename).read_bytes() == files[name.removeprefix("theaters/quest/")]
            if name.endswith("theater.json"):
                assert len(copied) == 3
                assert len(uploaded) == 2
            uploaded.append(name)
            saved.append(result)

        result.upload_from_filename.side_effect = upload
        return result

    bucket.copy_blob.side_effect = copy_blob
    bucket.blob.side_effect = blob
    with (
        patch("storage.theater_repository.shutil.copyfile", side_effect=AssertionError("No mounted copies")),
    ):
        assert repository.export_catalog_theater("quest", checkout, catalog_assets) == (not fail_copy)

    if fail_copy:
        assert "theaters/quest/theater.json" not in uploaded
        for result in saved:
            result.delete.assert_called_once_with(if_generation_match=99)
    else:
        assert set(copied) == {
            "theaters/quest/references/hero.png", "theaters/quest/playlists/ambient/music.mp3",
            "theaters/quest/planning.yaml",
        }
        assert set(uploaded) == {
            "theaters/quest/theater.yaml", "theaters/quest/lore/world.md", "theaters/quest/theater.json",
        }


