import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from testing.base import BaseTestCase
from storage.database import DatabaseManager


class TestDatabaseManager(BaseTestCase):

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_deployer.db"
        self.db = DatabaseManager.from_local(db_path=str(self.db_file))
        self.db._init_db()

    def tearDown(self):
        self.db.close()
        import gc
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_connection_reuse(self):
        conn1 = self.db._get_connection()
        conn2 = self.db._get_connection()
        self.assertIs(conn1, conn2)

        self.db.close()
        self.assertIsNone(self.db._conn)
        conn3 = self.db._get_connection()
        self.assertIsNot(conn1, conn3)

    def test_async_database_methods(self):
        async def _run_async():
            user = await self.db.register_user_async("async_user", "async@example.com", "Pass12345")
            self.assertEqual(user["username"], "async_user")

            token = await self.db.create_auth_session_async(user["id"])
            self.assertTrue(len(token) > 20)

            dep_success = await self.db.record_deployment_async("async_theater_1", user["id"], "KEY-ASYNC")
            self.assertTrue(dep_success)

            view_success = await self.db.record_theater_view_async("async_theater_1", user["id"], "127.0.0.1")
            self.assertTrue(view_success)

            export_success = await self.db.export_theater_to_db_async(
                "async_theater_1",
                {"shown_image_prompt": "Async Prompt"},
                [{"filename": "out.png", "category": "output", "data": b"ASYNC_BYTES"}],
                user_id=user["id"],
                name="Async Session"
            )
            self.assertTrue(export_success)

            inval_success = await self.db.invalidate_session_token_async(token)
            self.assertTrue(inval_success)

        asyncio.run(_run_async())


    def test_pragma_wal_enabled(self):
        with self.db._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

    def test_user_registration_and_authentication(self):
        user = self.db.register_user("testuser", "test@example.com", "SecretPass123")
        self.assertEqual(user["username"], "testuser")
        self.assertEqual(user["credits"], 100.0)

        # Authenticate valid credentials
        auth_user = self.db.authenticate_user("testuser", "SecretPass123")
        self.assertIsNotNone(auth_user)
        self.assertEqual(auth_user["id"], user["id"])

        # Authenticate by email
        auth_email = self.db.authenticate_user("TEST@EXAMPLE.COM", "SecretPass123")
        self.assertIsNotNone(auth_email)

        # Invalid password
        bad_pass = self.db.authenticate_user("testuser", "WrongPassword")
        self.assertIsNone(bad_pass)

    def test_auth_session_tokens(self):
        user = self.db.register_user("tokenuser", "token@example.com", "Password123")
        token = self.db.create_auth_session(user["id"], days_valid=1)
        self.assertTrue(len(token) > 20)

        valid_user = self.db.validate_session_token(token)
        self.assertIsNotNone(valid_user)
        self.assertEqual(valid_user["id"], user["id"])

        # Invalidate token
        success = self.db.invalidate_session_token(token)
        self.assertTrue(success)
        self.assertIsNone(self.db.validate_session_token(token))

    def test_deployment_recording_and_credits(self):
        user = self.db.register_user("deployuser", "deploy@example.com", "Password123")
        join_key = "KEY-TEST12"
        
        success = self.db.record_deployment("theater_test123", user["id"], join_key, cost=5.0)
        self.assertTrue(success)

        # Check updated credits
        updated_user = self.db.get_user_by_id(user["id"])
        self.assertEqual(updated_user["credits"], 95.0)

        # Query by join key
        dep = self.db.get_theater_by_join_key("key-test12")
        self.assertIsNotNone(dep)
        self.assertEqual(dep["theater_id"], "theater_test123")


    def test_export_theater_and_reconstruction(self):
        user = self.db.register_user("exportuser", "export@example.com", "Password123")
        theater_id = "theater_export_test"
        state_data = {
            "shown_image_prompt": "Test Prompt",
            "doodles": [{"type": "draw", "x0": 0.1, "y0": 0.2}],
            "chat_messages": [{"author": "agent", "text": "Hello!"}]
        }
        image_files = [
            {"filename": "test_out.png", "category": "output", "data": b"PNG_DATA"},
            {"filename": "test_ref.jpg", "category": "reference", "data": b"JPG_DATA"},
            # Theater-root files are exported with category "." by CanvasStateManager.
            {"filename": "style.txt", "category": ".", "data": b"moody watercolor"},
        ]

        res = self.db.export_theater_to_db(theater_id, state_data, image_files, user_id=user["id"], name="Export Test")
        self.assertTrue(res)

        exported = self.db.get_exported_theater(theater_id)
        self.assertIsNotNone(exported)
        self.assertEqual(exported["name"], "Export Test")
        self.assertEqual(len(exported["images"]), 3)

        # Test reconstruction into target directory
        recon_dir = Path(self.temp_dir.name) / "reconstructed_theater"
        recon_success = self.db.reconstruct_theater_from_db(theater_id, recon_dir)
        self.assertTrue(recon_success)

        self.assertTrue((recon_dir / "output" / "test_out.png").exists())
        self.assertEqual((recon_dir / "output" / "test_out.png").read_bytes(), b"PNG_DATA")
        self.assertTrue((recon_dir / "references" / "test_ref.jpg").exists())
        self.assertEqual((recon_dir / "references" / "test_ref.jpg").read_bytes(), b"JPG_DATA")
        self.assertEqual((recon_dir / "style.txt").read_text(encoding="utf-8"), "moody watercolor")

    def test_stats_tracking(self):
        user1 = self.db.register_user("statsuser1", "stats1@example.com", "Password123")
        user2 = self.db.register_user("statsuser2", "stats2@example.com", "Password123")

        # Record theater views
        self.db.record_theater_view("theater_alpha", user_id=user1["id"], ip_address="127.0.0.1")
        self.db.record_theater_view("theater_alpha", user_id=user2["id"], ip_address="127.0.0.1")
        self.db.record_theater_view("theater_beta", user_id=None, ip_address="192.168.1.1")

        stats = self.db.get_stats_summary()
        self.assertGreaterEqual(stats["total_accounts"], 2)
        self.assertGreaterEqual(stats["active_users_7d"], 2)
        self.assertEqual(stats["total_theater_views"], 3)
        self.assertEqual(stats["theater_views_7d"], 3)
        self.assertTrue(any(s["theater_id"] == "theater_alpha" and s["views"] == 2 for s in stats["top_viewed_theaters"]))

    def test_password_reset_flow(self):
        user = self.db.register_user("resetuser", "reset@example.com", "OldPassword123")
        
        # Create active session token
        session_token = self.db.create_auth_session(user["id"])
        self.assertIsNotNone(self.db.validate_session_token(session_token))

        # Request reset token by email
        res = self.db.create_password_reset_token("reset@example.com")
        self.assertIsNotNone(res)
        token, u_info = res
        self.assertEqual(u_info["username"], "resetuser")

        # Validate reset token
        val_user = self.db.validate_password_reset_token(token)
        self.assertIsNotNone(val_user)
        self.assertEqual(val_user["username"], "resetuser")

        # Reset password
        success = self.db.reset_password_with_token(token, "NewPassword456")
        self.assertTrue(success)

        # Old password authentication fails
        self.assertIsNone(self.db.authenticate_user("resetuser", "OldPassword123"))

        # New password authentication succeeds
        auth_new = self.db.authenticate_user("resetuser", "NewPassword456")
        self.assertIsNotNone(auth_new)

        # Reusing token fails
        self.assertIsNone(self.db.validate_password_reset_token(token))
        self.assertFalse(self.db.reset_password_with_token(token, "AnotherPassword789"))

        # Old session token invalidated
        self.assertIsNone(self.db.validate_session_token(session_token))

    def test_buy_credits_and_transactions(self):
        user = self.db.register_user("credituser", "credit@example.com", "Password123")
        initial_credits = user["credits"]

        # Add credits (e.g. Pro pack: 200 credits for $18.00)
        res = self.db.add_user_credits(user["id"], 200.0, 18.00, payment_method="card_mock")
        self.assertEqual(res["credits_added"], 200.0)
        self.assertEqual(res["amount_usd"], 18.00)
        self.assertEqual(res["user"]["credits"], initial_credits + 200.0)

        # Check transactions history
        txs = self.db.get_user_transactions(user["id"])
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["credits_added"], 200.0)
        self.assertEqual(txs[0]["amount_usd"], 18.00)
        self.assertEqual(txs[0]["payment_method"], "card_mock")
        self.assertEqual(txs[0]["status"], "completed")

        # Adding invalid/negative credits fails
        with self.assertRaises(ValueError):
            self.db.add_user_credits(user["id"], -10.0, 5.0)


if __name__ == "__main__":
    unittest.main()

