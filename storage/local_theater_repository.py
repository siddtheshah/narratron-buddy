"""Theater persistence using local directory copies."""

from pathlib import Path
import logging
import shutil

from components.theater_package import safe_asset_path
from storage.theater_repository import TheaterRepository, _copy_directory_contents

logger = logging.getLogger(__name__)


class LocalTheaterRepository(TheaterRepository):
    """Save working copies into a local or explicitly supplied directory."""

    def _export_theater(self, theater_id: str, source_dir: Path) -> bool:
        target = self.theater_path(theater_id)
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / "references" / "characters").mkdir(parents=True, exist_ok=True)
            (target / "references" / "scenes").mkdir(parents=True, exist_ok=True)
            for item in source_dir.iterdir():
                if item.name in {"editor.json", "editor.tmp"}:
                    continue
                dest = target / item.name
                if item.is_dir():
                    _copy_directory_contents(item, dest)
                else:
                    shutil.copyfile(item, dest)
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

