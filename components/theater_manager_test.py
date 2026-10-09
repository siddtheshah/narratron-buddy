"""Tests for the theater workspace boundary."""

import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import pytest
from testing.reference_images import png_bytes

from absl.testing import flagsaver

from components.theater_manager import (
    TheaterManager,
    asset_destination,
    extract_asset_package,
    get_ephemeral_root,
)


def test_character_images_in_uploaded_package_preserve_named_folders(tmp_path: Path) -> None:
    manager = TheaterManager(tmp_path)
    image = png_bytes()
    package_bytes = io.BytesIO()
    with zipfile.ZipFile(package_bytes, "w") as package:
        package.writestr("assets/references/characters/Arthur Modella/1.png", image)
        package.writestr("assets/references/characters/Grim Vallos/1.png", image)
    reference_files, playlists_data, lore_files, _ = extract_asset_package(package_bytes.getvalue())
    manager.create_theater("Stage", "stage", reference_files=reference_files, playlists_data=playlists_data, lore_files=lore_files)
    assert (tmp_path / "stage/references/characters/Arthur Modella/1.png").read_bytes() == image
    assert (tmp_path / "stage/references/characters/Grim Vallos/1.png").read_bytes() == image
    assert not (tmp_path / "stage/references/1.png").exists()
    assert not (tmp_path / "stage/references/Grim Vallos.png").exists()
    theater = manager.theater("stage")
    assert theater.characters_dir() == tmp_path / "stage" / "references" / "characters"
    assert theater.updated_characters_dir() == tmp_path / "stage" / "output" / "artifacts" / "updated_references" / "characters"
    assert theater.scenes_dir() == tmp_path / "stage" / "references" / "scenes"
    assert theater.updated_scenes_dir() == tmp_path / "stage" / "output" / "artifacts" / "updated_references" / "scenes"


def test_loose_character_portrait_upload_is_rejected_before_writes(tmp_path: Path) -> None:
    manager = TheaterManager(tmp_path)
    with pytest.raises(ValueError, match="must be inside"):
        manager.create_theater("Stage", "stage", reference_files=[
            ("references/characters/Grim Vallos/1.png", png_bytes()),
            ("references/characters/Arthur Modella.png", png_bytes()),
        ])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("filename", [
    "references/../../../outside.png", "../references/hero.png",
    "references/../hero.png", "references\\..\\..\\outside.png",
    "/references/hero.png", "C:/references/hero.png", "C:hero.png",
    "\\\\server\\share\\references\\hero.png", "references/C:/hero.png",
    "references/./hero.png", "references//hero.png", "references/hero.png:stream",
    "references/.. /hero.png", "references/NUL.png", "",
    "../planning.yaml", "../metadata.json", "stamps/../hero.png",
    "references/characters/../../outside.png", "references/characters/Arthur/../1.png",
])
def test_upload_paths_are_rejected_before_any_writes(tmp_path: Path, filename: str) -> None:
    manager = TheaterManager(tmp_path)
    with pytest.raises(ValueError):
        manager.create_theater("Attack", "attack", reference_files=[
            ("references/safe.png", png_bytes()), (filename, png_bytes()),
        ])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("filename", [
    "lore/../../../outside.txt", "../lore/notes.txt", "/lore/notes.txt",
    "C:\\lore\\notes.txt", "lore\\..\\notes.txt",
])
def test_lore_upload_paths_are_rejected_before_any_writes(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError):
        TheaterManager(tmp_path).create_theater("Attack", "attack",
            reference_files=[("safe.png", png_bytes())], lore_files=[(filename, b"Lore")])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("playlist", ["../outside", "/outside", "C:\\outside", "C:outside", "..", "nested/name", "nested\\name"])
def test_playlist_names_are_rejected_before_any_writes(tmp_path: Path, playlist: str) -> None:
    with pytest.raises(ValueError):
        TheaterManager(tmp_path).create_theater("Attack", "attack",
            playlists_data={"safe": [("safe.mp3", b"audio")], playlist: [("song.mp3", b"audio")]})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("filename", ["../song.mp3", "/song.mp3", "C:track.mp3", "folder\\..\\song.mp3"])
def test_playlist_track_paths_are_validated_before_flattening(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError):
        TheaterManager(tmp_path).create_theater("Attack", "attack", playlists_data={"ambient": [(filename, b"audio")]})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("theater_id", ["../outside", "/outside", "C:outside", "nested/name", "nested\\name", ""])
