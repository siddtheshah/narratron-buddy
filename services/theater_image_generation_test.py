"""Package references and stamps receive distinct background and composition settings."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from providers.image_provider import ImageReference
from services.theater_image_generation import generate_theater_image


def test_stamp_uses_flare_transparency_and_cutout_instructions(tmp_path: Path) -> None:
    (tmp_path / "theater.yaml").write_text("visuals:\n  model: hybrid-flux-gemini\n  style: Painted fantasy\n", encoding="utf-8")
    provider = MagicMock()
    with patch("services.theater_image_generation.get_image_provider", return_value=provider) as resolve:
        generate_theater_image(tmp_path, kind="stamp", prompt="Goblin scout", references=[])
    resolve.assert_called_once_with("openai-gpt-image-flare")
    request = provider.generate.call_args.args[0]
    assert request.background == "transparent"
    assert request.aspect_ratio == "1:1"
    assert "Goblin scout" in request.prompt
    assert "Style: Painted fantasy" in request.prompt
    assert "real alpha transparency" in request.prompt
    assert "do not crop" in request.prompt
    assert "checkerboard" in request.prompt


def test_reference_uses_flare_scene_prompt_and_selected_references(tmp_path: Path) -> None:
    (tmp_path / "theater.yaml").write_text("visuals:\n  style: Painted fantasy\n", encoding="utf-8")
    references = [ImageReference(name="hero.png", data=b"hero", mime_type="image/png")]
    provider = MagicMock()
    with patch("services.theater_image_generation.get_image_provider", return_value=provider) as resolve:
        generate_theater_image(tmp_path, kind="reference", prompt="A misty harbor", references=references)
    resolve.assert_called_once_with("openai-gpt-image-flare")
    request = provider.generate.call_args.args[0]
    assert request.background == "opaque"
    assert request.aspect_ratio == "16:9"
    assert request.references == references
    assert request.prompt == "A misty harbor\nStyle: Painted fantasy"
