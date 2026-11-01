"""Catalog playable theater assets without initializing generation providers."""

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
import yaml

from components.theater_manager import Theater
from providers.image_provider import ImageReference
from providers.registry import get_text_response_provider
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseRequest,
    parse_and_validate_structured_response,
)
from utils.config_loader import apply_app_config

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ReferenceKind = Literal["character", "scene", "reference", "stamp"]
ReferenceIntentAction = Literal["modify", "create_new"]


@dataclass(frozen=True)
class TheaterReferenceEntry:
    name: str
    path: str
    kind: ReferenceKind
    folder: str = ""
    description: str = ""


class ReferenceIntentDecision(BaseModel):
    action: ReferenceIntentAction = Field(
        description="Whether the user wants to 'modify' the existing tagged reference or 'create_new' (create an entirely new reference/asset based on the tagged reference)."
    )
    target_reference: str = Field(
        default="",
        description="If action is 'modify', the name of the tagged reference to modify. If action is 'create_new', a short descriptive name for the new asset.",
    )
    reasoning: str = Field(
        default="",
        description="Short explanation of why 'modify' or 'create_new' was chosen.",
    )


def asset_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return "application/octet-stream"


def _pick_latest_folder_image(folders: list[Path]) -> Path | None:
    for folder in folders:
        if not folder.is_dir():
            continue
        candidates = [
            f for f in sorted(folder.iterdir())
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if candidates:
            def _sort_key(p: Path) -> tuple[int, float]:
                num = int(p.stem) if p.stem.isdigit() else 0
                return (num, p.stat().st_mtime if p.exists() else 0.0)

            candidates.sort(key=_sort_key)
            return candidates[-1]
    return None


def next_reference_iteration(
    root: Path,
    category: Literal["characters", "scenes"],
    folder: str,
) -> int:
    nums: list[int] = []
    for base in (
        root / "references" / category / folder,
        root / "output" / "artifacts" / "updated_references" / category / folder,
    ):
        if base.is_dir():
            for p in base.iterdir():
                if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS and p.stem.isdigit():
                    nums.append(int(p.stem))
    return (max(nums) + 1) if nums else 1


def list_theater_reference_entries(
    root: Path,
    *,
    include_stamps: bool = False,
) -> list[TheaterReferenceEntry]:
    resolved_root = root.resolve()
    entries: list[TheaterReferenceEntry] = []
    seen_keys: set[tuple[str, str]] = set()

    # 1. Characters (folder-based in updated_references/characters and references/characters)
    char_folders: dict[str, list[Path]] = {}
    for char_root in (
        resolved_root / "output" / "artifacts" / "updated_references" / "characters",
        resolved_root / "references" / "characters",
    ):
        if not char_root.is_dir():
            continue
        for directory in sorted(char_root.iterdir()):
            if directory.is_dir() and char_root in directory.resolve().parents:
                key = directory.name.lower().replace("_", " ").strip()
                if key:
                    char_folders.setdefault(key, []).append(directory)

    for _key, folders in sorted(char_folders.items(), key=lambda item: item[0]):
        latest = _pick_latest_folder_image(folders)
        if latest is None:
            continue
        folder_name = folders[0].name
        display_name = folder_name.replace("_", " ")
        rel_path = latest.resolve().relative_to(resolved_root).as_posix()
        char_desc = ""
        for fld in folders:
            char_yaml = fld / "character.yaml"
            if char_yaml.is_file():
                try:
                    loaded = yaml.safe_load(char_yaml.read_text(encoding="utf-8"))
                    if type(loaded) is dict and loaded.get("description"):
                        char_desc = str(loaded["description"]).strip()
                        break
                except Exception:
                    pass
        entries.append(
            TheaterReferenceEntry(
                name=display_name,
                path=rel_path,
                kind="character",
                folder=folder_name,
                description=char_desc,
            )
        )
        seen_keys.add(("character", display_name.lower()))

    # 2. Scenes (folder-based and flat files in references/scenes)
    scene_folders: dict[str, list[Path]] = {}
    for scene_root in (
        resolved_root / "output" / "artifacts" / "updated_references" / "scenes",
        resolved_root / "references" / "scenes",
    ):
        if not scene_root.is_dir():
            continue
        for directory in sorted(scene_root.iterdir()):
            if directory.is_dir() and scene_root in directory.resolve().parents:
                key = directory.name.lower().replace("_", " ").strip()
                if key:
                    scene_folders.setdefault(key, []).append(directory)

    seen_scene_keys: set[str] = set()
    for key, folders in sorted(scene_folders.items(), key=lambda item: item[0]):
        latest = _pick_latest_folder_image(folders)
        if latest is None:
            continue
        seen_scene_keys.add(key)
        folder_name = folders[0].name
        display_name = folder_name.replace("_", " ")
        rel_path = latest.resolve().relative_to(resolved_root).as_posix()
        scene_desc = ""
        for fld in folders:
            scene_yaml = fld / "scene.yaml"
            if scene_yaml.is_file():
                try:
                    loaded = yaml.safe_load(scene_yaml.read_text(encoding="utf-8"))
                    if type(loaded) is dict and loaded.get("description"):
                        scene_desc = str(loaded["description"]).strip()
                        break
                except Exception:
                    pass
        entries.append(
            TheaterReferenceEntry(
                name=display_name,
                path=rel_path,
                kind="scene",
                folder=folder_name,
                description=scene_desc,
            )
        )
        seen_keys.add(("scene", display_name.lower()))

    authored_scenes = resolved_root / "references" / "scenes"
    if authored_scenes.is_dir():
        for file in sorted(authored_scenes.iterdir()):
            if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS and authored_scenes in file.resolve().parents:
                key = file.stem.lower().replace("_", " ").strip()
                if key and key not in seen_scene_keys:
                    seen_scene_keys.add(key)
                    display_name = file.stem.replace("_", " ")
                    rel_path = file.resolve().relative_to(resolved_root).as_posix()
                    entries.append(
                        TheaterReferenceEntry(
                            name=display_name,
                            path=rel_path,
                            kind="scene",
                            folder="",
                        )
                    )
                    seen_keys.add(("scene", display_name.lower()))

    # 3. General references in references/ (excluding characters/ and scenes/)
    ref_dir = resolved_root / "references"
    if ref_dir.is_dir():
        for file in sorted(ref_dir.rglob("*")):
            if not file.is_file() or file.suffix.lower() not in IMAGE_EXTENSIONS or ref_dir not in file.resolve().parents:
                continue
            rel_to_ref = file.resolve().relative_to(ref_dir)
            if rel_to_ref.parts and rel_to_ref.parts[0] in ("characters", "scenes"):
                continue
            rel_path = file.resolve().relative_to(resolved_root).as_posix()
            entries.append(
                TheaterReferenceEntry(
                    name=file.stem,
                    path=rel_path,
                    kind="reference",
                    folder="",
                )
            )
            seen_keys.add(("reference", file.stem.lower()))

    # 4. Stamps in stamps/ (optional)
    if include_stamps:
        stamps_dir = resolved_root / "stamps"
        if stamps_dir.is_dir():
            for file in sorted(stamps_dir.rglob("*")):
                if not file.is_file() or file.suffix.lower() not in IMAGE_EXTENSIONS or stamps_dir not in file.resolve().parents:
                    continue
                rel_path = file.resolve().relative_to(resolved_root).as_posix()
                entries.append(
                    TheaterReferenceEntry(
                        name=file.stem,
                        path=rel_path,
                        kind="stamp",
                        folder="",
                    )
                )

    return entries


def _normalize_ref_token(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", value.strip().lstrip("@").lower()).strip()


def _match_entry_by_token(
    token: str,
    entries: list[TheaterReferenceEntry],
) -> TheaterReferenceEntry | None:
    clean = token.strip().lstrip("@").strip()
    if not clean:
        return None
    clean_lower = clean.lower()
    norm = _normalize_ref_token(clean)
    for entry in entries:
        if entry.path.lower() == clean_lower:
            return entry
    for entry in entries:
        if entry.name.lower() == clean_lower or (entry.folder and entry.folder.lower() == clean_lower):
            return entry
    for entry in entries:
        stem = Path(entry.path).stem.lower()
        if _normalize_ref_token(entry.name) == norm or (entry.folder and _normalize_ref_token(entry.folder) == norm) or _normalize_ref_token(stem) == norm:
            return entry
    return None


def extract_prompt_reference_entries(
    prompt: str,
    entries: list[TheaterReferenceEntry],
) -> list[TheaterReferenceEntry]:
    if not prompt or "@" not in prompt or not entries:
        return []

    candidate_aliases: list[tuple[str, TheaterReferenceEntry]] = []
    for entry in entries:
        aliases = {
            entry.name,
            entry.name.replace(" ", "_"),
            entry.name.replace(" ", "-"),
        }
        if entry.folder:
            aliases.add(entry.folder)
            aliases.add(entry.folder.replace(" ", "_"))
        stem = Path(entry.path).stem
        if not stem.isdigit():
            aliases.add(stem)
            aliases.add(stem.replace("_", " "))
        for alias in aliases:
            cleaned = alias.strip()
            if cleaned:
                candidate_aliases.append((cleaned, entry))

    candidate_aliases.sort(key=lambda item: len(item[0]), reverse=True)

    occupied_spans: list[tuple[int, int]] = []
    matched_with_pos: list[tuple[int, TheaterReferenceEntry]] = []

    for alias, entry in candidate_aliases:
        pattern = re.compile(
            r"(?:^|(?<=[\s([{,\"']))@" + re.escape(alias) + r"(?=$|[\s)\]}.,!?:;\"'])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(prompt):
            start, end = match.span()
            if any(not (end <= s or start >= e) for s, e in occupied_spans):
                continue
            occupied_spans.append((start, end))
            matched_with_pos.append((start, entry))

    matched_with_pos.sort(key=lambda item: item[0])
    result: list[TheaterReferenceEntry] = []
    seen_paths: set[str] = set()
    for _pos, entry in matched_with_pos:
        if entry.path not in seen_paths:
            seen_paths.add(entry.path)
            result.append(entry)
    return result


def extract_and_resolve_references(
    root: Path,
    prompt: str,
    explicit_references: list[str] | None = None,
    *,
    include_stamps: bool = True,
    max_references: int = 4,
) -> tuple[list[TheaterReferenceEntry], list[ImageReference]]:
    resolved_root = root.resolve()
    entries = list_theater_reference_entries(resolved_root, include_stamps=include_stamps)
    resolved_entries: list[TheaterReferenceEntry] = []
    seen_paths: set[str] = set()

    for raw_ref in explicit_references or []:
        matched = _match_entry_by_token(raw_ref, entries)
        if matched is not None:
            if matched.path not in seen_paths:
                seen_paths.add(matched.path)
                resolved_entries.append(matched)
            continue
        clean_rel = raw_ref.strip().replace("\\", "/").lstrip("/")
        if clean_rel:
            candidate = (resolved_root / clean_rel).resolve()
            if resolved_root in candidate.parents and candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS:
                rel_posix = candidate.relative_to(resolved_root).as_posix()
                if rel_posix not in seen_paths:
                    seen_paths.add(rel_posix)
                    parts = Path(rel_posix).parts
                    kind: ReferenceKind = "reference"
                    folder = ""
                    name = candidate.stem
                    if len(parts) >= 4 and parts[0] == "references" and parts[1] in ("characters", "scenes"):
                        kind = "character" if parts[1] == "characters" else "scene"
                        folder = parts[2]
                        name = folder
                    elif len(parts) >= 2 and parts[0] == "stamps":
                        kind = "stamp"
                    resolved_entries.append(
                        TheaterReferenceEntry(name=name, path=rel_posix, kind=kind, folder=folder)
                    )

    for prompt_entry in extract_prompt_reference_entries(prompt, entries):
        if prompt_entry.path not in seen_paths:
            seen_paths.add(prompt_entry.path)
            resolved_entries.append(prompt_entry)

    resolved_entries = resolved_entries[:max_references]
    image_references: list[ImageReference] = []
    for entry in resolved_entries:
        full_path = (resolved_root / entry.path).resolve()
        if resolved_root in full_path.parents and full_path.is_file():
            image_references.append(
                ImageReference(
                    name=full_path.name,
                    data=full_path.read_bytes(),
                    mime_type=asset_mime_type(full_path),
                    label=entry.name,
                    description=entry.description if entry.description else None,
                )
            )
    return resolved_entries, image_references


def classify_reference_intent(
    root: Path | None,
    *,
    kind: Literal["stamp", "scene", "character", "animation"],
    prompt: str,
    tagged_references: list[TheaterReferenceEntry],
    text_response_provider: TextResponseProvider | None = None,
) -> ReferenceIntentDecision:
    """Use a quick LLM call to determine if the user wants to modify the tagged reference or create a new one."""
    if not tagged_references:
        return ReferenceIntentDecision(action="create_new", target_reference="", reasoning="No references tagged.")

    provider = text_response_provider
    if provider is None:
        raw_config: dict[str, JsonValue] = {}
        if root is not None and (root / "theater.yaml").is_file():
            loaded = yaml.safe_load((root / "theater.yaml").read_text(encoding="utf-8"))
            if type(loaded) is dict:
                raw_config = TypeAdapter(dict[str, JsonValue]).validate_python(loaded)
        settings = TypeAdapter(dict[str, JsonValue]).validate_python(apply_app_config(raw_config))
        anim_cfg = TypeAdapter(dict[str, JsonValue]).validate_python(settings.get("animation") or {})
        provider_id = str(anim_cfg.get("text_provider") or "gemini-3")
        model_id = str(anim_cfg.get("text_model") or "gemini-3.5-flash-lite")
        provider = get_text_response_provider(provider_id, {"model": model_id})

    ref_descriptions = "\n".join(
        f"- @{ref.name} (kind: {ref.kind}, path: {ref.path})"
        for ref in tagged_references
    )
    classification_prompt = (
        f"The user is submitting a request in the canvas asset manager to generate a '{kind}'.\n"
        f"Tagged references from the theater:\n{ref_descriptions}\n\n"
        f"User request prompt:\n{prompt}\n\n"
        "Determine whether the user wants to:\n"
        "1. 'modify': Modify, update, restyle, or create a new version of the existing tagged reference itself "
        "(for example, changing a character's outfit/expression/age, changing a scene's lighting/weather/time of day, "
        "or editing an existing stamp).\n"
        "2. 'create_new': Create an entirely new, separate reference or asset based on or inspired by the tagged reference "
        "(for example, creating a different character related to the tagged character, creating a new scene featuring "
        "a tagged character, creating a stamp token from a character portrait, or creating an animation).\n\n"
        "If action is 'modify', set target_reference to the exact name of the tagged reference being modified.\n"
        "If action is 'create_new', set target_reference to a concise name for the new asset."
    )
    request = TextResponseRequest(
        prompt=classification_prompt,
        temperature=0.1,
        max_output_tokens=256,
        response_schema=ReferenceIntentDecision,
    )
    response = provider.generate(request)
    if response.parsed is not None:
        return ReferenceIntentDecision.model_validate(response.parsed)
    return TypeAdapter(ReferenceIntentDecision).validate_python(
        parse_and_validate_structured_response(ReferenceIntentDecision, response.text)
    )


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
    root = theater.animations_dir().resolve()
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
                if not preview and manifest.video_url:
                    preview = manifest.video_url
            elif kind == "layered":
                if len(manifest.layers) < 2:
                    continue
                manifest.base_image = local_path(manifest.base_image or manifest.layers[0].path)
                for layer in manifest.layers:
                    layer.path = local_path(layer.path)
                preview = theater.get_url_for_path(manifest.base_image)
            else:
                default_extension = "webp" if all((folder / f"frame_{n}.webp").is_file() for n in range(1, 4)) else "jpg"
                frames = [local_path(frame) for frame in (manifest.frames or [f"frame_{n}.{default_extension}" for n in range(1, 4)])]
                if len(frames) != 3:
                    continue
                preview = theater.get_url_for_path(frames[0])
            result.append(AnimationAsset(id=folder.name, name=folder.name, type=kind,
                description=manifest.scene_prompt, url=preview, manifest=manifest.model_dump(mode="json"), frames=frames))
        except (OSError, ValueError, ValidationError):
            continue
    return result
