"""Package integrity and file-boundary tests for theater-builder drafts."""

from pathlib import Path
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from services.theater_builder import (
    BuilderProposal, FileMove, FileWrite, GenerationRequest, TheaterBuilderStore,
    safe_asset_path, upload_path,
)


@pytest.mark.parametrize("path", ["../secret.txt", "references/../../secret.png", "/references/a.png", "references/C:/a.png", "references/CON.png", "references/a.png.", "editor.json", "output/file.txt", "lore/script.py", "references/characters/hero.png", "references/scenes/harbor.png"])
def test_paths_cannot_escape_or_edit_internal_files(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        safe_asset_path(tmp_path, path)


@pytest.mark.parametrize(("name", "folder", "expected"), [
    ("hero.png", False, "references/hero.png"),
    ("stamp_orc.png", False, "stamps/stamp_orc.png"),
    ("token_hero.png", False, "stamps/token_hero.png"),
    ("rain.mp3", False, "playlists/default/rain.mp3"),
    ("notes.md", False, "lore/notes.txt"),
    ("world/references/people/hero.png", True, "references/people/hero.png"),
    ("world/stamps/tokens/hero.png", True, "stamps/tokens/hero.png"),
    ("world/characters/Arthur Modella/1.png", True, "references/characters/Arthur Modella/1.png"),
    ("world/references/characters/Arthur Modella/1.png", True, "references/characters/Arthur Modella/1.png"),
    ("references/characters/Arthur Modella.jpg", False, "references/characters/Arthur Modella/1.jpg"),
    ("references/characters/Arthur Modella/1.png", False, "references/characters/Arthur Modella/1.png"),
    ("world/scenes/Old Harbor/1.png", True, "references/scenes/Old Harbor/1.png"),
    ("world/references/scenes/Old Harbor/1.png", True, "references/scenes/Old Harbor/1.png"),
    ("references/scenes/Old Harbor.jpg", False, "references/scenes/Old Harbor/1.jpg"),
    ("references/scenes/Old Harbor/1.png", False, "references/scenes/Old Harbor/1.png"),
    ("scenes/Old Harbor.png", False, "references/scenes/Old Harbor/1.png"),
    ("world/playlists/mystery/rain.mp3", True, "playlists/mystery/rain.mp3"),
    ("world/theater.yaml", True, "theater.yaml"),
])
def test_flat_uploads_are_classified_and_folder_structure_is_preserved(name: str, folder: bool, expected: str) -> None:
    assert upload_path(name, folder) == expected


def test_folder_normalization_does_not_hide_traversal() -> None:
    with pytest.raises(ValueError):
        upload_path("../portrait.png", True)


def test_invalid_batch_does_not_partially_write_or_move_files(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    store.write_files(info, {"references/hero.png": b"image"})
    revision = info.revision
    with pytest.raises(ValueError):
        store.write_files(info, {"theater.yaml": b"[invalid, config]"}, [FileMove(source="references/hero.png", destination="references/people/hero.png")])
    assert info.revision == revision
    assert (store.directory(info.theater_id) / "references/hero.png").read_bytes() == b"image"
    assert not (store.directory(info.theater_id) / "references/people/hero.png").exists()


def test_draft_publish_removes_moved_assets_but_preserves_runtime_output(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path / "repository")
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    store.write_files(info, {"references/hero.png": b"image"})
    target = tmp_path / "runtime"
    target.mkdir()
    store.copy_to(info, target)
    (target / "output").mkdir()
    (target / "output/session.txt").write_text("runtime", encoding="utf-8")
    store.write_files(info, {}, [FileMove(source="references/hero.png", destination="references/people/hero.png")])
    store.copy_to(info, target)
    assert not (target / "references/hero.png").exists()
    assert (target / "references/people/hero.png").read_bytes() == b"image"
    assert (target / "output/session.txt").read_text(encoding="utf-8") == "runtime"


def test_existing_package_keeps_planning_and_nested_assets_in_draft(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "lore/characters").mkdir(parents=True)
    (source / "lore/characters/hero.txt").write_text("Hero", encoding="utf-8")
    (source / "planning.yaml").write_text("Player:\n  initial: Explorer\n", encoding="utf-8")
    (source / "theater.yaml").write_text("live_agent: {}\n", encoding="utf-8")
    (source / "theater.json").write_text("{}", encoding="utf-8")
    store = TheaterBuilderStore(tmp_path / "repository")
    info = store.create(7, "World", "live_agent: {}\n", "theater_existing", source)
    assert {item.path for item in store.files(info.theater_id)} == {"lore/characters/hero.txt", "theater.yaml", "planning.yaml"}
    assert store.load(info.theater_id).source_id == "theater_existing"


def test_assistant_returns_validated_proposals_without_mutating_draft(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    client = MagicMock()
    proposal = BuilderProposal(
        message="Add an opening scene and stamp.",
        writes=[{"path": "lore/opening.txt", "content": "A harbor at dawn."}],
        generations=[{"kind": "stamp", "name": "goblin", "prompt": "Goblin scout token"}],
    )
    client.models.generate_content.return_value.text = proposal.model_dump_json()
    with patch("services.theater_builder.genai.Client", return_value=client):
        result = store.propose(info, "Develop my world", [], {})
    assert result.writes[0].path == "lore/opening.txt"
    assert result.generations[0].kind == "stamp"
    assert result.generations[0].name == "goblin"
    assert not (store.directory(info.theater_id) / "lore/opening.txt").exists()
    client.close.assert_called_once()
    system_instruction = client.models.generate_content.call_args.kwargs["config"].system_instruction
    assert "kind 'stamp'" in system_instruction


def test_assistant_rejects_unsafe_generated_file_paths(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    client = MagicMock()
    client.models.generate_content.return_value.text = '{"message":"Change","writes":[{"path":"../secrets.txt","content":"bad"}]}'
    with patch("services.theater_builder.genai.Client", return_value=client), pytest.raises(ValueError):
        store.propose(info, "Develop my world", [], {})


def test_assistant_receives_image_previews_for_unnamed_uploads(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    buffer = BytesIO()
    Image.new("RGB", (800, 600), color="blue").save(buffer, format="PNG")
    store.write_files(info, {
        "references/IMG_001.png": buffer.getvalue(),
        "stamps/shovel.png": buffer.getvalue(),
        "stamps/cart.png": buffer.getvalue(),
    })
    client = MagicMock()
    client.models.generate_content.return_value.text = '{"message":"Organize the image"}'
    with patch("services.theater_builder.genai.Client", return_value=client):
        store.propose(info, "Organize my uploads", [], {})
    contents = client.models.generate_content.call_args.kwargs["contents"]
    assert len(contents) == 7
    assert contents[1].text == "Preview of references/IMG_001.png"
    assert {contents[3].text, contents[5].text} == {"Preview of stamps/cart.png", "Preview of stamps/shovel.png"}
    assert contents[2].inline_data.mime_type == "image/jpeg"
    with Image.open(BytesIO(contents[2].inline_data.data)) as preview:
        assert preview.width <= 512
        assert preview.height <= 512


def test_store_deletes_files_and_cleans_empty_directories(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    store.write_files(info, {
        "lore/chapter1/scene.txt": b"Scene content",
        "references/hero.png": b"hero",
    })
    initial_revision = info.revision
    store.delete_files(info, ["lore/chapter1/scene.txt"])
    assert info.revision == initial_revision + 1
    assert not (store.directory(info.theater_id) / "lore/chapter1/scene.txt").exists()
    assert not (store.directory(info.theater_id) / "lore/chapter1").exists()
    assert (store.directory(info.theater_id) / "references/hero.png").is_file()
    assert {item.path for item in store.files(info.theater_id)} == {"theater.yaml", "references/hero.png"}


def test_store_rejects_deleting_theater_yaml(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    with pytest.raises(ValueError, match="Cannot delete theater.yaml."):
        store.delete_files(info, ["theater.yaml"])


def test_store_rejects_deleting_nonexistent_or_duplicate_files(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    with pytest.raises(ValueError, match="File to delete does not exist"):
        store.delete_files(info, ["lore/missing.txt"])
    store.write_files(info, {"lore/scene.txt": b"scene"})
    with pytest.raises(ValueError, match="Duplicate deletion"):
        store.write_files(info, {}, deletions=["lore/scene.txt", "lore/scene.txt"])


def test_store_rejects_deletion_conflicts_with_writes_and_moves(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    store.write_files(info, {
        "lore/scene.txt": b"scene",
        "references/old.png": b"old",
    })
    with pytest.raises(ValueError, match="A file cannot be deleted and rewritten in the same change."):
        store.write_files(info, {"lore/scene.txt": b"new"}, deletions=["lore/scene.txt"])
    with pytest.raises(ValueError, match="A file cannot be deleted and moved in the same change."):
        store.write_files(info, {}, moves=[FileMove(source="references/old.png", destination="references/new.png")], deletions=["references/old.png"])


def test_assistant_proposes_deletions_and_validates_them(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    store.write_files(info, {"lore/obsolete.txt": b"old lore"})
    client = MagicMock()
    proposal = BuilderProposal(
        message="Remove obsolete lore document.",
        deletions=["lore/obsolete.txt"],
    )
    client.models.generate_content.return_value.text = proposal.model_dump_json()
    with patch("services.theater_builder.genai.Client", return_value=client):
        result = store.propose(info, "Delete old lore", [], {})
    assert result.deletions == ["lore/obsolete.txt"]
    system_instruction = client.models.generate_content.call_args.kwargs["config"].system_instruction
    assert "propose file deletions" in system_instruction
    assert "Never propose deleting theater.yaml" in system_instruction

    # Assistant proposing deletion of theater.yaml must be rejected
    bad_client = MagicMock()
    bad_proposal = BuilderProposal(message="Delete config", deletions=["theater.yaml"])
    bad_client.models.generate_content.return_value.text = bad_proposal.model_dump_json()
    with patch("services.theater_builder.genai.Client", return_value=bad_client), pytest.raises(ValueError, match="Cannot delete theater.yaml."):
        store.propose(info, "Delete theater.yaml", [], {})


def test_character_paths_allowed_in_safe_asset_path(tmp_path: Path) -> None:
    portrait = safe_asset_path(tmp_path, "references/characters/Arthur Modella/1.png")
    assert portrait == tmp_path / "references" / "characters" / "Arthur Modella" / "1.png"
    nested_portrait = safe_asset_path(tmp_path, "references/characters/Grim Vallos/alt/2.webp")
    assert nested_portrait == tmp_path / "references" / "characters" / "Grim Vallos" / "alt" / "2.webp"


def test_assistant_proposes_character_portraits_and_system_instruction(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path)
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    client = MagicMock()
    proposal = BuilderProposal(
        message="Add character dossier and portrait.",
        writes=[FileWrite(path="lore/characters/arthur.txt", content="Arthur Modella, the harbor captain.")],
        generations=[GenerationRequest(kind="character", name="Arthur Modella", prompt="Portrait of Arthur Modella, weathered captain")],
    )
    client.models.generate_content.return_value.text = proposal.model_dump_json()
    with patch("services.theater_builder.genai.Client", return_value=client):
        result = store.propose(info, "Create a harbor captain character", [], {})
    assert len(result.generations) == 1
    assert result.generations[0].kind == "character"
    assert result.generations[0].name == "Arthur Modella"
    system_instruction = client.models.generate_content.call_args.kwargs["config"].system_instruction
    assert "references/characters/<Character Name>" in system_instruction
    assert "references/scenes/<Scene Name>" in system_instruction
    assert "kind 'character'" in system_instruction
    assert "kind 'scene'" in system_instruction


def test_loading_theater_into_editor_preserves_characters_folder_and_assets(tmp_path: Path) -> None:
    source = tmp_path / "source_theater"
    source.mkdir()
    (source / "theater.yaml").write_text("live_agent: {}\n", encoding="utf-8")
    (source / "references" / "characters").mkdir(parents=True)
    (source / "references" / "characters" / "Arthur Modella").mkdir()
    (source / "references" / "characters" / "Arthur Modella" / "1.png").write_bytes(b"arthur_png")
    # Loose character file in source
    (source / "references" / "characters" / "Grim Vallos.png").write_bytes(b"grim_png")
    # Session updated character
    (source / "output" / "artifacts" / "updated_references" / "characters" / "Lyra").mkdir(parents=True)
    (source / "output" / "artifacts" / "updated_references" / "characters" / "Lyra" / "1.png").write_bytes(b"lyra_png")

    store = TheaterBuilderStore(tmp_path / "storage")
    info = store.create(7, "Preserved", "live_agent: {}\n", theater_id="theater_char", source=source)
    draft_dir = store.directory(info.theater_id)

    assert (draft_dir / "references" / "characters").is_dir()
    files = {item.path: item for item in store.files(info.theater_id)}
    assert "references/characters/Arthur Modella/1.png" in files
    assert "references/characters/Grim Vallos/1.png" in files
    assert "references/characters/Lyra/1.png" in files
    assert (draft_dir / "references" / "characters" / "Arthur Modella" / "1.png").read_bytes() == b"arthur_png"
    assert (draft_dir / "references" / "characters" / "Grim Vallos" / "1.png").read_bytes() == b"grim_png"
    assert (draft_dir / "references" / "characters" / "Lyra" / "1.png").read_bytes() == b"lyra_png"

    # Test copy_to also keeps characters directory
    target = tmp_path / "target_theater"
    target.mkdir()
    store.copy_to(info, target)
    assert (target / "references" / "characters").is_dir()
    assert (target / "references" / "characters" / "Arthur Modella" / "1.png").read_bytes() == b"arthur_png"


def test_existing_draft_syncs_characters_from_source_on_reopen(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path / "storage")
    info = store.create(7, "Draft", "live_agent: {}\n", theater_id="theater_reopen", source=None, populate_default=False)
    # Draft created without characters
    source = tmp_path / "source_theater"
    source.mkdir()
    (source / "references" / "characters" / "Grim Vallos").mkdir(parents=True)
    (source / "references" / "characters" / "Grim Vallos" / "1.png").write_bytes(b"grim_png")

    store.sync_source_characters(info, source)
    draft_dir = store.directory(info.theater_id)
    assert (draft_dir / "references" / "characters").is_dir()
    assert (draft_dir / "references" / "characters" / "Grim Vallos" / "1.png").read_bytes() == b"grim_png"


def test_loading_theater_into_editor_preserves_scenes_folder_and_assets(tmp_path: Path) -> None:
    source = tmp_path / "source_theater"
    source.mkdir()
    (source / "theater.yaml").write_text("live_agent: {}\n", encoding="utf-8")
    (source / "references" / "scenes").mkdir(parents=True)
    (source / "references" / "scenes" / "Old Harbor").mkdir()
    (source / "references" / "scenes" / "Old Harbor" / "1.png").write_bytes(b"harbor_png")
    (source / "references" / "scenes" / "Old Harbor" / "scene.yaml").write_bytes(b"description: Harbor at dawn\n")
    # Loose scene file in source
    (source / "references" / "scenes" / "High Peaks.png").write_bytes(b"peaks_png")
    # Session updated scene
    (source / "output" / "artifacts" / "updated_references" / "scenes" / "Sunken Crypt").mkdir(parents=True)
    (source / "output" / "artifacts" / "updated_references" / "scenes" / "Sunken Crypt" / "1.png").write_bytes(b"crypt_png")

    store = TheaterBuilderStore(tmp_path / "storage")
    info = store.create(7, "Preserved", "live_agent: {}\n", theater_id="theater_scene", source=source)
    draft_dir = store.directory(info.theater_id)

    assert (draft_dir / "references" / "scenes").is_dir()
    files = {item.path: item for item in store.files(info.theater_id)}
    assert "references/scenes/Old Harbor/1.png" in files
    assert "references/scenes/Old Harbor/scene.yaml" in files
    assert "references/scenes/High Peaks/1.png" in files
    assert "references/scenes/Sunken Crypt/1.png" in files
    assert (draft_dir / "references" / "scenes" / "Old Harbor" / "1.png").read_bytes() == b"harbor_png"
    assert (draft_dir / "references" / "scenes" / "High Peaks" / "1.png").read_bytes() == b"peaks_png"
    assert (draft_dir / "references" / "scenes" / "Sunken Crypt" / "1.png").read_bytes() == b"crypt_png"

    # Test copy_to also keeps scenes directory
    target = tmp_path / "target_theater"
    target.mkdir()
    store.copy_to(info, target)
    assert (target / "references" / "scenes").is_dir()
    assert (target / "references" / "scenes" / "Old Harbor" / "1.png").read_bytes() == b"harbor_png"


def test_existing_draft_syncs_scenes_from_source_on_reopen(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path / "storage")
    info = store.create(7, "Draft", "live_agent: {}\n", theater_id="theater_reopen_scene", source=None, populate_default=False)
    source = tmp_path / "source_theater"
    source.mkdir()
    (source / "references" / "scenes" / "Old Harbor").mkdir(parents=True)
    (source / "references" / "scenes" / "Old Harbor" / "1.png").write_bytes(b"harbor_png")

    store.sync_source_scenes(info, source)
    draft_dir = store.directory(info.theater_id)
    assert (draft_dir / "references" / "scenes").is_dir()
    assert (draft_dir / "references" / "scenes" / "Old Harbor" / "1.png").read_bytes() == b"harbor_png"




def test_character_traits_survive_builder_source_copy_edit_and_publish(tmp_path: Path) -> None:
    source = tmp_path / "source"
    character = source / "references/characters/Arthur Modella/character.yaml"
    character.parent.mkdir(parents=True)
    character.write_text("personality: Patient\n", encoding="utf-8")
    store = TheaterBuilderStore(tmp_path / "repository")
    info = store.create(7, "World", "live_agent: {}\n", source=source, populate_default=False)
    relative = "references/characters/Arthur Modella/character.yaml"
    assert store.directory(info.theater_id).joinpath(relative).read_text(encoding="utf-8") == "personality: Patient\n"
    store.write_files(info, {relative: b"personality: Bold\n"})
    target = tmp_path / "runtime"
    target.mkdir()
    store.copy_to(info, target)
    assert target.joinpath(relative).read_bytes() == b"personality: Bold\n"
    assert character.read_text(encoding="utf-8") == "personality: Patient\n"


def test_scene_yaml_survives_builder_copy_edit_and_publish(tmp_path: Path) -> None:
    source = tmp_path / "source"
    relative = "references/scenes/Old Harbor/scene.yaml"
    profile = source / relative
    profile.parent.mkdir(parents=True)
    profile.write_text("description: A harbor at dawn\n", encoding="utf-8")
    store = TheaterBuilderStore(tmp_path / "repository")
    info = store.create(7, "World", "live_agent: {}\n", source=source, populate_default=False)
    assert (store.directory(info.theater_id) / relative).read_bytes() == profile.read_bytes()
    store.write_files(info, {relative: b"description: A harbor at sunset\n"})
    target = tmp_path / "runtime"
    target.mkdir()
    store.copy_to(info, target)
    assert (target / relative).read_bytes() == b"description: A harbor at sunset\n"
    assert profile.read_text(encoding="utf-8") == "description: A harbor at dawn\n"
