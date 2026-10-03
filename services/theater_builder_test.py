"""Package integrity and file-boundary tests for theater-builder drafts."""

from pathlib import Path
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from services.theater_builder import (
    BuilderProposal, FileMove, TheaterBuilderStore, safe_asset_path, upload_path,
)


@pytest.mark.parametrize("path", ["../secret.txt", "references/../../secret.png", "/references/a.png", "references/C:/a.png", "references/CON.png", "references/a.png.", "editor.json", "output/file.txt", "lore/script.py"])
def test_paths_cannot_escape_or_edit_internal_files(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        safe_asset_path(tmp_path, path)


@pytest.mark.parametrize(("name", "folder", "expected"), [
    ("hero.png", False, "references/hero.png"),
    ("rain.mp3", False, "playlists/default/rain.mp3"),
    ("notes.md", False, "lore/notes.txt"),
    ("world/references/people/hero.png", True, "references/people/hero.png"),
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
        store.write_files(info, {"theater.yaml": b"[invalid, config]"}, [FileMove(source="references/hero.png", destination="references/characters/hero.png")])
    assert info.revision == revision
    assert (store.directory(info.theater_id) / "references/hero.png").read_bytes() == b"image"
    assert not (store.directory(info.theater_id) / "references/characters/hero.png").exists()


def test_draft_publish_removes_moved_assets_but_preserves_runtime_output(tmp_path: Path) -> None:
    store = TheaterBuilderStore(tmp_path / "repository")
    info = store.create(7, "World", "live_agent: {}\n", populate_default=False)
    store.write_files(info, {"references/hero.png": b"image"})
    target = tmp_path / "runtime"
    target.mkdir()
    store.copy_to(info, target)
    (target / "output").mkdir()
    (target / "output/session.txt").write_text("runtime", encoding="utf-8")
    store.write_files(info, {}, [FileMove(source="references/hero.png", destination="references/characters/hero.png")])
    store.copy_to(info, target)
    assert not (target / "references/hero.png").exists()
    assert (target / "references/characters/hero.png").read_bytes() == b"image"
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
    proposal = BuilderProposal(message="Add an opening scene.", writes=[{"path": "lore/opening.txt", "content": "A harbor at dawn."}])
    client.models.generate_content.return_value.text = proposal.model_dump_json()
    with patch("services.theater_builder.genai.Client", return_value=client):
        result = store.propose(info, "Develop my world", [], {})
    assert result.writes[0].path == "lore/opening.txt"
    assert not (store.directory(info.theater_id) / "lore/opening.txt").exists()
    client.close.assert_called_once()


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
    store.write_files(info, {"references/IMG_001.png": buffer.getvalue()})
    client = MagicMock()
    client.models.generate_content.return_value.text = '{"message":"Organize the image"}'
    with patch("services.theater_builder.genai.Client", return_value=client):
        store.propose(info, "Organize my uploads", [], {})
    contents = client.models.generate_content.call_args.kwargs["contents"]
    assert contents[1].text == "Preview of references/IMG_001.png"
    assert contents[2].inline_data.mime_type == "image/jpeg"
    with Image.open(BytesIO(contents[2].inline_data.data)) as preview:
        assert preview.width <= 512
        assert preview.height <= 512