def test_creation_rejects_unsafe_theater_ids(tmp_path: Path, theater_id: str) -> None:
    with pytest.raises(ValueError):
        TheaterManager(tmp_path).create_theater("Attack", theater_id)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("filename", [
    "references/../../../outside.png", "lore/../../../outside.txt",
    "playlists/../../outside/song.mp3", "../planning.yaml", "/references/hero.png",
    "C:/references/hero.png", "references\\..\\..\\outside.png", "../ignored.bin", "../directory/",
])
def test_zip_rejects_unsafe_paths_before_classification(tmp_path: Path, filename: str) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("references/safe.png", png_bytes())
        package.writestr(filename, png_bytes())
    with pytest.raises(ValueError):
        references, playlists, lore, _ = extract_asset_package(archive.getvalue())
        TheaterManager(tmp_path).create_theater("Attack", "attack",
            reference_files=references, playlists_data=playlists, lore_files=lore)
    assert list(tmp_path.iterdir()) == []


def test_resolved_asset_destination_cannot_escape_through_link(tmp_path: Path) -> None:
    root = tmp_path / "references"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"original")
    requested = root / "linked.png"
    original_resolve = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        # Windows may not grant symlink creation privileges; model its resolution.
        return outside if path == requested else original_resolve(path, strict=strict)

    with patch.object(Path, "resolve", resolve), pytest.raises(ValueError):
        asset_destination(root, "linked.png")
    assert outside.read_bytes() == b"original"


def test_valid_package_preserves_nested_assets_and_windows_separators(tmp_path: Path) -> None:
    image = png_bytes()
    manager = TheaterManager(tmp_path)
    metadata = manager.create_theater("Safe", "safe",
        reference_files=[("adventure\\references\\maps\\hero.png", image), ("planning.yaml", b"scenes: []")],
        lore_files=[("adventure/lore/world/history.txt", b"History")],
        playlists_data={"ambient": [("folder\\rain.mp3", b"audio")]})
    assert metadata.mounted_references.count("maps/hero.png") == 1
    assert (tmp_path / "safe/references/maps/hero.png").read_bytes() == image
    assert (tmp_path / "safe/lore/world/history.txt").read_bytes() == b"History"
    assert (tmp_path / "safe/playlists/ambient/rain.mp3").read_bytes() == b"audio"
    assert (tmp_path / "safe/planning.yaml").read_bytes() == b"scenes: []"


class TestTheaterRootSelection(unittest.TestCase):
    def test_cloud_ephemeral_root_defaults_to_tmp(self):
        self.assertEqual(get_ephemeral_root(), Path("/tmp/ephemeral"))

    @flagsaver.flagsaver(testing_use_local=True)
    def test_local_ephemeral_root_uses_workspace_ephemeral_directory(self):
        self.assertEqual(
            get_ephemeral_root().resolve(),
            (Path(__file__).parent.parent / "ephemeral").resolve(),
        )

    def test_theater_manager_defaults_to_ephemeral_root(self):
        tm = TheaterManager()
        self.assertEqual(tm.base_dir, get_ephemeral_root().resolve())


class TestTheaterManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = TheaterManager(base_theaters_dir=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_stamp_package_import_and_repository_round_trip(self) -> None:
        from storage.theater_repository import TheaterRepository

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("adventure/stamps/tokens/hero token.png", b"stamp-image")
        images, playlists, lore, config = extract_asset_package(archive.getvalue())
        metadata = self.manager.create_theater("Stamps", "stamp-stage", reference_files=images)
        theater = self.manager.theater("stamp-stage")
        self.assertNotIn("hero token.png", metadata.mounted_references)
        self.assertEqual(theater.stamps(), [{
            "id": "theater:stamp-stage:tokens/hero token.png",
            "name": "hero token",
            "url": "/theaters/stamp-stage/stamps/tokens/hero%20token.png",
        }])
        (theater.stamps_dir() / "notes.txt").write_text("ignored", encoding="utf-8")
        self.assertEqual(len(theater.stamps()), 1)
        repository = TheaterRepository(Path(self.temp_dir.name) / "repository")
        self.assertTrue(repository.export_theater("stamp-stage", theater.directory()))
        restored = Path(self.temp_dir.name) / "restored"
        self.assertTrue(repository.reconstruct_theater("stamp-stage", restored))
        self.assertEqual((restored / "stamps/tokens/hero token.png").read_bytes(), b"stamp-image")

    def test_stamp_import_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.create_theater("Invalid", "bad-stamps", reference_files=[("stamps/../escape.png", b"bad")])

    def test_missing_stamp_directory_returns_empty_list(self) -> None:
        self.assertEqual(self.manager.theater("missing").stamps(), [])

    def test_create_deploy_stop_and_destroy_theater(self) -> None:
        theater = self.manager.create_theater(
            name="Fantasy Quest",
            theater_id="quest",
            reference_files=[("references/maps/hero.png", png_bytes())],
            playlists_data={"ambient": [("rain.mp3", b"audio")]},
            theater_config={"live_agent": {"special_instructions": "painted fantasy"}},
        )

        theater_dir = Path(self.temp_dir.name) / "quest"
        self.assertEqual(theater.status, "created")
        self.assertTrue((theater_dir / "references" / "maps" / "hero.png").exists())
        self.assertTrue((theater_dir / "playlists" / "ambient" / "rain.mp3").exists())
        self.assertIn("special_instructions: painted fantasy", (theater_dir / "theater.yaml").read_text(encoding="utf-8"))
        self.assertEqual(self.manager.deploy_theater("quest").status, "deployed")
        self.assertEqual(self.manager.stop_theater("quest").status, "stopped")
        self.assertTrue(self.manager.destroy_theater("quest"))
        self.assertFalse(theater_dir.exists())

    def test_asset_reads_do_not_create_a_missing_theater_workspace(self):
        self.assertEqual(self.manager.get_theater_references("missing"), [])
        self.assertEqual(self.manager.get_theater_playlists("missing"), {})
        self.assertFalse(self.manager.theater("missing").directory().exists())

    def test_theater_binds_workspace_paths_and_lifecycle_operations(self):
        self.manager.create_theater(name="Bound Theater", theater_id="bound")
        theater = self.manager.theater("bound")

        self.assertEqual(theater.directory(), Path(self.temp_dir.name) / "bound")
        self.assertEqual(theater.references_dir(), theater.directory() / "references")
        self.assertEqual(theater.image_artifacts_dir(), theater.directory() / "output" / "artifacts" / "images")
        self.assertEqual(theater.music_artifacts_dir(), theater.directory() / "output" / "music")
        self.assertEqual(theater.chats_dir(), theater.directory() / "output" / "chats")
        self.assertIsInstance(theater.config(), dict)
        self.assertEqual(theater.config(), theater.get_config())
        self.assertEqual(theater.metadata.name, "Bound Theater")
        self.assertEqual(theater.deploy().status, "deployed")
        self.assertTrue(theater.destroy())

    def test_get_theater_migrates_canvas_only_legacy_metadata(self):
        theater_dir = Path(self.temp_dir.name) / "legacy"
        theater_dir.mkdir()
        (theater_dir / "theater.json").write_text(
            '{"canvas_state": {"chat_messages": []}}', encoding="utf-8"
        )

        theater = self.manager.get_theater("legacy")

        self.assertEqual(theater.theater_id, "legacy")
        self.assertEqual(theater.name, "legacy")
        self.assertEqual(theater.canvas_state, {"chat_messages": []})
        persisted = (theater_dir / "theater.json").read_text(encoding="utf-8")
        self.assertIn('"theater_id": "legacy"', persisted)
        self.assertIn('"name": "legacy"', persisted)

    def test_record_auto_begin_persists_the_timestamp(self):
        self.manager.create_theater(name="Auto Begin", theater_id="auto-begin")

        recorded_at = self.manager.record_auto_begin("auto-begin")

        self.assertIsNotNone(recorded_at)
        self.assertEqual(
            self.manager.get_theater("auto-begin").last_auto_begin_at,
            recorded_at,
        )

    def test_extract_asset_package_groups_assets_and_rejects_oversize_input(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("assets/references/hero.png", b"image")
            zip_file.writestr("assets/playlists/ambient/rain.mp3", b"audio")
            zip_file.writestr("assets/lore/kingdom_history.txt", "The kingdom was founded by navigators.")
            zip_file.writestr("assets/lore/secret.pdf", b"not text")
            zip_file.writestr("assets/theater.yaml", "agent:\n  style: folder style\n")

        references, playlists, lore, theater_config_yaml = extract_asset_package(archive.getvalue())
        self.assertEqual(references, [("assets/references/hero.png", b"image")])
        self.assertEqual(playlists, {"ambient": [("rain.mp3", b"audio")]})
        self.assertEqual(lore, [("assets/lore/kingdom_history.txt", b"The kingdom was founded by navigators.")])
        self.assertEqual(theater_config_yaml, "agent:\n  style: folder style\n")
        with self.assertRaises(ValueError):
            extract_asset_package(b"0" * (10 * 1024 * 1024 + 1))

    def test_lore_documents_are_text_only_and_cannot_escape_lore_directory(self):
        self.manager.create_theater(
            name="Lore Theater",
            theater_id="lore",
            lore_files=[("lore/world/setting.txt", b"Floating cities.")],
        )

        self.assertEqual(self.manager.get_lore_documents("lore"), ["world/setting.txt"])
        self.assertEqual(self.manager.read_lore_document("lore", "world/setting.txt"), "Floating cities.")
        with self.assertRaises(ValueError):
            self.manager.read_lore_document("lore", "../theater.yaml")
        with self.assertRaises(ValueError):
            self.manager.create_theater(
                name="Invalid Lore",
                theater_id="invalid-lore",
                lore_files=[("lore/notes.md", b"Not accepted.")],
            )

    def testget_url_for_path_resolves_output_and_reference_urls(self):
        theater = self.manager.theater("stage")
        output_file = str(theater.output_dir() / "animations" / "hero_anim" / "frame_1.png")
        url = theater.get_url_for_path(output_file)
        self.assertEqual(url, "/theaters/stage/output/animations/hero_anim/frame_1.png")

        rel_url = theater.get_url_for_path("animations/hero_anim/frame_1.png")
        self.assertEqual(rel_url, "/theaters/stage/output/animations/hero_anim/frame_1.png")

    def test_append_and_read_output_file_lines(self):
        self.manager.create_theater(name="Output Theater", theater_id="out_test")
        theater = self.manager.theater("out_test")
        self.assertEqual(theater.read_output_file_lines("story_log.jsonl"), [])

        theater.append_output_file("story_log.jsonl", '{"turn": 1}\n')
        theater.append_output_file("story_log.jsonl", '{"turn": 2}\n')

        lines = theater.read_output_file_lines("story_log.jsonl")
        self.assertEqual(lines, ['{"turn": 1}\n', '{"turn": 2}\n'])

    def test_output_file_cannot_escape_output_directory(self):
        self.manager.create_theater(name="Output Escape Theater", theater_id="out_esc")
        theater = self.manager.theater("out_esc")
        with self.assertRaises(ValueError):
            theater.append_output_file("../forbidden.txt", "data")
        with self.assertRaises(ValueError):
            theater.read_output_file_lines("../forbidden.txt")
        with self.assertRaises(ValueError):
            theater.append_output_file("", "data")

    def test_theater_does_not_accept_custom_config(self):
        with self.assertRaises(TypeError):
            self.manager.theater("out_test", custom_config={"key": "val"})  # type: ignore


def test_character_yaml_upload_is_preserved_beside_portraits(tmp_path: Path) -> None:
    manager = TheaterManager(tmp_path)
    manager.create_theater("Stage", "stage", reference_files=[
        ("references/characters/Arthur Modella/1.png", png_bytes()),
        ("references/characters/Arthur Modella/character.yaml", b"personality: Patient\n"),
    ])
    assert (manager.theater("stage").characters_dir() / "Arthur Modella" / "character.yaml").read_bytes() == b"personality: Patient\n"


def test_scene_yaml_upload_is_preserved_beside_scene_images(tmp_path: Path) -> None:
    manager = TheaterManager(tmp_path)
    manager.create_theater("Stage", "stage", reference_files=[
        ("references/scenes/Old Harbor/1.png", png_bytes()),
        ("references/scenes/Old Harbor/scene.yaml", b"description: A harbor at dawn\n"),
    ])
    assert (manager.theater("stage").scenes_dir() / "Old Harbor" / "scene.yaml").read_bytes() == b"description: A harbor at dawn\n"
