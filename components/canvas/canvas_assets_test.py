"""Media and regions stay pinned to the artwork selected by the canvas host."""
from hashlib import sha256
from pathlib import Path

from PIL import Image
import pytest
import yaml

from components.canvas.canvas_assets import CanvasAssetCatalog


def catalog_for(directory: Path) -> CanvasAssetCatalog:
    def url(path: str) -> str:
        return "/theaters/test/" + Path(path).relative_to(directory).as_posix()

    return CanvasAssetCatalog(directory, url)


def test_scene_revisions_pin_bytes_and_do_not_reuse_regions(tmp_path: Path) -> None:
    scene = tmp_path / "references" / "scenes" / "door"
    scene.mkdir(parents=True)
    artwork = scene / "1.png"
    Image.new("RGB", (400, 200), "red").save(artwork)
    digest = sha256(artwork.read_bytes()).hexdigest()
    (scene / "scene.yaml").write_text(yaml.safe_dump({"description": "Door", "canvas": {"artwork": {
        f"sha256:{digest}": {"lock": {"id": "lock", "bounds": {"x": 100, "y": 200, "width": 300, "height": 400}}}
    }}}), encoding="utf-8")
    catalog = catalog_for(tmp_path)
    first = catalog.scene("door")
    assert first.scene_id == "door" and first.width == 400 and first.height == 200
    assert set(first.regions) == {"lock"}
    assert first.asset_id == f"image:{digest}"
    assert first.url.endswith(f"/{digest}.png")
    assert Path(first.path).read_bytes() == artwork.read_bytes()
    Image.new("RGB", (800, 400), "blue").save(artwork)
    second = catalog.scene("door")
    assert second.asset_id != first.asset_id and second.regions == {}
    assert catalog.resolve_revision(first.asset_id).asset_id == first.asset_id
    with Image.open(first.path) as old:
        assert old.getpixel((0, 0)) == (255, 0, 0)


def test_updated_scene_is_selected_before_original_artwork(tmp_path: Path) -> None:
    for parent, color in [("references", "red"), ("artifacts/updated_references", "blue")]:
        directory = tmp_path / parent / "scenes" / "room"
        directory.mkdir(parents=True)
        Image.new("RGB", (10, 20), color).save(directory / "1.png")
    asset = catalog_for(tmp_path).scene("room")
    with Image.open(asset.path) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)


def test_ambiguous_stamps_and_arbitrary_paths_are_not_game_handles(tmp_path: Path) -> None:
    directory = tmp_path / "stamps"
    directory.mkdir()
    Image.new("RGB", (10, 20)).save(directory / "key.png")
    catalog = catalog_for(tmp_path)
    assert catalog.stamp("stamp:key").asset_id == "stamp:key"
    Image.new("RGB", (10, 20)).save(directory / "key.webp")
    with pytest.raises(ValueError, match="ambiguous"):
        catalog.stamp("stamp:key")
    for handle in ("../room", "room/1.png", "https://example.invalid/room.png"):
        with pytest.raises(ValueError):
            catalog.scene(handle)
        with pytest.raises(ValueError):
            catalog.stamp(handle)


def test_revision_recovery_rejects_corrupted_pinned_art(tmp_path: Path) -> None:
    directory = tmp_path / "stamps"
    directory.mkdir()
    Image.new("RGB", (10, 20), "red").save(directory / "key.png")
    asset = catalog_for(tmp_path).observe_image(str(directory / "key.png"))
    Image.new("RGB", (10, 20), "blue").save(asset.path)
    with pytest.raises(ValueError, match="recorded revision"):
        catalog_for(tmp_path).resolve_revision(asset.asset_id)


def test_region_geometry_is_validated_for_the_exact_asset(tmp_path: Path) -> None:
    directory = tmp_path / "references" / "scenes" / "door"
    directory.mkdir(parents=True)
    image = directory / "1.png"
    Image.new("RGB", (10, 20)).save(image)
    digest = sha256(image.read_bytes()).hexdigest()
    (directory / "scene.yaml").write_text(yaml.safe_dump({"canvas": {"artwork": {f"sha256:{digest}": {
        "lock": {"id": "lock", "bounds": {"x": 9900, "y": 0, "width": 200, "height": 100}}
    }}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="inside the image"):
        catalog_for(tmp_path).scene("door")
