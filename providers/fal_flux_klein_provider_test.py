from testing.reference_images import png_bytes

from providers.fal_flux_klein_provider import FalFluxKleinProvider
from providers.image_provider import ImageGenerationRequest, ImageReference


def test_fal_klein_uses_text_endpoint_and_downloads_result() -> None:
    calls = []
    provider = FalFluxKleinProvider(
        api_key="test-key",
        request_json=lambda endpoint, payload: calls.append((endpoint, payload)) or {"images": [{"url": "https://example.test/image.png"}], "seed": 42},
        download=lambda url: (png_bytes(), "image/png"),
    )

    result = provider.generate(ImageGenerationRequest(prompt="a small green house", width=1024, height=768))

    assert calls == [
        (
            "blackforestlabs/flux-3/klein/text-to-image",
            {"prompt": "a small green house", "num_images": 1, "output_format": "png", "image_size": {"width": 1024, "height": 768}},
        )
    ]
    assert result.image_bytes[:4] == b"RIFF"
    assert result.mime_type == "image/webp"
    assert result.model == "blackforestlabs/flux-3/klein/text-to-image"
    assert result.usage == {"seed": 42}


def test_fal_klein_uses_edit_endpoint_and_data_uri_for_references() -> None:
    calls = []
    provider = FalFluxKleinProvider(
        api_key="test-key",
        request_json=lambda endpoint, payload: calls.append((endpoint, payload)) or {"images": [{"url": "https://example.test/image.png"}]},
        download=lambda url: (png_bytes(), "image/png"),
    )

    provider.generate(
        ImageGenerationRequest(
            prompt="make it a watercolor",
            references=[ImageReference(name="source.png", data=b"abc", mime_type="image/png")],
        )
    )

    assert calls[0][0] == "blackforestlabs/flux-3/klein/edit-image"
    assert calls[0][1]["image_urls"] == ["data:image/png;base64,YWJj"]
    assert calls[0][1]["image_size"] == "landscape_16_9"
