"""Provider behavior without an agent, canvas, or provider credentials."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import MagicMock, patch

import pytest

from providers import (
    VideoProviderError,
    get_animation_provider,
    list_animation_provider_specs,
)
from providers.image_provider import ImageGenerationResult
from providers.fal_qwen_layered_provider import LayeredImageResult
from providers.video_provider import VideoGenerationResult
from providers.animation_provider import AnimationProvider, AnimationTechnique
from testing.reference_images import png_bytes


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
    provider = AnimationProvider(
        {"animation": {"forced_technique": "layered"}},
        video_provider=video,
        text_response_provider=planner,
        image_provider=image,
    )

    assert (
        provider.create_animation(
            "Rain over the harbor", "rain", technique="video", output_dir=tmp_path
        )
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
    provider = AnimationProvider({}, video_provider=video)

    with pytest.raises(VideoProviderError, match="provider unavailable"):
        provider.create_animation(
            "Rain over the harbor", "rain", technique="video", output_dir=tmp_path
        )
    assert not (tmp_path / "animations/rain/video.json").exists()


@pytest.mark.parametrize("identifier", ["../outside", "a/b", "", "a\\b"])
def test_rejects_unsafe_identifier_before_provider_call(
    tmp_path: Path, identifier: str
) -> None:
    video = MagicMock()
    provider = AnimationProvider({}, video_provider=video)

    with pytest.raises(ValueError, match="safe directory name"):
        provider.create_animation(
            "Rain", identifier, technique="video", output_dir=tmp_path
        )
    video.generate.assert_not_called()


@pytest.mark.parametrize("technique", ["video", "layered", "triframe"])
def test_registry_provider_reuses_instance_across_concurrent_output_directories(
    tmp_path: Path,
    technique: AnimationTechnique,
) -> None:
    image_bytes = png_bytes()
    image = MagicMock()
    image.generate.return_value = ImageGenerationResult(
        image_bytes=image_bytes,
        mime_type="image/png",
        provider="fake",
        model="fake",
    )
    video = MagicMock()
    video.generate.return_value = VideoGenerationResult(
        video_bytes=b"video",
        mime_type="video/mp4",
        provider="fake",
        model="fake",
    )
    layered = MagicMock()
    layered.model = "fake"
    layered.decompose.return_value = LayeredImageResult(
        images=[(image_bytes, "image/png"), (image_bytes, "image/png")],
    )
    provider = get_animation_provider(
        options={"visuals": {"style": "Watercolor"}},
        image_provider=image,
        video_provider=video,
        layered_provider=layered,
        text_response_provider=MagicMock(),
    )
    outputs = [tmp_path / "first", tmp_path / "second"]
    layers = [
        {"name": "background", "description": "Harbor", "effect": "none"},
        {"name": "subject", "description": "Rain", "effect": "sway"},
    ]
    plan = {
        "base_frame": "Rain at the harbor",
        "second_frame_change": "Rain blows left",
        "third_frame_change": "Rain falls straight",
    }

    def generate(output_dir: Path) -> AnimationTechnique:
        return provider.create_animation(
            "Rain", "rain", output_dir=output_dir, technique=technique
        )

    with (
        patch.object(provider, "plan_layers_with_provider", return_value=(layers, {})),
        patch.object(provider, "plan_triframe_with_provider", return_value=(plan, {})),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        assert list(executor.map(generate, outputs)) == [technique, technique]

    for output in outputs:
        folder = output / "animations/rain"
        manifest = json.loads(
            (folder / f"{technique}.json").read_text(encoding="utf-8")
        )
        assert manifest["scene_prompt"] == "Rain"
        paths: list[str]
        if technique == "video":
            paths = [manifest["video_path"]]
        elif technique == "layered":
            paths = [
                manifest["base_image"],
                *[layer["path"] for layer in manifest["layers"]],
            ]
        else:
            paths = manifest["frames"]
        assert paths
        for path in paths:
            assert not Path(path).is_absolute()
            assert (output / path).is_file()
    assert list_animation_provider_specs()[0]["id"] == provider.id


def test_registry_rejects_unknown_animation_provider() -> None:
    with pytest.raises(ValueError, match="Unknown animation provider"):
        get_animation_provider("missing")
