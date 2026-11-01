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
from providers.image_provider import ImageGenerationResult, ImageReference
from providers.fal_qwen_layered_provider import LayeredImageResult
from providers.video_provider import VideoGenerationResult
from providers.animation_provider import AnimationProvider, AnimationTechnique
from providers.decision_provider import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionProvider,
    DecisionResult,
    PredicateAnswer,
    PredicateQuestion,
)
from providers.text_response_provider import TextResponseResult
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

def test_video_technique_with_references_generates_starting_image_via_image_provider(tmp_path: Path) -> None:
    from PIL import Image as PILImage
    import io
    from providers.image_provider import ImageReference, ImageGenerationResult

    img = PILImage.new("RGB", (32, 32), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_img_bytes = buf.getvalue()

    image_mock = MagicMock()
    image_mock.generate.return_value = ImageGenerationResult(
        image_bytes=raw_img_bytes,
        mime_type="image/png",
        provider="mock-image-prov",
        model="mock-image-model",
        request_id="img-req-1",
    )

    video_mock = MagicMock()
    video_mock.generate.return_value = VideoGenerationResult(
        video_bytes=b"fake-video-bytes",
        mime_type="video/mp4",
        provider="fal-minimax-h3-image-to-video",
        model="minimax/h3-max-turbo/image-to-video",
        request_id="vid-req-1",
    )
    video_mock.model = "minimax/h3-max-turbo/image-to-video"
    video_mock.is_image_to_video = True

    provider = AnimationProvider(
        {},
        image_provider=image_mock,
        video_provider=video_mock,
    )

    knight_ref = ImageReference(name="knight.png", data=b"knight-ref", mime_type="image/png")
    selected = provider.create_animation(
        "A brave knight stands before a dragon.",
        "knight_dragon",
        technique="video",
        output_dir=tmp_path,
        references=[knight_ref],
    )

    assert selected == "video"
    # Ensure image_provider was called with the references to create the scene image
    image_mock.generate.assert_called_once()
    img_req = image_mock.generate.call_args[0][0]
    assert img_req.references == [knight_ref]
    assert "knight stands before a dragon" in img_req.prompt

    # Ensure video_provider received the generated scene image as start image
    video_mock.generate.assert_called_once()
    vid_req = video_mock.generate.call_args[0][0]
    assert vid_req.image_bytes == raw_img_bytes

    # Check manifest and start_frame file
    anim_dir = tmp_path / "animations/knight_dragon"
    assert (anim_dir / "start_frame.webp").is_file()
    assert (anim_dir / "video.mp4").is_file()
    manifest = json.loads((anim_dir / "video.json").read_text(encoding="utf-8"))
    assert manifest["start_image"] == "animations/knight_dragon/start_frame.webp"
    assert manifest["start_frame"] == "animations/knight_dragon/start_frame.webp"
    assert manifest["provider"]["start_image_provider"] == "mock-image-prov"
    assert manifest["provider"]["start_image_model"] == "mock-image-model"


def test_video_technique_with_image_to_video_provider_generates_starting_image_without_references(tmp_path: Path) -> None:
    from PIL import Image as PILImage
    import io
    from providers.image_provider import ImageGenerationResult, ImageReference

    img = PILImage.new("RGB", (32, 32), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_img_bytes = buf.getvalue()

    image_mock = MagicMock()
    image_mock.generate.return_value = ImageGenerationResult(
        image_bytes=raw_img_bytes,
        mime_type="image/png",
        provider="mock-image-prov",
        model="mock-image-model",
    )

    video_mock = MagicMock()
    video_mock.generate.return_value = VideoGenerationResult(
        video_bytes=b"fake-video-bytes",
        mime_type="video/mp4",
        provider="fal-minimax-h3-image-to-video",
        model="minimax/h3-max-turbo/image-to-video",
    )
    video_mock.model = "minimax/h3-max-turbo/image-to-video"
    video_mock.is_image_to_video = True

    provider = AnimationProvider(
        {},
        image_provider=image_mock,
        video_provider=video_mock,
    )

    selected = provider.create_animation(
        "A peaceful lake at dusk.",
        "lake_dusk",
        technique="video",
        output_dir=tmp_path,
        references=[],
    )

    assert selected == "video"
    image_mock.generate.assert_called_once()
    img_req = image_mock.generate.call_args[0][0]
    assert img_req.references == []
    assert "peaceful lake at dusk" in img_req.prompt
    video_mock.generate.assert_called_once()
    vid_req = video_mock.generate.call_args[0][0]
    assert vid_req.image_bytes == raw_img_bytes


def test_registry_resolves_image_to_video_provider() -> None:
    from providers.registry import get_video_provider, list_video_provider_specs

    provider = get_video_provider("fal-minimax-h3-image-to-video", options={"api_key": "test-key", "model": "minimax/h3-max-turbo/image-to-video"})
    assert provider.id == "fal-minimax-h3-image-to-video"
    assert provider.model == "minimax/h3-max-turbo/image-to-video"
    assert provider.is_image_to_video is True

    specs = list_video_provider_specs()
    spec_ids = [s["id"] for s in specs]
    assert "fal-minimax-h3-turbo" in spec_ids
    assert "fal-minimax-h3-image-to-video" in spec_ids


def test_plan_technique_with_decision_provider() -> None:
    decision_provider = MagicMock(spec=DecisionProvider)
    decision_provider.decide.return_value = DecisionResult(
        provider="fake-decisions",
        model="fake-decision-model",
        answers={
            "technique": ChoiceAnswer(name="technique", choice="layered"),
            "subject_effect": ChoiceAnswer(name="subject_effect", choice="sway"),
            "needs_foreground_layer": PredicateAnswer(
                name="needs_foreground_layer", probability=0.85
            ),
            "foreground_effect": ChoiceAnswer(
                name="foreground_effect", choice="ghostly"
            ),
        },
        request_id="req-dec-123",
        usage={"input_tokens": 50},
    )

    technique, metadata = AnimationProvider.plan_technique_with_decision_provider(
        decision_provider, "An ancient lantern hanging from a branch"
    )

    assert technique == "layered"
    assert metadata["provider"] == "fake-decisions"
    assert metadata["model"] == "fake-decision-model"
    assert metadata["subject_effect"] == "sway"
    assert metadata["needs_foreground_layer"] is True
    assert metadata["foreground_effect"] == "ghostly"

    decision_provider.decide.assert_called_once()
    req = decision_provider.decide.call_args[0][0]
    assert req.input_text == "An ancient lantern hanging from a branch"
    assert len(req.questions) == 4
    names = [q.name for q in req.questions]
    assert names == [
        "technique",
        "subject_effect",
        "needs_foreground_layer",
        "foreground_effect",
    ]

    # Verify questions match animation-technique-planner scenario specs
    tech_q: ChoiceQuestion = req.questions[0]
    assert tech_q.choices == ("triframe", "layered", "video")

    subj_q: ChoiceQuestion = req.questions[1]
    assert subj_q.choices == (
        "none",
        "sway",
        "vibrate",
        "pulse",
        "gentle_rocking",
        "light_halo",
        "ghostly",
        "energy_blast",
    )

    fg_pred_q: PredicateQuestion = req.questions[2]
    assert fg_pred_q.name == "needs_foreground_layer"

    fg_q: ChoiceQuestion = req.questions[3]
    assert fg_q.choices == ("none", "sway", "pulse", "ghostly", "light_halo", "mirage")


def test_create_animation_with_decision_provider(tmp_path: Path) -> None:
    decision_provider = MagicMock(spec=DecisionProvider)
    decision_provider.decide.return_value = DecisionResult(
        provider="fake-decisions",
        model="fake-decision-model",
        answers={
            "technique": ChoiceAnswer(name="technique", choice="video"),
            "subject_effect": ChoiceAnswer(name="subject_effect", choice="none"),
            "needs_foreground_layer": PredicateAnswer(
                name="needs_foreground_layer", probability=0.1
            ),
            "foreground_effect": ChoiceAnswer(
                name="foreground_effect", choice="none"
            ),
        },
    )
    video = MagicMock()
    video.generate.return_value = VideoGenerationResult(
        video_bytes=b"video",
        mime_type="video/mp4",
        provider="fake",
        model="fake",
    )
    planner = MagicMock()

    provider = AnimationProvider(
        {},
        decision_provider=decision_provider,
        video_provider=video,
        text_response_provider=planner,
    )

    selected = provider.create_animation(
        "A soaring falcon over canyon walls", "falcon_anim", output_dir=tmp_path
    )

    assert selected == "video"
    decision_provider.decide.assert_called_once()
    planner.generate.assert_not_called()
    video.generate.assert_called_once()
    assert (tmp_path / "animations/falcon_anim/video.json").is_file()


def test_create_animation_without_decision_provider_fallback(tmp_path: Path) -> None:
    video = MagicMock()
    video.generate.return_value = VideoGenerationResult(
        video_bytes=b"video",
        mime_type="video/mp4",
        provider="fake",
        model="fake",
    )
    planner = MagicMock()
    planner.generate.return_value = TextResponseResult(
        text='{"technique": "video", "reasoning": "High speed motion"}',
        parsed={"technique": "video", "reasoning": "High speed motion"},
        provider="fake-llm",
        model="fake-model",
    )

    provider = AnimationProvider(
        {},
        decision_provider=None,
        video_provider=video,
        text_response_provider=planner,
    )

    selected = provider.create_animation(
        "A galloping horse", "horse_anim", output_dir=tmp_path
    )

    assert selected == "video"
    planner.generate.assert_called_once()
    video.generate.assert_called_once()
    assert (tmp_path / "animations/horse_anim/video.json").is_file()


def test_registry_animation_provider_wires_decision_provider() -> None:
    mock_decision = MagicMock(spec=DecisionProvider)
    provider_with_decision = get_animation_provider(
        "default", decision_provider=mock_decision
    )
    assert provider_with_decision.decision_provider is mock_decision

    provider_without_decision = get_animation_provider("default")
    assert provider_without_decision.decision_provider is None


def test_video_technique_decision_provider_checks_base_image_necessary_and_reuses_reference(
    tmp_path: Path,
) -> None:
    raw_bytes = png_bytes()
    ref = ImageReference(
        name="harbor.png",
        data=raw_bytes,
        mime_type="image/png",
        description="A peaceful harbor with docked wooden boats at sunset",
    )

    mock_decision = MagicMock(spec=DecisionProvider)
    mock_decision.decide.return_value = DecisionResult(
        provider="test-decisions",
        model="test-model",
        answers={
            "new_base_image_necessary": PredicateAnswer(
                name="new_base_image_necessary", probability=0.1
            ),
        },
    )

    mock_image = MagicMock()
    mock_video = MagicMock()
    mock_video.generate.return_value = VideoGenerationResult(
        video_bytes=b"fake-video-bytes",
        mime_type="video/mp4",
        provider="mock-video-prov",
        model="mock-video-model",
        request_id="vid-req-reuse",
    )

    provider = AnimationProvider(
        {},
        image_provider=mock_image,
        video_provider=mock_video,
        decision_provider=mock_decision,
    )

    selected = provider.create_animation(
        "Docked boats gently swaying in harbor sunset.",
        "harbor_anim",
        technique="video",
        output_dir=tmp_path,
        references=[ref],
    )

    assert selected == "video"
    mock_decision.decide.assert_called_once()
    decision_req = mock_decision.decide.call_args[0][0]
    assert "Docked boats gently swaying in harbor sunset" in decision_req.input_text
    assert "A peaceful harbor with docked wooden boats at sunset" in decision_req.input_text
    assert decision_req.questions[0].name == "new_base_image_necessary"

    # Image provider generate should NOT be called because new base image is unnecessary
    mock_image.generate.assert_not_called()

    # Video provider received the base reference bytes directly
    mock_video.generate.assert_called_once()
    vid_req = mock_video.generate.call_args[0][0]
    assert vid_req.image_bytes == raw_bytes
    assert vid_req.image_mime_type == "image/png"

    anim_dir = tmp_path / "animations/harbor_anim"
    assert (anim_dir / "start_frame.webp").is_file()
    assert (anim_dir / "video.mp4").is_file()

    manifest = json.loads((anim_dir / "video.json").read_text(encoding="utf-8"))
    assert manifest["start_image"] == "animations/harbor_anim/start_frame.webp"
    assert manifest["start_frame"] == "animations/harbor_anim/start_frame.webp"
    assert manifest["provider"]["start_image_reference"] == "harbor.png"


def test_video_technique_decision_provider_generates_new_image_when_necessary(
    tmp_path: Path,
) -> None:
    ref_bytes = png_bytes()
    new_img_bytes = png_bytes()

    ref = ImageReference(
        name="hero.png",
        data=ref_bytes,
        mime_type="image/png",
        description="Close-up portrait of knight",
    )

    mock_decision = MagicMock(spec=DecisionProvider)
    mock_decision.decide.return_value = DecisionResult(
        provider="test-decisions",
        model="test-model",
        answers={
            "new_base_image_necessary": PredicateAnswer(
                name="new_base_image_necessary", probability=0.9
            ),
        },
    )

    mock_image = MagicMock()
    mock_image.generate.return_value = ImageGenerationResult(
        image_bytes=new_img_bytes,
        mime_type="image/png",
        provider="mock-image-prov",
        model="mock-image-model",
        request_id="img-req-gen",
    )

    mock_video = MagicMock()
    mock_video.generate.return_value = VideoGenerationResult(
        video_bytes=b"fake-video-bytes",
        mime_type="video/mp4",
        provider="mock-video-prov",
        model="mock-video-model",
        request_id="vid-req-gen",
    )

    provider = AnimationProvider(
        {},
        image_provider=mock_image,
        video_provider=mock_video,
        decision_provider=mock_decision,
    )

    selected = provider.create_animation(
        "A knight charges through a fiery battlefield.",
        "knight_battle",
        technique="video",
        output_dir=tmp_path,
        references=[ref],
    )

    assert selected == "video"
    mock_decision.decide.assert_called_once()
    mock_image.generate.assert_called_once()
    mock_video.generate.assert_called_once()
    vid_req = mock_video.generate.call_args[0][0]
    assert vid_req.image_bytes == new_img_bytes


def test_check_new_base_image_necessary_helper() -> None:
    mock_decision = MagicMock(spec=DecisionProvider)
    mock_decision.decide.return_value = DecisionResult(
        provider="test-decisions",
        model="test-model",
        answers={
            "new_base_image_necessary": PredicateAnswer(
                name="new_base_image_necessary", probability=0.2
            ),
        },
    )

    result = AnimationProvider.check_new_base_image_necessary(
        mock_decision,
        scene_prompt="River bank at dawn",
        base_reference_description="Misty river bank with morning light",
    )
    assert result is False

    mock_decision.decide.return_value = DecisionResult(
        provider="test-decisions",
        model="test-model",
        answers={
            "new_base_image_necessary": PredicateAnswer(
                name="new_base_image_necessary", probability=0.8
            ),
        },
    )
    result_needed = AnimationProvider.check_new_base_image_necessary(
        mock_decision,
        scene_prompt="Spaceship battle in deep space",
        base_reference_description="Misty river bank with morning light",
    )
    assert result_needed is True

