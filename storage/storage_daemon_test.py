"""Unit tests for DatabaseManager session persistence and StorageDaemon billing & cleanup logic."""

import datetime
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from storage.database import DatabaseManager
from storage.storage_daemon import StorageDaemon
from components.theater_manager import TheaterManager


class TestStorageDaemonAndPersistence(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_storage.db"
        self.theaters_dir = Path(self.test_dir) / "theaters"
        self.theaters_dir.mkdir(parents=True, exist_ok=True)

        self.db = DatabaseManager.from_local(str(self.db_path))
        self.theater_manager = TheaterManager(base_theaters_dir=self.theaters_dir)

        self.daemon = StorageDaemon(
            db=self.db,
            theater_manager=self.theater_manager,
            interval_seconds=1.0,
            ttl_seconds=3600.0,
            hourly_cost=2.0,
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_record_deployment_and_persistence(self):
        user = self.db.register_user("testuser", "test@example.com", "Password123")
        user_id = user["id"]

        # Record a non-persistent deployment
        self.db.record_deployment("theater_normal", user_id, "KEY-1", cost=0.0, is_persistent=False)
        self.assertFalse(self.db.get_theater_persistence("theater_normal"))

        # Record a persistent deployment
        self.db.record_deployment("theater_persistent", user_id, "KEY-2", cost=0.0, is_persistent=True)
        self.assertTrue(self.db.get_theater_persistence("theater_persistent"))

        # Toggle persistence
        self.assertTrue(self.db.set_theater_persistence("theater_normal", True))
        self.assertTrue(self.db.get_theater_persistence("theater_normal"))

        self.assertTrue(self.db.set_theater_persistence("theater_normal", False))
        self.assertFalse(self.db.get_theater_persistence("theater_normal"))

    def test_database_daemon_cleanup_and_charge_accrual(self):
        user = self.db.register_user("owner", "owner@example.com", "Password123")
        user_id = user["id"]

        now = datetime.datetime.now(datetime.timezone.utc)
        old_time = (now - datetime.timedelta(days=10)).isoformat()

        # 1. Non-persistent session (old -> should be cleaned up)
        self.db.record_deployment("old_temp_theater", user_id, "KEY-OLD", cost=0.0, is_persistent=False)
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET created_at = ? WHERE theater_id = ?",
                (old_time, "old_temp_theater")
            )
            conn.commit()

        # 2. Non-persistent session (recent -> should NOT be cleaned up)
        self.db.record_deployment("new_temp_theater", user_id, "KEY-NEW", cost=0.0, is_persistent=False)

        # 3. Persistent session (old created_at -> should accrue charges, NOT be cleaned up)
        self.db.record_deployment("persistent_theater", user_id, "KEY-PER", cost=0.0, is_persistent=True)
        three_hours_ago = (now - datetime.timedelta(hours=3)).isoformat()
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET created_at = ?, last_billed_at = ? WHERE theater_id = ?",
                (three_hours_ago, three_hours_ago, "persistent_theater")
            )
            conn.commit()

        # User credits balance before daemon run
        user_before = self.db.get_user_by_id(user_id)
        credits_before = user_before["credits"]

        # Run database daemon (ttl_seconds = 7 days = 604800s, hourly_cost = 1.0)
        res = self.db.run_database_daemon(
            ttl_seconds=604800.0,
            hourly_cost=1.0,
            current_time=now
        )

        self.assertIn("old_temp_theater", res["cleaned_up_sessions"])
        self.assertNotIn("new_temp_theater", res["cleaned_up_sessions"])
        self.assertNotIn("persistent_theater", res["cleaned_up_sessions"])

        # Check accrued charges: 3 hours @ 1.0 = 3.0 credits
        user_after = self.db.get_user_by_id(user_id)
        self.assertEqual(credits_before - user_after["credits"], 3.0)
        self.assertEqual(len(res["accrued_charges"]), 1)
        self.assertEqual(res["accrued_charges"][0]["theater_id"], "persistent_theater")
        self.assertEqual(res["accrued_charges"][0]["amount"], 3.0)

    def test_database_daemon_insufficient_credits_expiration(self):
        user = self.db.register_user("poor_owner", "poor@example.com", "Password123")
        user_id = user["id"]

        # Set user credits low
        with self.db._get_connection() as conn:
            conn.cursor().execute("UPDATE users SET credits = 0.5 WHERE id = ?", (user_id,))
            conn.commit()

        now = datetime.datetime.now(datetime.timezone.utc)
        five_hours_ago = (now - datetime.timedelta(hours=5)).isoformat()

        # Persistent session requiring 2.5 credits (5 hours @ 0.5/hr)
        self.db.record_deployment("unaffordable_persistent", user_id, "KEY-UNAFFORD", cost=0.0, is_persistent=True)
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET created_at = ?, last_billed_at = ? WHERE theater_id = ?",
                (five_hours_ago, five_hours_ago, "unaffordable_persistent")
            )
            conn.commit()

        # Run daemon
        res = self.db.run_database_daemon(
            ttl_seconds=604800.0,
            hourly_cost=0.5,
            current_time=now
        )

        # Session should be expired due to insufficient credits
        self.assertIn("unaffordable_persistent", res["cleaned_up_sessions"])
        self.assertIsNone(self.db.get_deployment("unaffordable_persistent"))

    def test_run_once_cleans_expired_session(self):
        user = self.db.register_user("daemon_user", "daemon@example.com", "Password123")
        user_id = user["id"]

        # Create a theater folder
        theater_meta = self.theater_manager.create_theater("Temp Theater", "theater_daemon_1")
        self.db.record_deployment("theater_daemon_1", user_id, theater_meta.join_key, cost=0.0, is_persistent=False)

        # Set created_at to 2 hours ago (exceeding 1 hour TTL)
        two_hours_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET created_at = ? WHERE theater_id = ?",
                (two_hours_ago, "theater_daemon_1")
            )
            conn.commit()

        res = self.daemon.run_once()

        self.assertIn("theater_daemon_1", res["cleaned_up_sessions"])
        self.assertIsNone(self.db.get_deployment("theater_daemon_1"))
        self.assertFalse((self.theaters_dir / "theater_daemon_1").exists())

    def test_run_once_accrues_persistent_charges(self):
        user = self.db.register_user("persistent_user", "per@example.com", "Password123")
        user_id = user["id"]

        theater_meta = self.theater_manager.create_theater("Persistent Theater", "theater_daemon_2")
        self.db.record_deployment("theater_daemon_2", user_id, theater_meta.join_key, cost=0.0, is_persistent=True)

        two_hours_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET created_at = ?, last_billed_at = ? WHERE theater_id = ?",
                (two_hours_ago, two_hours_ago, "theater_daemon_2")
            )
            conn.commit()

        initial_credits = self.db.get_user_by_id(user_id)["credits"]

        res = self.daemon.run_once()

        self.assertNotIn("theater_daemon_2", res["cleaned_up_sessions"])
        updated_credits = self.db.get_user_by_id(user_id)["credits"]
        # 2 hours @ 2.0/hr = 4.0 credits
        self.assertEqual(initial_credits - updated_credits, 4.0)
        self.assertEqual(len(res["accrued_charges"]), 1)
        self.assertEqual(res["accrued_charges"][0]["theater_id"], "theater_daemon_2")

    def test_default_pricing_md_accrual_rate(self):
        # Verify default daemon accrual rate aligns with PRICING.md (0.1 Credits/day flat rate = 0.004167 Credits/hr)
        default_daemon = StorageDaemon(
            db=self.db,
            theater_manager=self.theater_manager,
        )
        user = self.db.register_user("pricing_user", "pricing@example.com", "Password123")
        user_id = user["id"]

        theater_meta = self.theater_manager.create_theater("Pricing Theater", "theater_pricing_1")
        self.db.record_deployment("theater_pricing_1", user_id, theater_meta.join_key, cost=0.0, is_persistent=True)

        one_day_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)).isoformat()
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET created_at = ?, last_billed_at = ? WHERE theater_id = ?",
                (one_day_ago, one_day_ago, "theater_pricing_1")
            )
            conn.commit()

        initial_credits = self.db.get_user_by_id(user_id)["credits"]
        res = default_daemon.run_once()

        updated_credits = self.db.get_user_by_id(user_id)["credits"]
        # 24 hours @ 0.004167 Cr/hr = ~0.10 Credits (flat base persistent rate per PRICING.md)
        self.assertAlmostEqual(initial_credits - updated_credits, 0.10, places=2)
        self.assertEqual(len(res["accrued_charges"]), 1)


if __name__ == "__main__":
    unittest.main()
