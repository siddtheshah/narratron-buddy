"""Tests for storage/stamp_storage.py."""

import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from absl.testing import flagsaver

from storage.stamp_storage import (
    StampStorage,
    get_stamps_root,
    process_stamp_image,
)


class TestStampStorage(unittest.TestCase):
    """Unit tests for stamp image processing and filesystem storage."""

    def _create_test_image_bytes(
        self, width: int = 100, height: int = 100, fmt: str = "PNG"
    ) -> bytes:
        mode = "RGB" if fmt == "JPEG" else "RGBA"
        color = (255, 0, 0) if fmt == "JPEG" else (255, 0, 0, 255)
        img = Image.new(mode, (width, height), color=color)
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_process_stamp_image_normal(self) -> None:
        raw_bytes = self._create_test_image_bytes(50, 50, "PNG")
        processed, ctype = process_stamp_image(raw_bytes)
        self.assertEqual(ctype, "image/png")
        img = Image.open(io.BytesIO(processed))
        self.assertEqual(img.size, (50, 50))

    def test_process_stamp_image_resizes_large_image(self) -> None:
        raw_bytes = self._create_test_image_bytes(600, 400, "PNG")
        processed, ctype = process_stamp_image(raw_bytes, max_dimension=256)
        self.assertEqual(ctype, "image/png")
        img = Image.open(io.BytesIO(processed))
        width, height = img.size
        self.assertLessEqual(width, 256)
        self.assertLessEqual(height, 256)
        self.assertEqual(width, 256)

    def test_process_stamp_image_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            process_stamp_image(b"")

    def test_process_stamp_image_rejects_invalid_data(self) -> None:
        with self.assertRaises(ValueError):
            process_stamp_image(b"not an image at all")

    def test_process_stamp_image_formats(self) -> None:
        jpeg_bytes = self._create_test_image_bytes(80, 80, "JPEG")
        processed_jpeg, ctype_jpeg = process_stamp_image(jpeg_bytes)
        self.assertEqual(ctype_jpeg, "image/jpeg")

        webp_bytes = self._create_test_image_bytes(80, 80, "WEBP")
        processed_webp, ctype_webp = process_stamp_image(webp_bytes)
        self.assertEqual(ctype_webp, "image/webp")

    def test_process_stamp_image_preserves_transparency(self) -> None:
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        img.putpixel((50, 50), (255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        processed, ctype = process_stamp_image(buf.getvalue())
        self.assertEqual(ctype, "image/png")
        result_img = Image.open(io.BytesIO(processed))
        self.assertEqual(result_img.mode, "RGBA")
        self.assertEqual(result_img.getpixel((0, 0))[3], 0)
        self.assertEqual(result_img.getpixel((50, 50))[3], 128)

    def test_save_read_and_delete_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = StampStorage(base_dir=tmp_dir)
            test_bytes = self._create_test_image_bytes(64, 64, "PNG")

            filename = storage.save_stamp(
                user_id=42,
                filename="my_cat.png",
                image_bytes=test_bytes,
                content_type="image/png",
            )
            self.assertTrue(filename.endswith("my_cat.png"))

            # Verify file exists on disk
            disk_path = Path(tmp_dir) / "42" / filename
            self.assertTrue(disk_path.is_file())

            # Read stamp
            read_bytes = storage.read_stamp(user_id=42, filename=filename)
            self.assertIsNotNone(read_bytes)
            if read_bytes is not None:
                self.assertEqual(read_bytes, test_bytes)

            # Delete stamp
            deleted = storage.delete_stamp(user_id=42, filename=filename)
            self.assertTrue(deleted)
            self.assertFalse(disk_path.is_file())

            # Read after delete returns None
            self.assertIsNone(storage.read_stamp(user_id=42, filename=filename))

    @flagsaver.flagsaver(testing_use_local=True)
    def test_get_stamps_root_local(self) -> None:
        root = get_stamps_root()
        self.assertTrue(str(root).endswith("stamps"))

    @flagsaver.flagsaver(testing_use_local=False)
    def test_get_stamps_root_production(self) -> None:
        root = get_stamps_root()
        self.assertEqual(root, Path("/mnt/storage/stamps"))


if __name__ == "__main__":
    unittest.main()
