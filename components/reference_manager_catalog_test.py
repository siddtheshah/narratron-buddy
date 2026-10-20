"""Reference discovery stays separate from generated image output discovery."""

from pathlib import Path

from PIL import Image, PngImagePlugin
import pytest

from components.reference_manager import ReferenceManager
from components.theater_manager import Theater, TheaterManager


@pytest.fixture
def theater(tmp_path: Path) -> Theater:
    theater = TheaterManager(base_theaters_dir=str(tmp_path)).theater("reference-catalog")
    theater.directory().mkdir(parents=True, exist_ok=True)
    (theater.directory() / "theater.yaml").write_text(
        "reference_generation_enabled: false\nstarting_image: title.png\n", encoding="utf-8"
    )
    return theater


def save_image(path: Path, description: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", description)
    Image.new("RGB", (8, 8), "blue").save(path, pnginfo=metadata)


def test_catalog_uses_latest_reference_versions_and_excludes_outputs(theater: Theater) -> None:
    authored = theater.scenes_dir() / "Harbor" / "1.png"
    updated = theater.updated_scenes_dir() / "Harbor" / "2.png"
    portrait = theater.characters_dir() / "Mira" / "1.png"
    output = theater.image_artifacts_dir() / "generated_harbor.png"
    cover = theater.references_dir() / "title.png"
    for path in (authored, updated, portrait, output, cover):
        save_image(path)
    manager = ReferenceManager(theater)

    entries = manager.list_references()
    assert {entry["path"] for entry in entries} == {str(updated), str(portrait)}
    assert {entry["alias"] for entry in entries} == {"<Harbor>", "<Mira>"}
    assert [entry["path"] for entry in manager.find_image_names("harbor")] == [str(updated)]
    assert manager.find_image_names("generated_harbor") == []
    assert [entry["path"] for entry in manager.list_references(include_characters=False)] == [str(updated)]


def test_catalog_refreshes_authored_assets_and_searches_metadata(theater: Theater) -> None:
    manager = ReferenceManager(theater)
    assert manager.list_references() == []
    reference = theater.references_dir() / "art" / "Candlelit Scribe.png"
    save_image(reference, "A monk writing by candlelight")

    entry = manager.find_image_names("MONK")[0]
    assert entry["path"] == str(reference)
    assert entry["alias"] == "art_Candlelit_Scribe"
    assert entry["description"] == "A monk writing by candlelight"


def test_catalog_refreshes_scene_iteration_without_reinitializing(theater: Theater) -> None:
    first = theater.scenes_dir() / "Harbor" / "1.png"
    save_image(first)
    manager = ReferenceManager(theater)
    assert manager.find_image_names("Harbor")[0]["path"] == str(first)
    latest = theater.updated_scenes_dir() / "Harbor" / "2.png"
    save_image(latest)
    assert manager.find_image_names("Harbor")[0]["path"] == str(latest)
