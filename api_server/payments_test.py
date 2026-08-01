"""Unit tests for payment endpoints (mock flow and real flow routing)."""

import os
import shutil
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from testing.base import BaseTestCase
from api_server import app, FLAGS, db, theater_manager
from api_server.payments import _is_mock_payment_mode
import api_server.payments as payments
import object_registry


class TestPaymentsFlow(BaseTestCase):
    def setUp(self):
        super().setUp()
        FLAGS.allow_mock_payments = True
        FLAGS.testing_use_local_database = True
        self.test_dir = tempfile.mkdtemp()
        theater_manager.base_dir = Path(self.test_dir).resolve()
        theater_manager.base_dir.mkdir(parents=True, exist_ok=True)
        db.is_live = False
        db.db_path = Path(self.test_dir) / "test_payments.db"
        db._init_db()

        self.client = TestClient(app)

        # Register a test user
        self.username = f"payuser_{os.urandom(4).hex()}"
        self.email = f"{self.username}@example.com"
        reg_res = self.client.post("/api/auth/register", json={
            "username": self.username,
            "email": self.email,
            "password": "Password123!"
        })
        self.assertEqual(reg_res.status_code, 200)

    def tearDown(self):
        super().tearDown()
        if hasattr(self, "test_dir") and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_mock_mode_detection(self):
        """Verify _is_mock_payment_mode returns True under testing flag or mock payment method."""
        self.assertTrue(_is_mock_payment_mode("card_mock"))
        orig_test_flag = FLAGS.testing_use_local_database
        try:
            FLAGS.testing_use_local_database = True
            self.assertTrue(_is_mock_payment_mode("anything"))
        finally:
            FLAGS.testing_use_local_database = orig_test_flag

    def test_buy_credits_mock_success(self):
        """Verify buy_credits completes mock purchase and adds user credits."""
        res = self.client.post("/api/payments/buy-credits", json={
            "package_id": "starter",
            "card_number": "4242424242424242",
            "card_exp": "12/28",
            "card_cvc": "123",
            "card_name": "Test User",
            "payment_method": "card_mock"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["credits_added"], 100.0)

    def test_package_checkout_uses_configured_stripe_price_id(self):
        """Predefined packs must use Stripe Price objects, never ad-hoc prices."""
        checkout_session = MagicMock(id="cs_pro", url="https://checkout.example/pro")
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_example"}), \
             patch("api_server.payments.stripe.checkout.Session.create", return_value=checkout_session) as create:
            FLAGS.allow_mock_payments = False
            FLAGS.testing_use_local_database = False
            response = self.client.post("/api/payments/buy-credits", json={
                "package_id": "pro", "payment_method": "stripe_checkout",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs["line_items"], [{
            "price": "price_1TzbisRjBSgVFVM6Jmyk0IcL", "quantity": 1,
        }])
        self.assertNotIn("payment_method_types", create.call_args.kwargs)

    def test_verify_session_endpoint(self):
        """Verify verify-session endpoint handles mock and unauthenticated calls."""
        anon_client = TestClient(app)
        res = anon_client.get("/api/payments/verify-session?session_id=cs_test_123")
        self.assertEqual(res.status_code, 401)

        res2 = self.client.get("/api/payments/verify-session?session_id=cs_test_123")
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json()["verified"])

    def test_stripe_webhook_handler(self):
        """Verify stripe webhook endpoint receives checkout.session.completed event."""
        payload = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {
                        "user_id": "1",
                        "credits_to_add": "50.0",
                        "usd_amount": "2.50"
                    }
                }
            }
        }
        res = self.client.post("/api/payments/webhook", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()


def test_payment_history_reads_transactions_from_registry_database():
    registry_db = MagicMock()
    registry_db.get_user_transactions.return_value = [{"credits_added": 100.0}]
    request = MagicMock()
    with patch.object(object_registry, "db", registry_db), patch.object(payments, "get_current_user", return_value={"id": 7}):
        result = payments.get_payment_history(request)
    assert result == {"status": "ok", "transactions": [{"credits_added": 100.0}]}
    registry_db.get_user_transactions.assert_called_once_with(7)
