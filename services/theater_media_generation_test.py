"""Configured provider selection and portable generated media files."""

import json
from io import BytesIO
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from providers.music_provider import MusicGenerationResult
from providers.video_provider import VideoGenerationResult
from services.theater_media_generation import generate_theater_media
from services.theater_assets import animations
from components.theater_manager import TheaterManager
from providers.image_provider import ImageGenerationResult
from providers.fal_qwen_layered_provider import LayeredImageResult


def test_music_uses_configured_provider_options_and_preserves_audio_format(tmp_path: Path) -> None:
    (tmp_path / "theater.yaml").write_text("{}", encoding="utf-8")
    provider = MagicMock()
    provider.generate.return_value = MusicGenerationResult(
        audio_bytes=b"wave audio", mime_type="audio/wav", provider="mock", model="mock",
    )
    settings = {"music": {"provider": "custom-music", "provider_options": {"model": "custom-model"}, "style": "Orchestral"}}
    with patch("services.theater_media_generation.apply_app_config", return_value=settings), patch(
        "services.theater_media_generation.get_music_provider", return_value=provider,
    ) as resolve:
        files = generate_theater_media(tmp_path, kind="music", prompt="Rain", identifier="rain")
    resolve.assert_called_once_with("custom-music", {"model": "custom-model"})
    assert provider.generate.call_args.args[0].prompt == "Rain\nStyle: Orchestral"
    assert files == {"output/music/rain.wav": b"wave audio"}


def test_animation_uses_configured_video_provider_duration_and_visual_style(tmp_path: Path) -> None:
    (tmp_path / "theater.yaml").write_text("{}", encoding="utf-8")
    provider = MagicMock()
    provider.generate.return_value = VideoGenerationResult(
        video_bytes=b"video", mime_type="video/mp4", provider="mock", model="mock",
    )
    settings = {"animation": {"video_provider": "custom-video", "video_duration_seconds": 8}, "visuals": {"style": "Watercolor"}}
    with patch("services.theater_media_generation.apply_app_config", return_value=settings), patch(
        "providers.registry.get_video_provider", return_value=provider,
    ) as resolve:
        files = generate_theater_media(tmp_path, kind="animation", prompt="Rain", identifier="rain")
    resolve.assert_called_once_with("custom-video")
    request = provider.generate.call_args.args[0]
    assert request.video_duration_seconds == 8
    assert "Style: Watercolor" in request.prompt
    assert "loopable" in request.prompt
    assert files["animations/rain/video.mp4"] == b"video"
    manifest = json.loads(files["animations/rain/video.json"])
    assert manifest["video_path"] == "animations/rain/video.mp4"
    assert manifest["loop"] is True


@pytest.mark.parametrize("technique", ["layered", "triframe"])
def test_image_animation_presets_produce_playable_catalog_assets(tmp_path: Path, technique: Literal["layered", "triframe"]) -> None:
    theater = TheaterManager(tmp_path).theater("presets")
    root = theater.directory()
    root.mkdir(parents=True, exist_ok=True)
    (root / "theater.yaml").write_text("{}", encoding="utf-8")
    buffer = BytesIO()
    Image.new("RGBA", (8, 8), (0, 255, 0, 100)).save(buffer, "PNG")
    image = buffer.getvalue()
    provider = MagicMock()
    provider.generate.return_value = ImageGenerationResult(image_bytes=image, mime_type="image/png", provider="mock", model="mock")
    layered = MagicMock()
    layered.model = "mock-layered"
    layered.decompose.return_value = LayeredImageResult(images=[(image, "image/png"), (image, "image/png")])
    layers = [{"name": "background", "description": "Forest", "effect": "none"},
              {"name": "subject", "description": "Tree", "effect": "sway"}]
    plan = {"base_frame": "Forest", "second_frame_change": "Leaves sway left", "third_frame_change": "Leaves sway back"}
    settings = {"animation": {"text_provider": "planner", "text_model": "planner-model"},
                "visuals": {"model": "image", "style": "Watercolor"}}
    with patch("services.theater_media_generation.apply_app_config", return_value=settings), \
         patch("providers.registry.get_image_provider", return_value=provider), \
         patch("providers.registry.get_text_response_provider") as planner, \
         patch("providers.animation_provider.FalQwenLayeredProvider", return_value=layered), \
         patch("providers.animation_provider.AnimationProvider.plan_layers_with_provider", return_value=(layers, {})), \
         patch("providers.animation_provider.AnimationProvider.plan_triframe_with_provider", return_value=(plan, {})):
        files = generate_theater_media(root, kind="animation", prompt="Forest", identifier="forest", animation_type=technique)
    planner.assert_called_once_with("planner", {"model": "planner-model"})
    for filename, content in files.items():
        target = root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    assets = animations(theater)
    assert len(assets) == 1
    assert assets[0].type == technique
    assert assets[0].description == "Forest"
    if technique == "triframe":
        assert len(assets[0].frames) == 3
        assert provider.generate.call_count == 3
        assert provider.generate.call_args_list[1].args[0].references[0].mime_type == "image/png"
        assert "Style: Watercolor" in provider.generate.call_args.args[0].prompt
    else:
        assert assets[0].manifest["layers"][1]["effect"] == "sway"
        with Image.open(root / "animations/forest/layer_2.webp") as saved:
            assert saved.getpixel((0, 0))[3] == 100
