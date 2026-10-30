"""Generate saved canvas media using the theater's configured providers."""

import json
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter
import yaml

from providers.music_provider import MusicGenerationRequest
from providers.video_provider import VideoGenerationRequest
from providers.registry import get_music_provider, get_video_provider
from providers.registry import get_image_provider, get_text_response_provider
from providers.image_provider import ImageGenerationRequest, ImageReference, generated_image_to_webp
from providers.fal_qwen_layered_provider import FalQwenLayeredProvider, LayeredImageRequest
from tools.animation_tool import AnimationTools
from utils.config_loader import apply_app_config


def generate_theater_media(
    root: Path, *, kind: Literal["music", "animation"], prompt: str, identifier: str,
    animation_type: Literal["video", "layered", "triframe"] = "video",
) -> dict[str, bytes]:
    settings = TypeAdapter(dict[str, JsonValue]).validate_python(
        apply_app_config(yaml.safe_load((root / "theater.yaml").read_text(encoding="utf-8")) or {})
    )
    section = "music" if kind == "music" else "animation"
    config = TypeAdapter(dict[str, JsonValue]).validate_python(settings.get(section) or {})
    if kind == "music":
        style = str(config.get("style") or "").strip()
        effective_prompt = f"{prompt}\nStyle: {style}" if style else prompt
        options = TypeAdapter(dict[str, JsonValue]).validate_python(config.get("provider_options") or {})
        result = get_music_provider(str(config.get("provider") or "lyria"), options).generate(
            MusicGenerationRequest(prompt=effective_prompt)
        )
        extensions = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac", "audio/mp4": "m4a"}
        if not result.audio_bytes or result.mime_type not in extensions:
            raise ValueError("Music generation must return supported audio.")
        return {f"output/music/{identifier}.{extensions[result.mime_type]}": result.audio_bytes}
    visuals = TypeAdapter(dict[str, JsonValue]).validate_python(settings.get("visuals") or {})
    style = str(visuals.get("style") or "").strip()
    effective_prompt = f"{prompt}\nStyle: {style}" if style else prompt
    if animation_type != "video":
        return _generate_image_animation(config, visuals, prompt, effective_prompt, identifier, animation_type)
    effective_prompt += "\nCreate a seamless looping animation, with no text or cuts."
    video = get_video_provider(str(config.get("video_provider") or "fal-minimax-h3-turbo")).generate(
        VideoGenerationRequest(
            prompt=effective_prompt, aspect_ratio="16:9",
            video_duration_seconds=TypeAdapter(int).validate_python(config.get("video_duration_seconds") or 5),
        )
    )
    if not video.video_bytes or video.mime_type != "video/mp4":
        raise ValueError("Animation generation must return an MP4 video.")
    manifest = {"version": 1, "type": "video", "scene_prompt": prompt, "video_path": "video.mp4", "loop": True, "muted": True}
    return {
        f"output/animations/{identifier}/video.mp4": video.video_bytes,
        f"output/animations/{identifier}/video.json": json.dumps(manifest).encode("utf-8"),
    }


def _generate_image_animation(
    config: dict[str, JsonValue], visuals: dict[str, JsonValue], prompt: str,
    effective_prompt: str, identifier: str, animation_type: Literal["layered", "triframe"],
) -> dict[str, bytes]:
    planner = get_text_response_provider(
        str(config.get("text_provider") or "gemini-3"),
        {"model": str(config.get("text_model") or "gemini-3.5-flash-lite")},
    )
    image_provider = get_image_provider(
        str(visuals.get("model") or "hybrid-flux-gemini"),
        TypeAdapter(dict[str, JsonValue]).validate_python(visuals.get("model_options") or {}),
    )
    folder = f"output/animations/{identifier}"
    files: dict[str, bytes] = {}
    manifest: dict[str, JsonValue] = {"version": 1, "type": animation_type, "scene_prompt": prompt}
    if animation_type == "triframe":
        plan, _ = AnimationTools.plan_triframe_with_provider(planner, effective_prompt)
        previous: list[ImageReference] = []
        frames: list[JsonValue] = []
        for index, change in enumerate(["", plan["second_frame_change"], plan["third_frame_change"]], start=1):
            frame_prompt = f"{plan['base_frame']}\n{change}\nRender exactly one full-bleed scene, no collage, panels, text or borders."
            style = str(visuals.get("style") or "").strip()
            if style:
                frame_prompt += f"\nStyle: {style}"
            result = image_provider.generate(ImageGenerationRequest(prompt=frame_prompt, references=previous, aspect_ratio="16:9"))
            content = generated_image_to_webp(result.image_bytes)
            filename = f"frame_{index}.webp"
            files[f"{folder}/{filename}"] = content
            frames.append(filename)
            previous = [ImageReference(name=filename, data=content, mime_type="image/webp")]
        manifest["frames"] = frames
    else:
        layers, _ = AnimationTools.plan_layers_with_provider(planner, effective_prompt)
        flattened = AnimationTools._flatten_layer_plan(effective_prompt, layers)
        base = image_provider.generate(ImageGenerationRequest(prompt=flattened, aspect_ratio="16:9"))
        files[f"{folder}/base.webp"] = generated_image_to_webp(base.image_bytes)
        decomposition = AnimationTools._decomposition_prompt(prompt, layers)
        layered_result = FalQwenLayeredProvider().decompose(LayeredImageRequest(
            image_bytes=base.image_bytes, mime_type=base.mime_type, prompt=decomposition, num_layers=len(layers),
        ))
        if len(layered_result.images) != len(layers):
            raise ValueError("Layer decomposition did not produce the planned layers.")
        manifest_layers: list[JsonValue] = []
        for index, (layer, (content, _)) in enumerate(zip(layers, layered_result.images)):
            filename = f"layer_{index + 1}.webp"
            files[f"{folder}/{filename}"] = generated_image_to_webp(content)
            manifest_layers.append({**layer, "path": filename, "order": index})
        manifest["base_image"] = "base.webp"
        manifest["layers"] = manifest_layers
    files[f"{folder}/{animation_type}.json"] = json.dumps(manifest).encode("utf-8")
    return files
