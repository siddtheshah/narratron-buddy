from providers.fal_flux_klein_provider import FalFluxKleinProvider
from providers.image_provider import ImageGenerationRequest, ImageReference


def test_fal_klein_uses_text_endpoint_and_downloads_result():
    calls = []
    provider = FalFluxKleinProvider(
        api_key="test-key",
        request_json=lambda endpoint, payload: calls.append((endpoint, payload)) or {"images": [{"url": "https://example.test/image.png"}], "seed": 42},
        download=lambda url: (b"generated", "image/png"),
    )

    result = provider.generate(ImageGenerationRequest(prompt="a small green house", width=1024, height=768))

    assert calls == [
        (
            "fal-ai/flux-2/klein/9b",
            {"prompt": "a small green house", "num_images": 1, "output_format": "png", "image_size": {"width": 1024, "height": 768}},
        )
    ]
    assert result.image_bytes == b"generated"
    assert result.model == "fal-ai/flux-2/klein/9b"
    assert result.usage == {"seed": 42}


def test_fal_klein_uses_edit_endpoint_and_data_uri_for_references():
    calls = []
    provider = FalFluxKleinProvider(
        api_key="test-key",
        request_json=lambda endpoint, payload: calls.append((endpoint, payload)) or {"images": [{"url": "https://example.test/image.png"}]},
        download=lambda url: (b"generated", "image/png"),
    )

    provider.generate(
        ImageGenerationRequest(
            prompt="make it a watercolor",
            references=[ImageReference(name="source.png", data=b"abc", mime_type="image/png")],
        )
    )

    assert calls[0][0] == "fal-ai/flux-2/klein/9b/edit"
    assert calls[0][1]["image_urls"] == ["data:image/png;base64,YWJj"]
    assert calls[0][1]["image_size"] == "landscape_16_9"
