"""Generate saved canvas media using the theater's configured providers."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import JsonValue, TypeAdapter
import yaml

from providers.music_provider import MusicGenerationRequest
from providers.registry import get_music_provider, get_animation_provider
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
    # Stage complete artifacts before the API persists or bills the request.
    # The editor and canvas assets pane both use this entry point.
    with TemporaryDirectory(prefix="narratron-animation-") as staging:
        output = Path(staging)
        provider = get_animation_provider(options=settings)
        provider.create_animation(prompt, identifier, output_dir=output, technique=animation_type)
        folder = output / "animations" / identifier
        return {
            f"animations/{identifier}/{path.name}": path.read_bytes()
            for path in sorted(folder.iterdir()) if path.is_file()
        }
