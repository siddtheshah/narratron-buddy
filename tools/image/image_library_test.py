import os
import shutil
import tempfile

from PIL import Image, PngImagePlugin

from components.theater_manager import TheaterManager
from tools.image.image_library import ImageLibrary


class TestImageLibrary:
    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.manager = TheaterManager(base_theaters_dir=self.temp_dir)
        self.theater = self.manager.theater("image-library")
        self.library = ImageLibrary(self.theater)
        os.makedirs(self.library.reference_dir, exist_ok=True)
        os.makedirs(self.library.output_dir, exist_ok=True)

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_find_image_names_returns_aliases_and_searches_metadata(self) -> None:
        path = os.path.join(self.library.reference_dir, "Candlelit Scribe.png")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Description", "A monk writing by candlelight")
        Image.new("RGB", (10, 10), color="gold").save(path, pnginfo=metadata)
        self.library._load_references()

        assert self.library.find_image_names("monk") == [{
            "name": "Candlelit Scribe",
            "alias": "Candlelit_Scribe",
            "path": path,
            "title": "",
            "description": "A monk writing by candlelight",
        }]

    def test_find_image_names_includes_generated_images(self) -> None:
        path = os.path.join(self.library.output_dir, "tower_scene.png")
        Image.new("RGB", (10, 10), color="navy").save(path)

        matches = self.library.find_image_names("tower")

        assert matches[0]["name"] == "tower_scene"
        assert matches[0]["alias"] == "tower_scene"

    def test_get_recent_images_orders_by_creation_time_descending(self) -> None:
        path1 = os.path.join(self.library.output_dir, "scene_one.png")
        path2 = os.path.join(self.library.output_dir, "scene_two.png")
        Image.new("RGB", (10, 10), color="red").save(path1)
        Image.new("RGB", (10, 10), color="blue").save(path2)
        os.utime(path1, (1000.0, 1000.0))
        os.utime(path2, (2000.0, 2000.0))

        recent = self.library.get_recent_images(limit=5, generated_only=True)
        assert len(recent) == 2
        assert recent[0]["name"] == "scene_two"
        assert recent[1]["name"] == "scene_one"
        assert recent[0]["created_at"] == 2000.0
        assert recent[1]["created_at"] == 1000.0

    def test_get_recent_images_deduplicates_webp_and_jpeg_stems(self) -> None:
        jpg_path = os.path.join(self.library.output_dir, "hero_portrait_1700000000.jpg")
        webp_path = os.path.join(self.library.output_dir, "hero_portrait_1700000000.webp")
        Image.new("RGB", (10, 10), color="green").save(jpg_path)
        Image.new("RGB", (10, 10), color="green").save(webp_path)
        os.utime(jpg_path, (1700000000.0, 1700000000.0))
        os.utime(webp_path, (1700000001.0, 1700000001.0))

        recent = self.library.get_recent_images(limit=5, generated_only=True)
        assert len(recent) == 1
        assert recent[0]["name"] == "hero_portrait_1700000000"
        assert recent[0]["path"] == jpg_path

    def test_get_recent_images_respects_limit(self) -> None:
        for i in range(5):
            p = os.path.join(self.library.output_dir, f"frame_{i}.png")
            Image.new("RGB", (10, 10), color="black").save(p)
            os.utime(p, (1000.0 + i, 1000.0 + i))

        recent = self.library.get_recent_images(limit=3, generated_only=True)
        assert len(recent) == 3
        assert [r["name"] for r in recent] == ["frame_4", "frame_3", "frame_2"]

    def test_get_recent_images_generated_only_filters_references(self) -> None:
        ref_path = os.path.join(self.library.reference_dir, "ref_art.png")
        gen_path = os.path.join(self.library.output_dir, "gen_art.png")
        Image.new("RGB", (10, 10), color="white").save(ref_path)
        Image.new("RGB", (10, 10), color="black").save(gen_path)
        os.utime(ref_path, (3000.0, 3000.0))
        os.utime(gen_path, (2000.0, 2000.0))

        gen_only = self.library.get_recent_images(limit=5, generated_only=True)
        assert len(gen_only) == 1
        assert gen_only[0]["name"] == "gen_art"

        both = self.library.get_recent_images(limit=5, generated_only=False)
        assert len(both) == 2
        assert both[0]["name"] == "ref_art"
        assert both[1]["name"] == "gen_art"

    def test_get_recent_images_rejects_invalid_limit(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="limit must be at least 1"):
            self.library.get_recent_images(limit=0)

