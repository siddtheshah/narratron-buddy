"""Quality-focused asset generation, separate from live canvas image routing."""

from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter
import yaml

from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageReference
from providers.registry import get_image_provider


def generate_theater_image(
    root: Path, *, kind: Literal["reference", "stamp", "character"], prompt: str,
    references: list[ImageReference],
) -> ImageGenerationResult:
    settings = TypeAdapter(dict[str, JsonValue]).validate_python(
        yaml.safe_load((root / "theater.yaml").read_text(encoding="utf-8")) or {}
    )
    visuals = TypeAdapter(dict[str, JsonValue]).validate_python(settings.get("visuals") or {})
    style = str(visuals.get("style") or "").strip()
    if style:
        prompt += f"\nStyle: {style}"
    if kind == "stamp":
        prompt += (
            "\nCreate a single movable canvas stamp / tabletop token. "
            "Isolate the subject on a fully transparent background with real alpha transparency. "
            "Center the entire subject within a square frame, leaving a small clear margin; "
            "do not crop limbs or accessories. No scenery, ground plane, border, lettering, "
            "drop shadow, solid background, or checkerboard pattern."
        )
        request = ImageGenerationRequest(
            prompt=prompt, references=references, aspect_ratio="1:1", background="transparent",
        )
    elif kind == "character":
        prompt += (
            "\nSingle-character reference portrait. "
            "Square head-and-shoulders portrait, clear face and silhouette, neutral background, "
            "consistent costume details, no text, no collage."
        )
        request = ImageGenerationRequest(
            prompt=prompt, references=references, aspect_ratio="1:1", background="opaque",
        )
    else:
        request = ImageGenerationRequest(prompt=prompt, references=references, background="opaque")
    return get_image_provider("openai-gpt-image-flare").generate(request)
