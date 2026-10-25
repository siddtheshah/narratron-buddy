"""Shared editor/canvas checkout lifecycle and explicit durable saves."""

from __future__ import annotations

import asyncio

from pydantic import JsonValue, TypeAdapter
import yaml

from components.theater_manager import TheaterManager, TheaterMetadata
from services.theater_builder import DraftInfo, TheaterBuilderStore, validate_text
from storage.theater_repository import TheaterRepository


checkout_locks: dict[str, asyncio.Lock] = {}


def refresh_checkout_metadata(info: DraftInfo, manager: TheaterManager) -> None:
    """Keep runtime metadata consistent with edits in the shared directory."""
    builder = TheaterBuilderStore(manager.base_dir)
    root = builder.directory(info.theater_id)
    text = (root / "theater.yaml").read_text(encoding="utf-8")
    validate_text("theater.yaml", text)
    config = TypeAdapter(dict[str, JsonValue]).validate_python(yaml.safe_load(text))
    metadata = manager.get_theater(info.theater_id)
    if metadata is None:
        metadata = TheaterMetadata(theater_id=info.theater_id, name=info.name)
    metadata.name = info.name
    metadata.config = config
    files = builder.files(info.theater_id)
    metadata.mounted_references = [item.path.removeprefix("references/") for item in files
                                   if item.kind == "image" and item.path.startswith("references/")]
    metadata.mounted_playlists = {}
    for item in files:
        if item.kind == "audio":
            playlist, filename = item.path.removeprefix("playlists/").split("/", 1)
            metadata.mounted_playlists.setdefault(playlist, []).append(filename)
    manager._save_metadata(metadata)


def save_theater_checkout(theater_id: str, manager: TheaterManager, repository: TheaterRepository) -> bool:
    """Save the same working copy from either screen, including package removals."""
    builder = TheaterBuilderStore(manager.base_dir)
    info = builder.load(theater_id)
    if info is not None:
        refresh_checkout_metadata(info, manager)
    if not repository.export_theater(theater_id, manager.theater(theater_id).directory()):
        return False
    if info is not None:
        info.saved_revision = info.revision
        builder.save_info(info)
    return True
