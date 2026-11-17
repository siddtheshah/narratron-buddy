"""Personal stamp resolution uses the supplied database and storage root."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from components.canvas.doodle_state import DoodleAction
from services.user_stamp_resolver import UserStampResolver
from storage.database import DatabaseManager
from storage.stamp_storage import StampStorage


@pytest.mark.parametrize("action", [
    {"stamp_id": 42},
    {"stamp_id": "42"},
    {"url": "/api/stamps/42?revision=1"},
    {"stamp_id": "legacy", "url": "/api/stamps/42/image"},
])
def test_catalog_stamp_uses_record_owner_and_filename(action: DoodleAction) -> None:
    database = MagicMock(spec=DatabaseManager)
    database.get_stamp_by_id.return_value = {"user_id": 7, "filename": "goblin.webp"}
    storage = MagicMock(spec=StampStorage)
    storage.read_stamp.return_value = b"STAMP"
    resolver = UserStampResolver(database, storage)

    assert resolver.resolve({**action, "user_id": 999}) == b"STAMP"
    database.get_stamp_by_id.assert_called_once_with(42)
    storage.read_stamp.assert_called_once_with(7, "goblin.webp")


@pytest.mark.parametrize("filename, action", [
    ("goblin.png", {"name": "Goblin", "user_id": 7}),
    ("old_42.webp", {"stamp_id": 42, "user_id": 7}),
    ("goblin.png", {"name": "Goblin", "user_id": "invalid"}),
])
def test_legacy_stamp_uses_supplied_storage_root(tmp_path: Path, filename: str, action: DoodleAction) -> None:
    database = MagicMock(spec=DatabaseManager)
    database.get_stamp_by_id.return_value = None
    storage = MagicMock(spec=StampStorage)
    storage.base_dir = tmp_path
    directory = tmp_path / "7" if action.get("user_id") == 7 else tmp_path
    directory.mkdir(exist_ok=True)
    (directory / filename).write_bytes(b"LEGACY")
    resolver = UserStampResolver(database, storage)

    assert resolver.resolve(action) == b"LEGACY"
    storage.read_stamp.assert_not_called()


def test_catalog_failure_preserves_legacy_fallback(tmp_path: Path) -> None:
    database = MagicMock(spec=DatabaseManager)
    database.get_stamp_by_id.side_effect = RuntimeError("Unavailable")
    storage = MagicMock(spec=StampStorage)
    storage.base_dir = tmp_path
    (tmp_path / "stamp_42.png").write_bytes(b"LEGACY")

    assert UserStampResolver(database, storage).resolve({"stamp_id": 42}) == b"LEGACY"


def test_missing_stamp_returns_none_without_reading_unrelated_files(tmp_path: Path) -> None:
    database = MagicMock(spec=DatabaseManager)
    storage = MagicMock(spec=StampStorage)
    storage.base_dir = tmp_path
    (tmp_path / "unrelated.png").write_bytes(b"UNRELATED")

    assert UserStampResolver(database, storage).resolve({"url": "/api/stamps/invalid"}) is None
    database.get_stamp_by_id.assert_not_called()
    storage.read_stamp.assert_not_called()
