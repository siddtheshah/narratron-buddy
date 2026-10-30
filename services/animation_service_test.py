"""Service behavior without an agent, canvas, or provider credentials."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from providers import VideoProviderError
from providers.video_provider import VideoGenerationResult
from services.animation_service import AnimationService


def test_explicit_technique_uses_only_required_provider(tmp_path: Path) -> None:
    video = MagicMock()
    video.generate.return_value = VideoGenerationResult(
        video_bytes=b"video",
        mime_type="video/mp4",
        provider="fake",
        model="fake",
    )
    planner = MagicMock()
    image = MagicMock()
    service = AnimationService(
        tmp_path,
        {"animation": {"forced_technique": "layered"}},
        video_provider=video,
        text_response_provider=planner,
        image_provider=image,
    )

    assert (
        service.create_animation("Rain over the harbor", "rain", technique="video")
        == "video"
    )
    assert (tmp_path / "animations/rain/video.json").is_file()
    video.generate.assert_called_once()
    planner.generate.assert_not_called()
    image.generate.assert_not_called()


def test_generation_failure_propagates_without_completion_manifest(
    tmp_path: Path,
) -> None:
    video = MagicMock()
    video.generate.side_effect = VideoProviderError("provider unavailable")
    service = AnimationService(tmp_path, {}, video_provider=video)

    with pytest.raises(VideoProviderError, match="provider unavailable"):
        service.create_animation("Rain over the harbor", "rain", technique="video")
    assert not (tmp_path / "animations/rain/video.json").exists()


@pytest.mark.parametrize("identifier", ["../outside", "a/b", "", "a\\b"])
def test_rejects_unsafe_identifier_before_provider_call(
    tmp_path: Path, identifier: str
) -> None:
    video = MagicMock()
    service = AnimationService(tmp_path, {}, video_provider=video)

    with pytest.raises(ValueError, match="safe directory name"):
        service.create_animation("Rain", identifier, technique="video")
    video.generate.assert_not_called()
