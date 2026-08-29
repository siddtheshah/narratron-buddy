"""Storage repository for persisting and reconstructing theater assets.

Operates directly on a directory path (e.g. local directory or mounted GCS bucket).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

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


class TheaterRepository:
    """Provides theater export, reconstruction, and metadata management on a storage directory."""

    def __init__(self, base_dir: Optional[Path | str] = None):
        if base_dir is not None:
            self._base_dir = Path(base_dir).resolve()
        else:
            self._base_dir = ensure_theaters_root()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def theater_path(self, theater_id: str) -> Path:
        return self._base_dir / theater_id

    def export_theater(
        self,
        theater_id: str,
        source_dir: Path | str,
        metadata: Optional[Dict[str, Any]] = None,
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
            return True

        try:
            target.mkdir(parents=True, exist_ok=True)
            for item in source_dir.iterdir():
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            return True
        except Exception as e:
            logger.warning("Failed to export theater %s to %s: %s", theater_id, target, e)
            return False

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
            return (target_dir / "theater.json").exists()

        if not source.exists():
            return False

        try:
            shutil.copytree(source, target_dir, dirs_exist_ok=True)
            (target_dir / "output").mkdir(parents=True, exist_ok=True)
            (target_dir / "references").mkdir(parents=True, exist_ok=True)
            (target_dir / "playlists").mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            logger.warning("Failed to reconstruct theater %s to %s: %s", theater_id, target_dir, e)
            return False

    def get_theater_metadata(self, theater_id: str) -> Optional[Dict[str, Any]]:
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
        if target.exists():
            try:
                shutil.rmtree(target)
                return True
            except Exception as e:
                logger.warning("Failed to delete theater %s: %s", theater_id, e)
                return False
        return False

    def list_theaters(self) -> List[Dict[str, Any]]:
        """List all theaters available in repository storage."""
        theaters = []
        if self._base_dir.exists():
            for item in self._base_dir.iterdir():
                if item.is_dir() and (item / "theater.json").exists():
                    meta = self.get_theater_metadata(item.name)
                    if meta:
                        theaters.append(meta)
        return theaters
