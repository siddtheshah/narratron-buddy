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
