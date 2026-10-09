"""Catalog playable theater assets without initializing generation providers."""

from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from components.theater_manager import Theater


class Track(BaseModel):
    id: str
    name: str
    url: str


class Playlist(BaseModel):
    id: str
    name: str
    tracks: list[Track]


class AnimationLayer(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str


class AnimationManifest(BaseModel):
    model_config = ConfigDict(extra="allow")
    scene_prompt: str = ""
    frames: list[str] = Field(default_factory=list)
    layers: list[AnimationLayer] = Field(default_factory=list)
    base_image: str = ""
    video_path: str = "video.mp4"
    video_url: str = ""
    poster_image: str = ""


class AnimationAsset(BaseModel):
    id: str
    name: str
    type: Literal["video", "layered", "triframe"]
    description: str
    url: str
    manifest: dict[str, JsonValue]
    frames: list[str]


def playlists(theater: Theater) -> list[Playlist]:
    result: list[Playlist] = []
    for root, generated in ((theater.playlists_dir(), False), (theater.music_artifacts_dir(), True)):
        root = root.resolve()
        groups: dict[str, list[Track]] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or root not in path.resolve().parents:
                continue
            if path.suffix.lower() not in (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"):
                continue
            relative = path.relative_to(root).as_posix()
            name = "Generated music" if generated else (path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else "Ungrouped")
            url = theater.get_url_for_path(str(path)) if generated else f"/theaters/{theater.theater_id}/playlists/{quote(relative, safe='/')}"
            if not generated and "/" not in relative:
                continue  # The serving route requires a named playlist folder.
            groups.setdefault(name, []).append(Track(id=url, name=path.stem, url=url))
        result.extend(
            Playlist(id="generated:music" if generated else f"playlist:{quote(name, safe='')}", name=name, tracks=tracks)
            for name, tracks in groups.items()
        )
    return result


def animations(theater: Theater) -> list[AnimationAsset]:
    root = (theater.output_dir() / "animations").resolve()
    result: list[AnimationAsset] = []
    for folder in sorted(root.glob("*")):
        if not folder.is_dir() or root not in folder.resolve().parents:
            continue
        try:
            def local_path(value: str) -> str:
                path = (folder / Path(value).name).resolve()
                if folder.resolve() not in path.parents or not path.is_file():
                    raise ValueError("Missing animation file")
                return str(path)

            manifest = AnimationManifest()
            kind: Literal["video", "layered", "triframe"]
            if (folder / "video.json").is_file() or (folder / "video.mp4").is_file():
                kind, filename = "video", "video.json"
            elif (folder / "layered.json").is_file() or (folder / "animation.json").is_file():
                kind, filename = "layered", "layered.json" if (folder / "layered.json").is_file() else "animation.json"
            else:
                kind, filename = "triframe", "triframe.json"
            if (folder / filename).is_file():
                manifest = AnimationManifest.model_validate_json((folder / filename).read_text(encoding="utf-8"))
            frames: list[str] = []
            if kind == "video":
                manifest.video_path = local_path(manifest.video_path)
                manifest.video_url = theater.get_url_for_path(manifest.video_path)
                preview = ""
                if manifest.poster_image:
                    try:
                        manifest.poster_image = local_path(manifest.poster_image)
                        preview = theater.get_url_for_path(manifest.poster_image)
                    except ValueError:
                        manifest.poster_image = ""
            elif kind == "layered":
                if len(manifest.layers) < 2:
                    continue
                manifest.base_image = local_path(manifest.base_image or manifest.layers[0].path)
                for layer in manifest.layers:
                    layer.path = local_path(layer.path)
                preview = theater.get_url_for_path(manifest.base_image)
            else:
                frames = [local_path(frame) for frame in (manifest.frames or [f"frame_{n}.jpg" for n in range(1, 4)])]
                if len(frames) != 3:
                    continue
                preview = theater.get_url_for_path(frames[0])
            result.append(AnimationAsset(id=folder.name, name=folder.name, type=kind,
                description=manifest.scene_prompt, url=preview, manifest=manifest.model_dump(mode="json"), frames=frames))
        except (OSError, ValueError, ValidationError):
            continue
    return result
