"""WebP display conversion remains compatible with uploaded raster formats."""

from pathlib import Path

from PIL import Image
import pytest

from utils.image_utils import compress_image_to_webp


@pytest.mark.parametrize("format, extension", [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp"), ("GIF", "gif")])
def test_display_conversion_accepts_uploaded_formats(tmp_path: Path, format: str, extension: str) -> None:
    source = tmp_path / f"upload.{extension}"
    Image.new("RGB", (23, 17), "blue").save(source, format)
    original = source.read_bytes()
    result = compress_image_to_webp(source)
    assert result is not None
    assert source.read_bytes() == original
    with Image.open(result) as image:
        assert image.format == "WEBP"
        assert image.size == (23, 17)


@pytest.mark.parametrize("from_file", [True, False])
def test_palette_upload_retains_transparency_in_webp_display(tmp_path: Path, from_file: bool) -> None:
    image = Image.new("P", (2, 1))
    image.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    image.putdata([0, 1])
    image.info["transparency"] = 0
    source = tmp_path / "palette.png"
    image.save(source)
    result = compress_image_to_webp(source if from_file else image, output_path=tmp_path / "display.webp")
    assert result is not None
    with Image.open(result) as converted:
        assert converted.getchannel("A").tobytes() == bytes([0, 255])
