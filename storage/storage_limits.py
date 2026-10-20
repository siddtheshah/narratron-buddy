"""Storage limits and accounting for user theaters and drafts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.database import _DatabaseManagerBase

MAX_FILE_UPLOAD_BYTES: int = 5 * 1024 * 1024  # 5 MB
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
) -> int:
    """Calculate the total bytes of all deployed theaters and active editor drafts for user_id."""
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

    seen_dirs: set[Path] = set()
    total_bytes = 0

    # 1. Deployed theaters owned by user
    theater_ids = resolved_db.get_user_theater_ids(user_id)
    for tid in theater_ids:
        theater_path = (root / tid).resolve()
        if theater_path.is_dir() and theater_path not in seen_dirs:
            seen_dirs.add(theater_path)
            total_bytes += get_directory_size_bytes(theater_path)

    # 2. Active editor drafts owned by user
    builder_root = (root / "_builder").resolve()
    if builder_root.is_dir():
        for draft_entry in builder_root.iterdir():
            if not draft_entry.is_dir():
                continue
            resolved_draft = draft_entry.resolve()
            if resolved_draft in seen_dirs:
                continue
            editor_json_path = draft_entry / "editor.json"
            if not editor_json_path.is_file():
                continue
            try:
                manifest_data = json.loads(editor_json_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if manifest_data.get("owner_id") == user_id:
                seen_dirs.add(resolved_draft)
                total_bytes += get_directory_size_bytes(draft_entry)

    return total_bytes


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
