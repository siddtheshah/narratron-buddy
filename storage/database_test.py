"""Unit tests for storage/database.py covering non-trivial logic, edge cases, and async methods."""

import asyncio
import datetime
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from testing.base import BaseTestCase
from storage.database import (
    DatabaseManager,
    _DictCursor,
    _ReusableConnection,
)


class TestDictCursorAndReusableConnection(unittest.TestCase):
    """Test lower-level helper wrappers: _DictCursor and _ReusableConnection."""

    def test_dict_cursor_with_description(self):
        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None), ("username", None), ("credits", None)]
        mock_cursor.fetchone.return_value = (1, "alice", 50.0)
        mock_cursor.fetchall.return_value = [(1, "alice", 50.0), (2, "bob", 100.0)]

        cursor = _DictCursor(mock_cursor)
        cursor.execute("SELECT * FROM users")
        mock_cursor.execute.assert_called_once_with("SELECT * FROM users", ())

        row = cursor.fetchone()
        self.assertEqual(row, {"id": 1, "username": "alice", "credits": 50.0})

        rows = cursor.fetchall()
        self.assertEqual(
            rows,
            [
                {"id": 1, "username": "alice", "credits": 50.0},
                {"id": 2, "username": "bob", "credits": 100.0},
            ],
        )

    def test_dict_cursor_none_result_and_none_description(self):
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_cursor.fetchone.return_value = None

        cursor = _DictCursor(mock_cursor)
        self.assertIsNone(cursor.fetchone())

        # Test when description is None but fetchone returns raw data
        mock_cursor.fetchone.return_value = (1, 2, 3)
        self.assertEqual(cursor.fetchone(), (1, 2, 3))

    def test_dict_cursor_attribute_delegation(self):
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        cursor = _DictCursor(mock_cursor)
        self.assertEqual(cursor.lastrowid, 42)

    def test_reusable_connection_context_manager_normal(self):
        mock_conn = MagicMock()
        reusable = _ReusableConnection(mock_conn, is_dict_cursor=True)

        # Context manager normal exit -> commit without closing
        with reusable as conn:
            c = conn.cursor()
            self.assertIsInstance(c, _DictCursor)

        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_not_called()

    def test_reusable_connection_context_manager_exception(self):
        mock_conn = MagicMock()
        reusable = _ReusableConnection(mock_conn, is_dict_cursor=False)

        with self.assertRaises(RuntimeError):
            with reusable:
                raise RuntimeError("Something failed")

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()
        mock_conn.close.assert_not_called()

    def test_reusable_connection_exception_handling(self):
        mock_conn = MagicMock()
        mock_conn.rollback.side_effect = Exception("Rollback error")
        mock_conn.close.side_effect = Exception("Close error")
        reusable = _ReusableConnection(mock_conn)

        # Ensure rollback and close exception handling inside _ReusableConnection doesn't crash
        reusable.rollback()
        reusable.close()


