from providers.fal_qwen_layered_provider import FalQwenLayeredProvider, LayeredImageRequest


def test_qwen_layered_posts_grounding_prompt_and_data_uri_then_downloads_layers():
    calls = []

    def request_json(endpoint, payload):
        calls.append((endpoint, payload))
        return {"request_id": "qwen-request", "seed": 7, "images": [{"url": "https://background"}, {"url": "https://layer/1"}, {"url": "https://layer/2"}]}

    provider = FalQwenLayeredProvider(api_key="test", request_json=request_json, download=lambda url: (url.encode(), "image/png"))
    result = provider.decompose(LayeredImageRequest(b"image", "image/jpeg", "background; subject", 3))

    assert calls[0][0] == "fal-ai/qwen-image-layered"
    assert calls[0][1]["image_url"].startswith("data:image/jpeg;base64,")
    assert calls[0][1]["prompt"] == "background; subject"
    assert calls[0][1]["num_layers"] == 3
    assert calls[0][1]["num_inference_steps"] == 14
    assert calls[0][1]["negative_prompt"] == "unclear boundaries, incomplete extractions"
    assert result.request_id == "qwen-request"
    assert len(result.images) == 3
    assert result.images[0][0] == b"https://background"
