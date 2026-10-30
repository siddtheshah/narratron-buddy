"""Configured provider selection and portable generated media files."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from providers.music_provider import MusicGenerationResult
from providers.video_provider import VideoGenerationResult
from services.theater_media_generation import generate_theater_media


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
        "services.theater_media_generation.get_video_provider", return_value=provider,
    ) as resolve:
        files = generate_theater_media(tmp_path, kind="animation", prompt="Rain", identifier="rain")
    resolve.assert_called_once_with("custom-video")
    request = provider.generate.call_args.args[0]
    assert request.video_duration_seconds == 8
    assert "Style: Watercolor" in request.prompt
    assert "looping" in request.prompt
    assert files["output/animations/rain/video.mp4"] == b"video"
    manifest = json.loads(files["output/animations/rain/video.json"])
    assert manifest["video_path"] == "video.mp4"
    assert manifest["loop"] is True