class TestDatabaseManagerConnectionAndMigration(BaseTestCase):
    """Test connection management, fallback to local SQLite, and table migration logic."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_db.db"
        self.db = DatabaseManager.from_local(str(self.db_file))

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_from_live_and_from_local_constructors(self):
        local_db = DatabaseManager.from_local("local.db")
        self.assertFalse(local_db.is_live)
        self.assertEqual(local_db.db_path, "local.db")

        live_db = DatabaseManager.from_live()
        self.assertTrue(live_db.is_live)
        self.assertIsNone(live_db.db_path)

    def test_connection_caching_and_switching(self):
        conn1 = self.db._get_connection()
        self.assertIsNotNone(conn1)

        # Same settings -> returns cached connection
        conn2 = self.db._get_connection()
        self.assertIs(conn1, conn2)

        # Switch db_path -> closes previous connection and opens new connection
        new_db_file = Path(self.temp_dir.name) / "test_db_2.db"
        self.db.db_path = str(new_db_file)
        conn3 = self.db._get_connection()
        self.assertIsNot(conn1, conn3)

    def test_migration_recreates_users_table_if_missing_id(self):
        # Create an old schema 'users' table without 'id'
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DROP TABLE IF EXISTS users")
            cursor.execute("CREATE TABLE users (username TEXT PRIMARY KEY, email TEXT)")
            conn.commit()

        # Triggering _init_db should drop the malformed users table and recreate proper schema
        self.db._init_db()
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(users)")
            cols = [r["name"] for r in cursor.fetchall()]
            self.assertIn("id", cols)
            self.assertIn("credits", cols)
            self.assertIn("last_active_at", cols)


class TestUserManagementAndAuth(BaseTestCase):
    """Test user registration, authentication, validation, and error edge cases."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_user_auth.db"
        self.db = DatabaseManager.from_local(str(self.db_file))

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_register_user_validation_errors(self):
        with self.assertRaises(ValueError) as ctx:
            self.db.register_user("  ", "email@test.com", "pass")
        self.assertIn("required", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            self.db.register_user("username", "", "pass")
        self.assertIn("required", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            self.db.register_user("username", "email@test.com", "")
        self.assertIn("required", str(ctx.exception).lower())

    def test_register_user_duplicate_username_and_email(self):
        self.db.register_user("user_one", "user1@test.com", "Pass12345")

        # Duplicate username
        with self.assertRaises(ValueError) as ctx:
            self.db.register_user("user_one", "different@test.com", "Pass12345")
        self.assertIn("username already exists", str(ctx.exception).lower())

        # Duplicate email (case-insensitive)
        with self.assertRaises(ValueError) as ctx:
            self.db.register_user("user_two", "USER1@TEST.COM", "Pass12345")
        self.assertIn("email already registered", str(ctx.exception).lower())

    def test_authenticate_user_edge_cases(self):
        self.db.register_user("AuthUser", "auth@test.com", "CorrectPassword")

        # Empty inputs
        self.assertIsNone(self.db.authenticate_user("", "CorrectPassword"))
        self.assertIsNone(self.db.authenticate_user("AuthUser", ""))

        # Non-existent user
        self.assertIsNone(self.db.authenticate_user("NonExistent", "CorrectPassword"))

        # Wrong password
        self.assertIsNone(self.db.authenticate_user("AuthUser", "WrongPassword"))

        # Valid auth by username (case-insensitive)
        user_by_name = self.db.authenticate_user("authuser", "CorrectPassword")
        self.assertIsNotNone(user_by_name)
        self.assertEqual(user_by_name["username"], "AuthUser")

        # Valid auth by email (case-insensitive)
        user_by_email = self.db.authenticate_user("AUTH@TEST.COM", "CorrectPassword")
        self.assertIsNotNone(user_by_email)
        self.assertEqual(user_by_email["email"], "auth@test.com")

    def test_get_user_by_id(self):
        user = self.db.register_user("iduser", "id@test.com", "Pass12345")
        found = self.db.get_user_by_id(user["id"])
        self.assertIsNotNone(found)
        self.assertEqual(found["username"], "iduser")
        self.assertEqual(found.get("mic_sensitivity"), 0.5)

        self.assertIsNone(self.db.get_user_by_id(99999))

    def test_update_user_mic_sensitivity(self):
        user = self.db.register_user("micuser", "mic@test.com", "Pass12345")
        self.assertEqual(user.get("mic_sensitivity"), 0.5)

        success = self.db.update_user_mic_sensitivity(user["id"], 0.85)
        self.assertTrue(success)

        found = self.db.get_user_by_id(user["id"])
        self.assertEqual(found.get("mic_sensitivity"), 0.85)


class TestAuthSessionsAndActivity(BaseTestCase):
    """Test auth session creation, expiration, validation, activity recording, and token deletion."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_sessions.db"
        self.db = DatabaseManager.from_local(str(self.db_file))
        self.user = self.db.register_user("sessuser", "sess@test.com", "Pass12345")

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_validate_session_token_empty_or_invalid(self):
        self.assertIsNone(self.db.validate_session_token(""))
        self.assertIsNone(self.db.validate_session_token("non_existent_token"))

    def test_validate_session_token_expired(self):
        token = self.db.create_auth_session(self.user["id"], days_valid=1)

        # Manually set expires_at in the past
        past_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat()
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE auth_sessions SET expires_at = ? WHERE token = ?",
                (past_time, token)
            )
            conn.commit()

        # Validation should return None and delete the expired token
        self.assertIsNone(self.db.validate_session_token(token))

        with self.db._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM auth_sessions WHERE token = ?", (token,)).fetchone()
            self.assertIsNone(row)

    def test_validate_session_token_record_activity_toggle(self):
        token = self.db.create_auth_session(self.user["id"])
        res = self.db.validate_session_token(token, record_activity=False)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], self.user["id"])

    def test_record_user_activity(self):
        self.assertFalse(self.db.record_user_activity(0))
        self.assertTrue(self.db.record_user_activity(self.user["id"]))

    def test_invalidate_session_token_missing(self):
        self.assertFalse(self.db.invalidate_session_token("fake_token"))


class TestPasswordResetFlowEdgeCases(BaseTestCase):
    """Test password reset token generation, validation, expiration, and password reset."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_reset.db"
        self.db = DatabaseManager.from_local(str(self.db_file))
        self.user = self.db.register_user("resetuser", "reset@test.com", "OldPassword123")

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_create_password_reset_token_invalid_inputs(self):
        self.assertIsNone(self.db.create_password_reset_token(""))
        self.assertIsNone(self.db.create_password_reset_token("nonexistent@test.com"))

    def test_validate_password_reset_token_expired(self):
        res = self.db.create_password_reset_token("reset@test.com", minutes_valid=15)
        self.assertIsNotNone(res)
        token, _ = res

        # Force token expiration
        past_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)).isoformat()
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE password_reset_tokens SET expires_at = ? WHERE token = ?",
                (past_time, token)
            )
            conn.commit()

        self.assertIsNone(self.db.validate_password_reset_token(token))

    def test_reset_password_with_empty_password(self):
        res = self.db.create_password_reset_token("resetuser")
        self.assertIsNotNone(res)
        token, _ = res
        self.assertFalse(self.db.reset_password_with_token(token, ""))


