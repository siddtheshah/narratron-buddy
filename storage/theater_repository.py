"""Storage repository for persisting and reconstructing theater assets.

Shared directory addressing, mounted reads, and the repository save contract.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
import logging
from pathlib import Path
import shutil
from typing import Callable

from pydantic import JsonValue
from google.cloud import storage

from components.theater_manager import validate_theater_id

from absl import flags

if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local resources (database, adventures, theater repository) for testing and development.",
    )

FLAGS = flags.FLAGS


def get_theaters_root() -> Path:
    """Return the theater-data root for the selected runtime environment."""
    if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
        return Path(__file__).parent.parent / "theaters"
    return Path("/mnt/storage/theaters")


def ensure_theaters_root() -> Path:
    """Return and create the selected theater-data root."""
    theaters_root = get_theaters_root().resolve()
    theaters_root.mkdir(parents=True, exist_ok=True)
    return theaters_root


logger = logging.getLogger(__name__)


def _copy_directory_contents(source: Path, target: Path) -> None:
    """Merge bytes and directories without requiring POSIX metadata support.

    Bucket mounts can reject permission and timestamp updates. Even copytree
    with copy_function=copyfile still applies copystat to each directory.
    """
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            _copy_directory_contents(item, destination)
        else:
            shutil.copyfile(item, destination)


class TheaterRepository(ABC):
    """Common repository contract and reads through the backing directory.

    Cloud repositories share the existing mounted reads; concrete implementations
    select how working copies are persisted, without runtime backend detection.
    """

    def __init__(self, base_dir: Path | str,
                 storage_changed: Callable[[str], None] | None = None) -> None:
        self.storage_changed = storage_changed
        self._base_dir = Path(base_dir).resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _notify_storage_changed(self, theater_id: str) -> None:
        if self.storage_changed is not None:
            self.storage_changed(theater_id)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def theater_path(self, theater_id: str) -> Path:
        validate_theater_id(theater_id)
        resolved_base = self._base_dir.resolve()
        target = (self._base_dir / theater_id).resolve()
        if (
            target == resolved_base
            or target == resolved_base.parent
            or resolved_base not in target.parents
            or target.parent != resolved_base
        ):
            raise ValueError(f"Theater path cannot escape repository base directory: {theater_id}")
        return target

    def export_theater(
        self,
        theater_id: str,
        source_dir: Path | str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> bool:
        """Export a theater's directory tree and metadata to repository storage."""
        source_dir = Path(source_dir).resolve()
        if not source_dir.exists():
            logger.warning("Cannot export non-existent theater directory: %s", source_dir)
            return False

        if metadata:
            meta_file = source_dir / "theater.json"
            try:
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning("Failed to write theater.json during export for %s: %s", theater_id, e)

        target = self.theater_path(theater_id)
        if source_dir == target:
            self._notify_storage_changed(theater_id)
            return True

        return self._export_theater(theater_id, source_dir)

    @abstractmethod
    def _export_theater(self, theater_id: str, source_dir: Path) -> bool:
        """Persist a validated working copy through this repository's backend."""
        raise NotImplementedError

    def export_catalog_theater(
        self, theater_id: str, source_dir: Path, catalog_assets: dict[str, storage.Blob],
    ) -> bool:
        """Save a catalog checkout using the backend's normal export behavior."""
        return self.export_theater(theater_id, source_dir)

    def reconstruct_theater(self, theater_id: str, target_dir: Path | str) -> bool:
        """Reconstruct a theater's directory and assets from repository storage."""
        source = self.theater_path(theater_id)
        target_dir = Path(target_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        if source == target_dir:
            # Already at target location
            (target_dir / "output").mkdir(parents=True, exist_ok=True)
            (target_dir / "references").mkdir(parents=True, exist_ok=True)
            (target_dir / "playlists").mkdir(parents=True, exist_ok=True)
            (target_dir / "stamps").mkdir(parents=True, exist_ok=True)
            (target_dir / "animations").mkdir(parents=True, exist_ok=True)
            (target_dir / "references" / "characters").mkdir(parents=True, exist_ok=True)
            (target_dir / "references" / "scenes").mkdir(parents=True, exist_ok=True)
            self._notify_storage_changed(theater_id)
            return (target_dir / "theater.json").exists()

        if not source.exists():
            return False

        try:
            _copy_directory_contents(source, target_dir)
            (target_dir / "output").mkdir(parents=True, exist_ok=True)
            (target_dir / "references").mkdir(parents=True, exist_ok=True)
            (target_dir / "playlists").mkdir(parents=True, exist_ok=True)
            (target_dir / "stamps").mkdir(parents=True, exist_ok=True)
            (target_dir / "animations").mkdir(parents=True, exist_ok=True)
            (target_dir / "references" / "characters").mkdir(parents=True, exist_ok=True)
            (target_dir / "references" / "scenes").mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            logger.warning("Failed to reconstruct theater %s to %s: %s", theater_id, target_dir, e)
            return False
        finally:
            self._notify_storage_changed(theater_id)

    def get_theater_metadata(self, theater_id: str) -> dict[str, JsonValue] | None:
        """Fetch parsed theater.json metadata for a theater without modifying disk."""
        meta_file = self.theater_path(theater_id) / "theater.json"
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to read theater metadata for %s: %s", theater_id, e)
        return None

    def theater_exists(self, theater_id: str) -> bool:
        """Check if theater exists in repository."""
        path = self.theater_path(theater_id)
        return path.exists() and (path / "theater.json").exists()

    def delete_theater(self, theater_id: str) -> bool:
        """Delete theater directory from repository."""
        target = self.theater_path(theater_id)
        resolved_base = self._base_dir.resolve()
        resolved_target = target.resolve()
        if (
            resolved_target == resolved_base
            or resolved_target == resolved_base.parent
            or resolved_base not in resolved_target.parents
            or resolved_target.parent != resolved_base
        ):
            raise ValueError(f"Unsafe repository directory for deletion: {resolved_target}")
        if target.exists():
            shutil.rmtree(target)
            self._notify_storage_changed(theater_id)
            return True
        return False

    def list_theaters(self) -> list[dict[str, JsonValue]]:
        """List all theaters available in repository storage."""
        theaters = []
        if self._base_dir.exists():
            for item in self._base_dir.iterdir():
                if item.is_dir() and (item / "theater.json").exists():
                    try:
                        validate_theater_id(item.name)
                    except ValueError:
                        continue
                    meta = self.get_theater_metadata(item.name)
                    if meta:
                        theaters.append(meta)
        return theaters
