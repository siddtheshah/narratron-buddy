"""Addressable package files, excluding runtime state and editor bookkeeping."""

from pathlib import Path, PurePosixPath
import re

from components.game_source import GAME_FILES


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
TEXT_EXTENSIONS = {".txt", ".md", ".yaml", ".yml", ".json", ".svg"}
ROOT_FILES = {"theater.yaml", "planning.yaml", "metadata.json", "README.md"}


class IneligibleAssetError(ValueError):
    """A file is outside the supported theater package layout."""


def safe_asset_path(root: Path, relative: str) -> Path:
    """Only package files are addressable; never internal metadata or runtime output."""
    normalized = relative.replace("\\", "/")
    parts = normalized.split("/")
    if not normalized or any(not part or part in {".", ".."} or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in parts):
        raise ValueError("Invalid asset path.")
    if any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part) for part in parts):
        raise ValueError("Reserved asset filename.")
    suffix = PurePosixPath(normalized).suffix.lower()
    allowed = normalized in ROOT_FILES or normalized in GAME_FILES
    allowed |= len(parts) >= 2 and parts[0] == "game" and suffix in {".svg", ".json"}
    allowed |= len(parts) >= 2 and parts[0] == "references" and suffix in IMAGE_EXTENSIONS
    allowed |= len(parts) >= 2 and parts[0] == "stamps" and suffix in IMAGE_EXTENSIONS
    if parts[:2] == ["references", "characters"]:
        allowed = len(parts) >= 4 and (suffix in IMAGE_EXTENSIONS or (len(parts) == 4 and parts[-1] == "character.yaml"))
    if parts[:2] == ["references", "scenes"]:
        allowed = len(parts) >= 4 and (suffix in IMAGE_EXTENSIONS or (len(parts) == 4 and parts[-1] == "scene.yaml"))
    allowed |= len(parts) >= 3 and parts[0] == "playlists" and suffix in AUDIO_EXTENSIONS
    allowed |= len(parts) == 3 and parts[0] == "playlists" and parts[-1] == "description.txt"
    allowed |= len(parts) >= 2 and parts[0] == "lore" and suffix in {".txt", ".md"}
    if not allowed:
        raise IneligibleAssetError("Use theater.yaml, planning.yaml, metadata.json, README.md, lore/*.txt or *.md, references/images, references/characters/<Name>/images or character.yaml, references/scenes/<Name>/images or scene.yaml, stamps/images, playlists/<Name>/audio or description.txt, or game/game.yaml (game.yml/game.json) with game/*.svg and companion game/*.json.")
    target = root.joinpath(*parts).resolve()
    if root.resolve() not in target.parents:
        raise ValueError("Asset path escapes the theater checkout.")
    return target