class TestDeploymentCreditsAndPersistence(BaseTestCase):
    """Test credit operations, deployment recording, persistence toggles, and deletion."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_dep.db"
        self.db = DatabaseManager.from_local(str(self.db_file))
        self.user = self.db.register_user("depuser", "dep@test.com", "Pass12345")

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_record_deployment_insufficient_credits(self):
        # User has default 25 credits; recording deployment costing 150 should fail
        with self.assertRaises(ValueError) as ctx:
            self.db.record_deployment("expensive_theater", self.user["id"], "KEY-EXP", cost=150.0)
        self.assertIn("insufficient credits", str(ctx.exception).lower())

    def test_add_user_credits_validation(self):
        with self.assertRaises(ValueError) as ctx:
            self.db.add_user_credits(self.user["id"], 0.0, 10.0)
        self.assertIn("positive", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            self.db.add_user_credits(99999, 50.0, 10.0)
        self.assertIn("not found", str(ctx.exception).lower())

    def test_delete_deployment(self):
        self.db.record_deployment("theater_to_delete", self.user["id"], "KEY-DEL")
        self.assertIsNotNone(self.db.get_deployment("theater_to_delete"))

        self.assertTrue(self.db.delete_deployment("theater_to_delete"))
        self.assertIsNone(self.db.get_deployment("theater_to_delete"))

        # Deleting non-existent deployment returns False
        self.assertFalse(self.db.delete_deployment("theater_to_delete"))

    def test_set_and_get_theater_persistence_edge_cases(self):
        # Non-existent theater
        self.assertFalse(self.db.set_theater_persistence("fake_theater", True))
        self.assertFalse(self.db.get_theater_persistence("fake_theater"))

        # Valid theater
        self.db.record_deployment("theater_pers", self.user["id"], "KEY-PERS", cost=0.0, is_persistent=False)
        self.assertFalse(self.db.get_theater_persistence("theater_pers"))

        # Enable persistence -> last_billed_at should be populated
        self.assertTrue(self.db.set_theater_persistence("theater_pers", True))
        self.assertTrue(self.db.get_theater_persistence("theater_pers"))
        dep = self.db.get_deployment("theater_pers")
        self.assertIsNotNone(dep["last_billed_at"])

    def test_record_user_usage_default_pricing_and_totals(self):
        # Initial user has 25.0 credits, 0.0 voice mins, 0 images created
        res = self.db.record_user_usage(self.user["id"], voice_minutes=15.5, images_created=4)
        self.assertEqual(res["total_voice_minutes"], 15.5)
        self.assertEqual(res["total_images_created"], 4)
        # Default cost: 15.5 + 4 = 19.5 credits deducted -> 25.0 - 19.5 = 5.5
        self.assertEqual(res["credits"], 5.5)

        # Record subsequent usage
        res2 = self.db.record_user_usage(self.user["id"], voice_minutes=10.0, images_created=2, credit_cost=5.0)
        self.assertEqual(res2["total_voice_minutes"], 25.5)
        self.assertEqual(res2["total_images_created"], 6)
        # Explicit cost 5.0 deducted -> 5.5 - 5.0 = 0.5
        self.assertEqual(res2["credits"], 0.5)

    def test_record_user_usage_negative_credits_allowed(self):
        # User has 25.0 credits; deduct 150.0 credits -> credits should become -125.0
        res = self.db.record_user_usage(self.user["id"], voice_minutes=100.0, images_created=50, credit_cost=150.0)
        self.assertEqual(res["credits"], -125.0)
        self.assertEqual(res["total_voice_minutes"], 100.0)
        self.assertEqual(res["total_images_created"], 50)

    def test_record_user_usage_validation_errors(self):
        with self.assertRaises(ValueError) as ctx:
            self.db.record_user_usage(self.user["id"], voice_minutes=-1.0, images_created=5)
        self.assertIn("non-negative", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            self.db.record_user_usage(self.user["id"], voice_minutes=5.0, images_created=-2)
        self.assertIn("non-negative", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            self.db.record_user_usage(self.user["id"], voice_minutes=5.0, images_created=2, credit_cost=-10.0)
        self.assertIn("non-negative", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            self.db.record_user_usage(99999, voice_minutes=5.0, images_created=2)
        self.assertIn("not found", str(ctx.exception).lower())


class TestBatonManagement(BaseTestCase):
    """Test multi-orator allowed list, baton request, accept, decline, and take back operations."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_baton.db"
        self.db = DatabaseManager.from_local(str(self.db_file))
        self.owner = self.db.register_user("owner_user", "owner@test.com", "Pass12345")
        self.orator1 = self.db.register_user("orator_one", "orator1@test.com", "Pass12345")
        self.orator2 = self.db.register_user("orator_two", "orator2@test.com", "Pass12345")
        self.theater_id = "baton_theater_1"
        self.db.record_deployment(self.theater_id, self.owner["id"], "KEY-BATON")

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_get_baton_state_nonexistent_theater(self):
        self.assertIsNone(self.db.get_theater_baton_state("non_existent_theater"))

    def test_add_allowed_orator_permissions_and_idempotency(self):
        # Non-owner attempting to add orator
        with self.assertRaises(ValueError) as ctx:
            self.db.add_allowed_orator(self.theater_id, owner_id=self.orator1["id"], target_user_id=self.orator2["id"])
        self.assertIn("owner", str(ctx.exception).lower())

        # Target user does not exist
        with self.assertRaises(ValueError) as ctx:
            self.db.add_allowed_orator(self.theater_id, owner_id=self.owner["id"], target_user_id=99999)
        self.assertIn("exist", str(ctx.exception).lower())

        # Owner adds orator1
        state = self.db.add_allowed_orator(self.theater_id, owner_id=self.owner["id"], target_user_id=self.orator1["id"])
        self.assertEqual(len(state["allowed_orators"]), 1)
        self.assertEqual(state["allowed_orators"][0]["username"], "orator_one")

        # Idempotent add (adding orator1 again should not duplicate)
        state2 = self.db.add_allowed_orator(self.theater_id, owner_id=self.owner["id"], target_user_id=self.orator1["id"])
        self.assertEqual(len(state2["allowed_orators"]), 1)

    def test_remove_allowed_orator_and_active_orator_reset(self):
        self.db.add_allowed_orator(self.theater_id, self.owner["id"], self.orator1["id"])

        # Non-owner cannot remove
        with self.assertRaises(ValueError):
            self.db.remove_allowed_orator(self.theater_id, owner_id=self.orator1["id"], target_user_id=self.orator1["id"])

        # Directly set orator1 as active orator in DB
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET active_orator_id = ? WHERE theater_id = ?",
                (self.orator1["id"], self.theater_id)
            )
            conn.commit()

        # Remove orator1 -> active orator should automatically reset to owner
        state = self.db.remove_allowed_orator(self.theater_id, owner_id=self.owner["id"], target_user_id=self.orator1["id"])
        self.assertEqual(len(state["allowed_orators"]), 0)
        self.assertEqual(state["active_orator"]["id"], self.owner["id"])

    def test_request_accept_decline_and_take_back_baton(self):
        self.db.add_allowed_orator(self.theater_id, self.owner["id"], self.orator1["id"])

        # Request baton for target not in allowed orators
        with self.assertRaises(ValueError):
            self.db.request_baton(self.theater_id, owner_id=self.owner["id"], target_user_id=self.orator2["id"])

        # Request baton by non-owner
        with self.assertRaises(ValueError):
            self.db.request_baton(self.theater_id, owner_id=self.orator1["id"], target_user_id=self.orator1["id"])

        # Owner requests baton to orator1
        state = self.db.request_baton(self.theater_id, owner_id=self.owner["id"], target_user_id=self.orator1["id"], timeout_seconds=60)
        self.assertIsNotNone(state["baton_request"])
        self.assertEqual(state["baton_request"]["target_user_id"], self.orator1["id"])

        # Wrong user attempts to accept baton
        with self.assertRaises(ValueError):
            self.db.accept_baton(self.theater_id, target_user_id=self.orator2["id"])

        # orator1 accepts baton
        state_accepted = self.db.accept_baton(self.theater_id, target_user_id=self.orator1["id"])
        self.assertEqual(state_accepted["active_orator"]["id"], self.orator1["id"])
        self.assertIsNone(state_accepted["baton_request"])

        # Owner takes back baton
        state_taken = self.db.take_back_baton(self.theater_id, owner_id=self.owner["id"])
        self.assertEqual(state_taken["active_orator"]["id"], self.owner["id"])

        # Owner requests baton again, then declines
        self.db.request_baton(self.theater_id, owner_id=self.owner["id"], target_user_id=self.orator1["id"])
        state_declined = self.db.decline_baton(self.theater_id, target_user_id=self.orator1["id"])
        self.assertIsNone(state_declined["baton_request"])

    def test_get_baton_state_auto_expires_old_request(self):
        self.db.add_allowed_orator(self.theater_id, self.owner["id"], self.orator1["id"])
        self.db.request_baton(self.theater_id, self.owner["id"], self.orator1["id"], timeout_seconds=1)

        # Set baton request expiration in past
        past_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)).isoformat()
        with self.db._get_connection() as conn:
            dep = conn.cursor().execute("SELECT baton_request FROM canvas_deployments WHERE theater_id = ?", (self.theater_id,)).fetchone()
            import json
            req_dict = json.loads(dep["baton_request"])
            req_dict["expires_at"] = past_time
            conn.cursor().execute(
                "UPDATE canvas_deployments SET baton_request = ? WHERE theater_id = ?",
                (json.dumps(req_dict), self.theater_id)
            )
            conn.commit()

        # Querying state should detect expired request and auto-decline it
        state = self.db.get_theater_baton_state(self.theater_id)
        self.assertIsNone(state["baton_request"])


