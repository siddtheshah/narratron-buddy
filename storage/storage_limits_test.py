"""Tests for user theaters storage limit calculations."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
