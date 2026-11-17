"""Resolve personal stamp images using explicitly supplied storage services."""

from __future__ import annotations

import logging
from pathlib import Path

from components.canvas.doodle_state import DoodleAction
from storage.database import DatabaseManager
from storage.stamp_storage import StampStorage

logger = logging.getLogger(__name__)


class UserStampResolver:
    def __init__(self, database_manager: DatabaseManager, stamp_storage: StampStorage) -> None:
        self.database_manager = database_manager
        self.stamp_storage = stamp_storage

    def resolve(self, action: DoodleAction) -> bytes | None:
        """Read a catalog stamp, then try legacy files in the supplied storage root."""
        numeric_stamp_id: int | None = None
        raw_stamp_id = action.get("stamp_id")
        if raw_stamp_id is not None:
            try:
                numeric_stamp_id = int(str(raw_stamp_id))
            except (ValueError, TypeError):
                pass
        url = str(action.get("url") or "").strip()
        if numeric_stamp_id is None and "/api/stamps/" in url:
            try:
                numeric_stamp_id = int(url.split("/api/stamps/", 1)[1].split("?")[0].split("/")[0])
            except (ValueError, TypeError):
                pass

        if numeric_stamp_id is not None:
            try:
                stamp_record = self.database_manager.get_stamp_by_id(numeric_stamp_id)
                if stamp_record is not None:
                    record_user_id = int(str(stamp_record.get("user_id", 0)))
                    filename = str(stamp_record.get("filename", ""))
                    data = self.stamp_storage.read_stamp(record_user_id, filename)
                    if data:
                        return data
            except Exception:
                logger.debug("Unable to read catalog stamp %s", numeric_stamp_id, exc_info=True)

        user_id: int | None = None
        raw_user = action.get("user_id")
        if raw_user is not None:
            try:
                user_id = int(str(raw_user))
            except (ValueError, TypeError):
                pass

        try:
            stamps_root = self.stamp_storage.base_dir.resolve()
            candidate_dirs: list[Path] = []
            if user_id is not None:
                candidate_dirs.append(stamps_root / str(user_id))
            candidate_dirs.append(stamps_root)
            stamp_name = str(action.get("name") or "").lower()
            for directory in candidate_dirs:
                if not directory.is_dir():
                    continue
                for entry in directory.glob("*"):
                    if entry.is_file() and entry.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                        if stamp_name and stamp_name in entry.stem.lower():
                            return entry.read_bytes()
                        if numeric_stamp_id is not None and str(numeric_stamp_id) in entry.stem:
                            return entry.read_bytes()
        except OSError:
            logger.debug("Unable to read legacy stamp files", exc_info=True)
        return None
