"""Tests for user theaters storage limit calculations."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from components.theater_manager import TheaterManager
from services.theater_builder import DraftInfo, TheaterBuilderStore
from storage.theater_repository import TheaterRepository

from storage.database import LocalDatabaseManager
from storage.storage_limits import (
    MAX_FILE_UPLOAD_BYTES,
    MAX_USER_THEATER_STORAGE_BYTES,
    calculate_user_theaters_storage_bytes,
    check_user_theaters_storage_allowance,
    get_directory_size_bytes,
)


class TestStorageLimits(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.theaters_root = Path(self.temp_dir.name)
        self.db = LocalDatabaseManager(db_path=":memory:")
        user = self.db.register_user("storagetester", "storagetester@example.com", "pass123")
        self.user_id = int(user["id"])

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_constants(self) -> None:
        self.assertEqual(MAX_FILE_UPLOAD_BYTES, 10 * 1024 * 1024)
        self.assertEqual(MAX_USER_THEATER_STORAGE_BYTES, 500 * 1024 * 1024)

    def test_get_directory_size_bytes(self) -> None:
        sub = self.theaters_root / "sample"
        sub.mkdir()
        (sub / "a.txt").write_bytes(b"12345")
        (sub / "b.txt").write_bytes(b"67890")
        nested = sub / "sub"
        nested.mkdir()
        (nested / "c.txt").write_bytes(b"abcdef")
        self.assertEqual(get_directory_size_bytes(sub), 16)
        self.assertEqual(get_directory_size_bytes(self.theaters_root / "nonexistent"), 0)

    def test_calculate_user_theaters_storage_includes_checkout_growth_once(self) -> None:
        # Initially empty
        self.assertEqual(
            calculate_user_theaters_storage_bytes(self.user_id, db=self.db, theaters_root=self.theaters_root),
            0,
        )

        # Deployed theater owned by user
        tid1 = "theater_1"
        self.db.record_deployment(tid1, self.user_id, "KEY-1", cost=0.0, name="Th1")
        th1_dir = self.theaters_root / tid1
        th1_dir.mkdir()
        (th1_dir / "image.png").write_bytes(b"X" * 1000)

        self.assertEqual(
            calculate_user_theaters_storage_bytes(self.user_id, db=self.db, theaters_root=self.theaters_root),
            1000,
        )

        # Another user's deployed theater is ignored
        other_user = self.db.register_user("other", "other@example.com", "pass123")
        other_uid = int(other_user["id"])
        tid2 = "theater_2"
        self.db.record_deployment(tid2, other_uid, "KEY-2", cost=0.0, name="Th2")
        th2_dir = self.theaters_root / tid2
        th2_dir.mkdir()
        (th2_dir / "other.png").write_bytes(b"Y" * 5000)

        self.assertEqual(
            calculate_user_theaters_storage_bytes(self.user_id, db=self.db, theaters_root=self.theaters_root),
            1000,
        )

        # The editor and canvas share a checkout of the same owned theater.
        checkouts_root = self.theaters_root / "runtime"
        checkout = checkouts_root / tid1
        checkout.mkdir(parents=True)
        (checkout / "audio.mp3").write_bytes(b"Z" * 2000)
        self.db.invalidate_theater_storage(tid1)

        self.assertEqual(
            calculate_user_theaters_storage_bytes(self.user_id, db=self.db, theaters_root=self.theaters_root, checkouts_root=checkouts_root),
            2000,
        )

        other_checkout = checkouts_root / tid2
        other_checkout.mkdir(parents=True)
        (other_checkout / "big.mp3").write_bytes(b"W" * 10000)

        self.assertEqual(
            calculate_user_theaters_storage_bytes(self.user_id, db=self.db, theaters_root=self.theaters_root, checkouts_root=checkouts_root),
            2000,
        )

    def test_check_user_theaters_storage_allowance_limits(self) -> None:
        check_user_theaters_storage_allowance(
            self.user_id, 1000, db=self.db, theaters_root=self.theaters_root
        )

        # Exceeds max user storage
        with self.assertRaises(ValueError) as ctx:
            check_user_theaters_storage_allowance(
                self.user_id,
                MAX_USER_THEATER_STORAGE_BYTES + 1,
                db=self.db,
                theaters_root=self.theaters_root,
            )
        self.assertIn("storage limit of 500MB exceeded", str(ctx.exception))

    def usage(self) -> int:
        return calculate_user_theaters_storage_bytes(
            self.user_id, db=self.db, theaters_root=self.theaters_root / "saved",
            checkouts_root=self.theaters_root / "runtime",
        )

    def test_repeated_quota_checks_reuse_persisted_index_without_filesystem_access(self) -> None:
        self.db.record_deployment("stage", self.user_id, "KEY", cost=0.0)
        saved = self.theaters_root / "saved" / "stage"
        saved.mkdir(parents=True)
        (saved / "asset.txt").write_bytes(b"12345")
        self.assertEqual(self.usage(), 5)
        self.assertEqual(self.db.get_user_theater_storage(self.user_id)[0]["storage_bytes"], 5)
        with patch("storage.storage_limits.get_directory_size_bytes", side_effect=AssertionError("Unexpected scan")):
            self.assertEqual(self.usage(), 5)
            self.assertEqual(self.usage(), 5)

    def test_index_survives_database_reopen_and_schema_migration_is_idempotent(self) -> None:
        database = LocalDatabaseManager(db_path=str(self.theaters_root / "index.db"))
        try:
            user = database.register_user("persistent", "persistent@example.com", "pass123")
            uid = int(user["id"])
            database.record_deployment("stage", uid, "KEY", cost=0.0)
            self.assertTrue(database.cache_theater_storage("stage", 123, 0))
            database.close()
            # Opening again reruns additive schema migration without clearing totals.
            with patch("storage.storage_limits.get_directory_size_bytes", side_effect=AssertionError("Unexpected scan")):
                self.assertEqual(calculate_user_theaters_storage_bytes(uid, db=database), 123)
        finally:
            database.close()

    def test_deleted_or_changed_record_cannot_accept_an_old_scan(self) -> None:
        self.db.record_deployment("stage", self.user_id, "KEY", cost=0.0)
        self.db.invalidate_theater_storage("stage")
        self.assertFalse(self.db.cache_theater_storage("stage", 1, 0))
        self.db.delete_deployment("stage")
        self.assertFalse(self.db.cache_theater_storage("stage", 1, 1))
        with self.assertRaisesRegex(ValueError, "negative"):
            self.db.cache_theater_storage("stage", -1, 1)

    def test_only_changed_theater_is_rescanned_and_deleted_theaters_stop_counting(self) -> None:
        for tid in ("one", "two"):
            self.db.record_deployment(tid, self.user_id, "KEY", cost=0.0)
            saved = self.theaters_root / "saved" / tid
            saved.mkdir(parents=True)
            (saved / "asset.txt").write_bytes(b"12345")
        self.assertEqual(self.usage(), 10)
        (self.theaters_root / "saved" / "one" / "asset.txt").write_bytes(b"123456789")
        self.db.invalidate_theater_storage("one")
        with patch("storage.storage_limits.get_directory_size_bytes", wraps=get_directory_size_bytes) as scan:
            self.assertEqual(self.usage(), 14)
            self.assertEqual([call.args[0].name for call in scan.call_args_list], ["one", "one"])
        self.db.delete_deployment("one")
        with patch("storage.storage_limits.get_directory_size_bytes", side_effect=AssertionError("Unexpected scan")):
            self.assertEqual(self.usage(), 5)

    def test_scan_invalidated_during_measurement_is_retried(self) -> None:
        self.db.record_deployment("stage", self.user_id, "KEY", cost=0.0)
        calls = 0

        def measure(directory: Path) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                self.db.invalidate_theater_storage("stage")
            return calls

        with patch("storage.storage_limits.get_directory_size_bytes", side_effect=measure):
            self.assertEqual(self.usage(), 4)
        self.assertEqual(calls, 4)
        record = self.db.get_user_theater_storage(self.user_id)[0]
        self.assertEqual(record["storage_bytes"], 4)
        self.assertFalse(self.db.cache_theater_storage("stage", 1, 0))

    def test_dirty_theater_near_quota_is_remeasured_before_accepting_upload(self) -> None:
        self.db.record_deployment("stage", self.user_id, "KEY", cost=0.0)
        self.assertEqual(self.usage(), 0)
        self.db.invalidate_theater_storage("stage")
        with patch("storage.storage_limits.get_directory_size_bytes", return_value=MAX_USER_THEATER_STORAGE_BYTES):
            with self.assertRaisesRegex(ValueError, "storage limit"):
                check_user_theaters_storage_allowance(self.user_id, 1, db=self.db, theaters_root=self.theaters_root)

    def test_editor_changes_and_saves_invalidate_index_but_metadata_reads_do_not(self) -> None:
        manager = TheaterManager(self.theaters_root / "runtime", storage_changed=self.db.invalidate_theater_storage)
        repository = TheaterRepository(self.theaters_root / "saved", storage_changed=self.db.invalidate_theater_storage)
        manager.create_theater("Stage", "stage", theater_config={"live_agent": {}})
        self.db.record_deployment("stage", self.user_id, "KEY", cost=0.0)
        builder = TheaterBuilderStore(manager.base_dir, manager.notify_storage_changed)
        info = DraftInfo(theater_id="stage", owner_id=self.user_id, name="Stage")
        self.usage()
        with patch("storage.storage_limits.get_directory_size_bytes", side_effect=AssertionError("Unexpected scan")):
            manager.get_theater("stage")
            self.usage()
        builder.write_files(info, {"lore/new.txt": b"X" * 1000})
        self.assertIsNone(self.db.get_user_theater_storage(self.user_id)[0]["storage_bytes"])
        checkout_size = self.usage()
        self.assertEqual(checkout_size, get_directory_size_bytes(manager.theater("stage").directory()))
        self.assertTrue(repository.export_theater("stage", manager.theater("stage").directory()))
        self.assertIsNone(self.db.get_user_theater_storage(self.user_id)[0]["storage_bytes"])
        self.assertEqual(self.usage(), checkout_size)
        builder.delete_files(info, ["lore/new.txt"])
        self.assertEqual(self.usage(), get_directory_size_bytes(repository.theater_path("stage")))
        self.assertTrue(repository.export_theater("stage", manager.theater("stage").directory()))
        self.assertLess(self.usage(), checkout_size)

    def test_failed_editor_batch_invalidates_previously_cached_size(self) -> None:
        manager = TheaterManager(self.theaters_root / "runtime", storage_changed=self.db.invalidate_theater_storage)
        manager.create_theater("Stage", "stage", theater_config={"live_agent": {}})
        self.db.record_deployment("stage", self.user_id, "KEY", cost=0.0)
        self.usage()
        builder = TheaterBuilderStore(manager.base_dir, manager.notify_storage_changed)
        info = DraftInfo(theater_id="stage", owner_id=self.user_id, name="Stage")
        with patch.object(Path, "write_bytes", side_effect=OSError("Disk full")):
            with self.assertRaisesRegex(OSError, "Disk full"):
                builder.write_files(info, {"lore/new.txt": b"hello"})
        self.assertIsNone(self.db.get_user_theater_storage(self.user_id)[0]["storage_bytes"])


if __name__ == "__main__":
    unittest.main()
