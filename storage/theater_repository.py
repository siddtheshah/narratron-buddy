"""Storage repository for persisting and reconstructing theater assets.

Operates directly on a directory path (e.g. local directory or mounted GCS bucket).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any, Callable, Dict, List, Optional

from components.theater_manager import validate_theater_id
from components.theater_package import safe_asset_path

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

    def __init__(self, base_dir: Optional[Path | str] = None,
                 storage_changed: Callable[[str], None] | None = None) -> None:
        self.storage_changed = storage_changed
        if base_dir is not None:
            self._base_dir = Path(base_dir).resolve()
        else:
            self._base_dir = ensure_theaters_root()
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
            self._notify_storage_changed(theater_id)
            return True

        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / "references" / "characters").mkdir(parents=True, exist_ok=True)
            (target / "references" / "scenes").mkdir(parents=True, exist_ok=True)
            for item in source_dir.iterdir():
                if item.name in {"editor.json", "editor.tmp"}:
                    continue
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            # Package saves replace the authored file set, while runtime output
            # and canvas state retain the repository's existing merge behavior.
            for item in target.rglob("*"):
                if not item.is_file():
                    continue
                relative = item.relative_to(target).as_posix()
                try:
                    source_file = safe_asset_path(source_dir, relative)
                    safe_asset_path(target, relative)
                except ValueError:
                    continue
                if not source_file.is_file():
                    item.unlink()
            return True
        except Exception as e:
            logger.warning("Failed to export theater %s to %s: %s", theater_id, target, e)
            return False
        finally:
            self._notify_storage_changed(theater_id)

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
            shutil.copytree(source, target_dir, dirs_exist_ok=True)
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

    def list_theaters(self) -> List[Dict[str, Any]]:
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
