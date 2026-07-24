import os
from pathlib import Path
import tempfile
import unittest

from deployer.database import DatabaseManager


class TestDatabaseManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_deployer.db"
        self.db = DatabaseManager(db_path=str(self.db_file))

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass


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
        
        success = self.db.record_deployment("session_test123", user["id"], join_key, cost=5.0)
        self.assertTrue(success)

        # Check updated credits
        updated_user = self.db.get_user_by_id(user["id"])
        self.assertEqual(updated_user["credits"], 95.0)

        # Query by join key
        dep = self.db.get_session_by_join_key("key-test12")
        self.assertIsNotNone(dep)
        self.assertEqual(dep["session_id"], "session_test123")


if __name__ == "__main__":
    unittest.main()
