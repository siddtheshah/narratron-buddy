"""Unit tests for theater storage billing ticks, watermark advancement, 50MB free allowance, and 0-credit behavior."""

from __future__ import annotations

import asyncio
import datetime
import shutil
import tempfile
import unittest
from pathlib import Path


from object_registry import run_storage_billing_loop
from pricing.pricing_controller import PricingController
from storage.database import LocalDatabaseManager, StorageBillingTickResult
from storage.storage_limits import FREE_USER_THEATER_STORAGE_BYTES


class TestStorageBillingAndWatermark(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_billing.db"
        self.pricing_controller = PricingController(
            storage_gb_daily_rate=0.033,
        )
        self.db = LocalDatabaseManager(str(self.db_path), pricing_controller=self.pricing_controller)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_initial_tick_initializes_watermark_without_charges(self) -> None:
        self.assertIsNone(self.db.get_storage_billing_watermark())
        now = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        result: StorageBillingTickResult = self.db.run_storage_billing_tick(current_time=now)
        self.assertEqual(result["watermark"], now.isoformat())
        self.assertEqual(result["hours_billed"], 0)
        self.assertEqual(result["charges"], [])
        self.assertEqual(self.db.get_storage_billing_watermark(), now.isoformat())

    def test_tick_under_one_hour_does_not_advance_watermark(self) -> None:
        t0 = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.db.set_storage_billing_watermark(t0.isoformat())

        # 30 minutes later
        t_half = t0 + datetime.timedelta(minutes=30)
        result: StorageBillingTickResult = self.db.run_storage_billing_tick(current_time=t_half)
        self.assertEqual(result["watermark"], t0.isoformat())
        self.assertEqual(result["hours_billed"], 0)
        self.assertEqual(result["charges"], [])
        self.assertEqual(self.db.get_storage_billing_watermark(), t0.isoformat())

    def test_first_50mb_free_and_excess_billed_at_marketed_rate(self) -> None:
        user_free = self.db.register_user("free_user", "free@example.com", "Password123")
        uid_free = int(user_free["id"])
        self.db.add_user_credits(uid_free, 10.0, 0.5)

        user_paid = self.db.register_user("paid_user", "paid@example.com", "Password123")
        uid_paid = int(user_paid["id"])
        self.db.add_user_credits(uid_paid, 10.0, 0.5)

        # Free user has a 40 MB theater (under 50 MB limit)
        self.db.record_deployment("theater_free", uid_free, "KEY-FREE", cost=0.0)
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE theaters SET storage_bytes = ? WHERE theater_id = ?",
                (40 * 1024 * 1024, "theater_free"),
            )
            conn.commit()

        # Paid user has two theaters: 40 MB and 60 MB -> total 100 MB (50 MB excess)
        self.db.record_deployment("theater_paid_1", uid_paid, "KEY-P1", cost=0.0)
        self.db.record_deployment("theater_paid_2", uid_paid, "KEY-P2", cost=0.0)
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE theaters SET storage_bytes = ? WHERE theater_id = ?",
                (40 * 1024 * 1024, "theater_paid_1"),
            )
            conn.cursor().execute(
                "UPDATE theaters SET storage_bytes = ? WHERE theater_id = ?",
                (60 * 1024 * 1024, "theater_paid_2"),
            )
            conn.commit()

        t0 = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.db.set_storage_billing_watermark(t0.isoformat())

        # 1 hour tick
        t1 = t0 + datetime.timedelta(hours=1)
        result: StorageBillingTickResult = self.db.run_storage_billing_tick(current_time=t1)

        self.assertEqual(result["hours_billed"], 1)
        self.assertEqual(result["watermark"], t1.isoformat())
        self.assertEqual(self.db.get_storage_billing_watermark(), t1.isoformat())

        # Free user should not be charged
        free_user_account = self.db.get_user_by_id(uid_free)
        self.assertEqual(free_user_account["credits"], 10.0)

        # Paid user should be charged for 50 MB excess for 1 hour
        chargeable_bytes = (100 * 1024 * 1024) - FREE_USER_THEATER_STORAGE_BYTES
        expected_cost = self.pricing_controller.calculate_storage_cost(
            chargeable_bytes / (1024.0 * 1024.0 * 1024.0), days=1.0 / 24.0
        )
        paid_user_account = self.db.get_user_by_id(uid_paid)
        self.assertAlmostEqual(10.0 - paid_user_account["credits"], expected_cost, places=6)

        self.assertEqual(len(result["charges"]), 1)
        self.assertEqual(result["charges"][0]["user_id"], uid_paid)
        self.assertEqual(result["charges"][0]["chargeable_bytes"], chargeable_bytes)
        self.assertAlmostEqual(result["charges"][0]["amount"], expected_cost, places=6)

    def test_offline_billing_charges_for_elapsed_offline_hours(self) -> None:
        user = self.db.register_user("offline_user", "offline@example.com", "Password123")
        uid = int(user["id"])
        self.db.add_user_credits(uid, 50.0, 2.5)

        # User has 150 MB of storage (100 MB chargeable above 50 MB free allowance)
        self.db.record_deployment("theater_offline", uid, "KEY-OFF", cost=0.0)
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE theaters SET storage_bytes = ? WHERE theater_id = ?",
                (150 * 1024 * 1024, "theater_offline"),
            )
            conn.commit()

        # Watermark was set 6 hours ago (simulating server being offline for 6 hours)
        t0 = datetime.datetime(2026, 11, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        self.db.set_storage_billing_watermark(t0.isoformat())

        # Server comes online 6 hours later
        t_online = t0 + datetime.timedelta(hours=6)
        result: StorageBillingTickResult = self.db.run_storage_billing_tick(current_time=t_online)

        self.assertEqual(result["hours_billed"], 6)
        self.assertEqual(result["watermark"], t_online.isoformat())
        self.assertEqual(self.db.get_storage_billing_watermark(), t_online.isoformat())

        # Should bill for 6 hours of storage usage
        chargeable_bytes = (150 * 1024 * 1024) - FREE_USER_THEATER_STORAGE_BYTES
        expected_cost_6h = self.pricing_controller.calculate_storage_cost(
            chargeable_bytes / (1024.0 * 1024.0 * 1024.0), days=6.0 / 24.0
        )
        updated_user = self.db.get_user_by_id(uid)
        self.assertAlmostEqual(50.0 - updated_user["credits"], expected_cost_6h, places=6)
        self.assertEqual(len(result["charges"]), 1)
        self.assertEqual(result["charges"][0]["hours"], 6)

    def test_idempotent_tick_prevents_double_billing(self) -> None:
        user = self.db.register_user("idem_user", "idem@example.com", "Password123")
        uid = int(user["id"])
        self.db.add_user_credits(uid, 20.0, 1.0)

        self.db.record_deployment("theater_idem", uid, "KEY-IDEM", cost=0.0)
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE theaters SET storage_bytes = ? WHERE theater_id = ?",
                (200 * 1024 * 1024, "theater_idem"),
            )
            conn.commit()

        t0 = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.db.set_storage_billing_watermark(t0.isoformat())

        t1 = t0 + datetime.timedelta(hours=2)
        # First execution
        first_result: StorageBillingTickResult = self.db.run_storage_billing_tick(current_time=t1)
        self.assertEqual(first_result["hours_billed"], 2)
        credits_after_first = self.db.get_user_by_id(uid)["credits"]

        # Immediate retry with same timestamp
        second_result: StorageBillingTickResult = self.db.run_storage_billing_tick(current_time=t1)
        self.assertEqual(second_result["hours_billed"], 0)
        self.assertEqual(second_result["charges"], [])
        credits_after_second = self.db.get_user_by_id(uid)["credits"]

        self.assertEqual(credits_after_first, credits_after_second)

    def test_zero_credits_user_can_deploy_theaters(self) -> None:
        user = self.db.register_user("zero_cred", "zero@example.com", "Password123")
        uid = int(user["id"])
        self.assertEqual(user["credits"], 0.0)

        # Zero credits user can deploy a free theater
        deployed = self.db.record_deployment("theater_zero_1", uid, "KEY-ZERO", cost=0.0)
        self.assertTrue(deployed)
        stored_dep = self.db.get_deployment("theater_zero_1")
        self.assertEqual(stored_dep["theater_id"], "theater_zero_1")
        self.assertEqual(stored_dep["user_id"], uid)

    def test_zero_credits_user_blocked_from_costly_deployment(self) -> None:
        user = self.db.register_user("poor_deployer", "poor-dep@example.com", "Password123")
        uid = int(user["id"])
        self.assertEqual(user["credits"], 0.0)

        with self.assertRaises(ValueError) as ctx:
            self.db.record_deployment("theater_expensive", uid, "KEY-EXP", cost=5.0)
        self.assertIn("insufficient credits", str(ctx.exception).lower())


class TestZeroCreditsApiIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_zero_credits.db"
        self.db = LocalDatabaseManager(str(self.db_path))

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_zero_credits_blocked_from_summoning_agent(self) -> None:
        import importlib
        app_module = importlib.import_module('api_server.app')
        import object_registry
        from unittest.mock import AsyncMock, MagicMock, patch

        user = self.db.register_user("zeroclient", "zeroclient@example.com", "Password123")
        uid = int(user["id"])
        self.assertEqual(user["credits"], 0.0)

        # Deploy theater with 0 credits succeeds
        theater_id = "theater_zero_client_test"
        deployed = self.db.record_deployment(theater_id, uid, "KEY-ZERO-TEST", cost=0.0)
        self.assertTrue(deployed)

        manager = MagicMock()
        mock_req = MagicMock()

        with (
            patch.object(object_registry, "db", self.db),
            patch.object(object_registry, "live_agent_manager", manager),
            patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": uid})),
        ):
            # Summoning agent should fail with 402 insufficient_credits
            response = asyncio.run(app_module.start_agent_endpoint(theater_id, mock_req))
            self.assertEqual(response.status_code, 402)
            payload = response.body.decode("utf-8")
            self.assertIn('"insufficient_credits":true', payload.lower())
            self.assertIn('"agent_running":false', payload.lower())
            manager.stop_session.assert_called_once_with(theater_id=theater_id)

            # Checking agent status should report insufficient_credits
            status_payload = asyncio.run(app_module.get_agent_status_endpoint(theater_id))
            self.assertTrue(status_payload.get("insufficient_credits"))
            self.assertFalse(status_payload.get("agent_running"))


    def test_run_storage_billing_loop_calls_tick_and_exits_on_stop(self) -> None:
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        stop = asyncio.Event()

        async def run_loop_briefly() -> None:
            task = asyncio.create_task(run_storage_billing_loop(stop))
            # Wait briefly to let initial tick run
            await asyncio.sleep(0.05)
            stop.set()
            await task

        with (
            patch("object_registry.db", mock_db),
            patch("object_registry.STORAGE_BILLING_CHECK_INTERVAL_SECONDS", 0.02),
        ):
            asyncio.run(run_loop_briefly())

        self.assertGreaterEqual(mock_db.run_storage_billing_tick.call_count, 1)

    def test_zero_credits_user_can_create_and_deploy_in_theater_editor(self) -> None:

        user = self.db.register_user("builderzero", "builderzero@example.com", "Password123")
        uid = int(user["id"])
        self.assertEqual(user["credits"], 0.0)

        # Record deployment for theater-builder initialized draft with cost=0.0
        theater_id = "theater_builder_zero"
        deployed = self.db.record_deployment(theater_id, uid, "KEY-BUILDER-ZERO", cost=0.0, name="Zero Builder Theater")
        self.assertTrue(deployed)

        dep = self.db.get_deployment(theater_id)
        self.assertIsNotNone(dep)
        self.assertEqual(dep["user_id"], uid)
        self.assertEqual(dep["name"], "Zero Builder Theater")

        # Update name in theater editor deploy/publish
        self.assertTrue(self.db.update_theater_name(theater_id, "Updated Name", user_id=uid))
        updated_dep = self.db.get_deployment(theater_id)
        self.assertEqual(updated_dep["name"], "Updated Name")


if __name__ == "__main__":
    unittest.main()