class TestTheaterExportAndReconstruction(BaseTestCase):
    """Test metadata querying, export to database, and reconstructing theater file structures."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_export.db"
        self.db = DatabaseManager.from_local(str(self.db_file))
        self.user = self.db.register_user("expuser", "exp@test.com", "Pass12345")

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_get_theater_by_join_key(self):
        self.db.record_deployment("join_key_theater", self.user["id"], "My-Custom-Key")
        found = self.db.get_theater_by_join_key("  my-custom-key ")
        self.assertIsNotNone(found)
        self.assertEqual(found["theater_id"], "join_key_theater")

        self.assertIsNone(self.db.get_theater_by_join_key("UNKNOWN-KEY"))

    def test_get_all_exported_theater_ids(self):
        self.db.record_deployment("theater_in_deployments", self.user["id"], "KEY-1")
        self.db.export_theater_to_db("theater_in_exports", {}, [], user_id=self.user["id"])

        ids = self.db.get_all_exported_theater_ids()
        self.assertIn("theater_in_deployments", ids)
        self.assertIn("theater_in_exports", ids)

    def test_get_theater_metadata_from_db_missing_and_fallback(self):
        # Non-existent theater
        self.assertIsNone(self.db.get_theater_metadata_from_db("missing_theater"))

        # Deployment exists, but no exported_theaters record -> fallback metadata dict
        self.db.record_deployment("dep_only_theater", self.user["id"], "KEY-DEP")
        meta = self.db.get_theater_metadata_from_db("dep_only_theater")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["theater_id"], "dep_only_theater")
        self.assertEqual(meta["join_key"], "KEY-DEP")

    def test_reconstruct_theater_from_db_non_existent(self):
        target_dir = Path(self.temp_dir.name) / "non_existent_recon"
        self.assertFalse(self.db.reconstruct_theater_from_db("ghost_theater", target_dir))

    def test_reconstruct_theater_with_legacy_file_unlinking_and_categories(self):
        theater_id = "recon_category_theater"
        state_data = {
            "metadata": {"name": "Category Theater", "join_key": "KEY-CAT"},
            "shown_image_prompt": "Prompt test"
        }
        images = [
            {"filename": "out1.png", "category": "output", "data": b"OUT_DATA"},
            {"filename": "ref1.jpg", "category": "references", "data": b"REF_DATA"},
            {"filename": "custom.txt", "category": "custom_cat", "data": b"CUSTOM_DATA"},
            {"filename": "chat.json", "category": "chats/room1", "data": b"CHAT_DATA"},
        ]
        self.db.export_theater_to_db(theater_id, state_data, images, user_id=self.user["id"])

        recon_dir = Path(self.temp_dir.name) / "recon_cat"
        recon_dir.mkdir(parents=True, exist_ok=True)
        legacy_file = recon_dir / "theater_state.json"
        legacy_file.write_text("{}", encoding="utf-8")

        res = self.db.reconstruct_theater_from_db(theater_id, recon_dir)
        self.assertTrue(res)

        # Legacy file should have been unlinked
        self.assertFalse(legacy_file.exists())

        # theater.json created
        self.assertTrue((recon_dir / "theater.json").exists())

        # Category files placed correctly
        self.assertTrue((recon_dir / "output" / "out1.png").exists())
        self.assertTrue((recon_dir / "references" / "ref1.jpg").exists())
        self.assertTrue((recon_dir / "custom_cat" / "custom.txt").exists())
        self.assertTrue((recon_dir / "chats" / "room1" / "chat.json").exists())


class TestDatabaseDaemonLogic(BaseTestCase):
    """Test edge cases in DatabaseDaemon / run_database_daemon logic."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_daemon.db"
        self.db = DatabaseManager.from_local(str(self.db_file))
        self.user = self.db.register_user("daemonuser", "daemon@test.com", "Pass12345")

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_run_database_daemon_with_naive_datetime_and_local_deployer(self):
        now_naive = datetime.datetime.now()

        # Non-persistent theater older than 1 hour
        old_time = (now_naive - datetime.timedelta(hours=5)).isoformat()
        self.db.record_deployment("daemon_temp_theater", self.user["id"], "KEY-T")
        with self.db._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE canvas_deployments SET created_at = ? WHERE theater_id = ?",
                (old_time, "daemon_temp_theater")
            )
            conn.commit()

        mock_deployer = MagicMock()
        mock_deployer.destroy_theater.side_effect = Exception("File locked error")

        # Running database daemon with naive current_time and mock local_deployer
        res = self.db.storage_daemon(
            local_deployer=mock_deployer,
            ttl_seconds=3600.0,
            hourly_cost=1.0,
            current_time=now_naive
        )

        self.assertIn("daemon_temp_theater", res["cleaned_up_sessions"])
        mock_deployer.destroy_theater.assert_called_once_with("daemon_temp_theater")


