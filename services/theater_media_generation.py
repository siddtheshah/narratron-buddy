"""Generate saved canvas media using the theater's configured providers."""

import json
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter
import yaml

from providers.music_provider import MusicGenerationRequest
from providers.video_provider import VideoGenerationRequest
from providers.registry import get_music_provider, get_video_provider
from utils.config_loader import apply_app_config


def generate_theater_media(
    root: Path, *, kind: Literal["music", "animation"], prompt: str, identifier: str,
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
