"""Storage limits for theaters and their shared working copies."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.database import _DatabaseManagerBase

MAX_FILE_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB
MAX_USER_THEATER_STORAGE_BYTES: int = 500 * 1024 * 1024  # 500 MB


def get_directory_size_bytes(directory: Path) -> int:
    """Return the total size of regular files inside directory."""
    if not directory.is_dir():
        return 0
    total = 0
    for item in directory.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def calculate_user_theaters_storage_bytes(
    user_id: int,
    db: _DatabaseManagerBase | None = None,
    theaters_root: Path | None = None,
    checkouts_root: Path | None = None,
) -> int:
    """Count each owned theater once, reserving space for unsaved checkout growth."""
    if db is None:
        import object_registry
        resolved_db = object_registry.db
    else:
        resolved_db = db

    if theaters_root is None:
        from storage.theater_repository import get_theaters_root
        root = get_theaters_root()
    else:
        root = theaters_root

    if checkouts_root is None:
        from components.theater_manager import get_ephemeral_root
        checkouts_root = get_ephemeral_root()
    return sum(max(get_directory_size_bytes(root / tid), get_directory_size_bytes(checkouts_root / tid))
               for tid in resolved_db.get_user_theater_ids(user_id))


def check_user_theaters_storage_allowance(
    user_id: int,
    additional_bytes: int,
    db: _DatabaseManagerBase | None = None,
    theaters_root: Path | None = None,
) -> None:
    """Raise ValueError if additional_bytes would push user's theater storage over MAX_USER_THEATER_STORAGE_BYTES."""
    if additional_bytes <= 0:
        return
    current_bytes = calculate_user_theaters_storage_bytes(user_id, db=db, theaters_root=theaters_root)
    if current_bytes + additional_bytes > MAX_USER_THEATER_STORAGE_BYTES:
        current_mb = current_bytes / (1024 * 1024)
        max_mb = MAX_USER_THEATER_STORAGE_BYTES // (1024 * 1024)
        raise ValueError(
            f"User theater storage limit of {max_mb}MB exceeded (currently using {current_mb:.1f}MB). "
            "Please delete existing theaters or assets to free up space."
        )