class TestAsyncDatabaseMethods(BaseTestCase):
    """Test all async wrapper methods on DatabaseManager."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_async.db"
        self.db = DatabaseManager.from_local(str(self.db_file))

    def tearDown(self):
        self.db.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        super().tearDown()

    def test_all_async_methods(self):
        async def _run():
            # register_user_async
            user = await self.db.register_user_async("async_hero", "hero@test.com", "Pass12345")
            self.assertEqual(user["username"], "async_hero")

            # create_auth_session_async & validate
            token = await self.db.create_auth_session_async(user["id"])
            self.assertTrue(len(token) > 10)

            # record_deployment_async
            dep_ok = await self.db.record_deployment_async("async_theater", user["id"], "KEY-ASYNC")
            self.assertTrue(dep_ok)

            # record_theater_view_async
            view_ok = await self.db.record_theater_view_async("async_theater", user["id"], "127.0.0.1")
            self.assertTrue(view_ok)

            # add_user_credits_async
            credit_res = await self.db.add_user_credits_async(user["id"], 50.0, 5.0)
            self.assertEqual(credit_res["credits_added"], 50.0)

            # record_user_usage_async
            usage_res = await self.db.record_user_usage_async(user["id"], voice_minutes=5.0, images_created=2)
            self.assertEqual(usage_res["total_voice_minutes"], 5.0)
            self.assertEqual(usage_res["total_images_created"], 2)

            # export_theater_to_db_async
            exp_ok = await self.db.export_theater_to_db_async("async_theater", {"prompt": "test"}, [], user_id=user["id"])
            self.assertTrue(exp_ok)

            # persist_canvas_theater_async
            mock_canvas_states = {
                "async_theater": MagicMock(export_theater_data=MagicMock(return_value=({"prompt": "p"}, [])))
            }
            mock_deployer = MagicMock(_get_theater_dir=MagicMock(return_value=Path(self.temp_dir.name)))
            persist_ok = await self.db.persist_canvas_theater_async(
                canvas_states=mock_canvas_states,
                local_deployer=mock_deployer,
                theater_id="async_theater",
                user_id=user["id"],
                name="Async Theater"
            )
            self.assertTrue(persist_ok)

            # invalidate_session_token_async
            inval_ok = await self.db.invalidate_session_token_async(token)
            self.assertTrue(inval_ok)

            # password reset async
            reset_token, _ = self.db.create_password_reset_token("hero@test.com")
            reset_ok = await self.db.reset_password_with_token_async(reset_token, "NewPass12345")
            self.assertTrue(reset_ok)

            # Baton async methods
            baton_user = await self.db.register_user_async("baton_hero", "baton@test.com", "Pass12345")
            await self.db.add_allowed_orator_async("async_theater", user["id"], baton_user["id"])
            baton_state = await self.db.get_theater_baton_state_async("async_theater")
            self.assertEqual(len(baton_state["allowed_orators"]), 1)

            await self.db.request_baton_async("async_theater", user["id"], baton_user["id"])
            await self.db.accept_baton_async("async_theater", baton_user["id"])
            await self.db.take_back_baton_async("async_theater", user["id"])
            await self.db.request_baton_async("async_theater", user["id"], baton_user["id"])
            await self.db.decline_baton_async("async_theater", baton_user["id"])
            await self.db.remove_allowed_orator_async("async_theater", user["id"], baton_user["id"])

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
