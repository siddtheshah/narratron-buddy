"""The provider boundary returns high-quality WebP for every generated raster."""

from io import BytesIO
from unittest.mock import patch

from PIL import Image, PngImagePlugin
import pytest

from providers.image_provider import (
    GENERATED_WEBP_QUALITY, ImageGenerationRequest, ImageGenerationResult,
    ImageProvider, ImageProviderError, generated_image_to_webp,
)


class RasterProvider(ImageProvider):
    id = "fixture"
    display_name = "Fixture"
    model = "fixture-model"

    def __init__(self, content: bytes) -> None:
        self.content = content

    def _generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        return ImageGenerationResult(self.content, "image/png", self.id, self.model,
                                     request_id="request", usage={"tokens": 12})


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_provider_returns_webp_with_dimensions_and_generation_details(format: str) -> None:
    source = BytesIO()
    Image.new("RGB", (37, 23), "teal").save(source, format)
    result = RasterProvider(source.getvalue()).generate(ImageGenerationRequest(prompt="Harbor"))
    assert result.mime_type == "image/webp"
    assert (result.provider, result.model, result.request_id, result.usage) == (
        "fixture", "fixture-model", "request", {"tokens": 12},
    )
    with Image.open(BytesIO(result.image_bytes)) as image:
        assert image.format == "WEBP"
        assert image.size == (37, 23)
    if format == "WEBP":
        assert result.image_bytes == source.getvalue()


@pytest.mark.parametrize("mode", ["RGBA", "P", "LA"])
def test_conversion_preserves_alpha_including_palette_transparency(mode: str) -> None:
    image = Image.new("RGBA", (3, 1))
    image.putalpha(Image.frombytes("L", (3, 1), bytes([0, 127, 255])))
    image = image.convert(mode)
    source = BytesIO()
    image.save(source, "PNG")
    with Image.open(BytesIO(source.getvalue())) as original:
        expected = original.convert("RGBA").getchannel("A").tobytes()
    with Image.open(BytesIO(generated_image_to_webp(source.getvalue()))) as converted:
        assert converted.getchannel("A").tobytes() == expected


def test_conversion_retains_exif_and_reference_png_text() -> None:
    image = Image.new("RGB", (32, 32))
    exif = image.getexif()
    exif[0x013B] = "Narratron"
    text = PngImagePlugin.PngInfo()
    text.add_text("Prompt", "Mountain shrine")
    text.add_text("Title", "天目山")
    text.add_text("MetadataDescription", "A shrine above the clouds")
    source = BytesIO()
    image.save(source, "PNG", exif=exif, pnginfo=text)
    with Image.open(BytesIO(generated_image_to_webp(source.getvalue()))) as converted:
        metadata = converted.getexif()
        assert metadata[0x013B] == "Narratron"
        assert metadata[0x010E] == "Mountain shrine"
        assert bytes(metadata[0x9C9B]).decode("utf-16le") == "天目山"
        assert metadata[0x9286] == "A shrine above the clouds"


def test_conversion_uses_high_quality_without_resizing() -> None:
    source = BytesIO()
    Image.new("RGB", (32, 32)).save(source, "PNG")
    original_save = Image.Image.save
    with patch.object(Image.Image, "save", autospec=True, side_effect=original_save) as save:
        generated_image_to_webp(source.getvalue())
    assert save.call_args.kwargs["quality"] == GENERATED_WEBP_QUALITY == 95
    assert save.call_args.kwargs["method"] == 6


@pytest.mark.parametrize("content", [b"", b"not an image", b"RIFF\x00\x00\x00\x00WEBP"])
def test_invalid_provider_images_raise_normalized_errors(content: bytes) -> None:
    with pytest.raises(ImageProviderError, match="encoded as WebP"):
        RasterProvider(content).generate(ImageGenerationRequest(prompt="Harbor"))
