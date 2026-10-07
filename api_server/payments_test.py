"""Unit tests for payment endpoints (mock flow and real flow routing)."""

import os
import shutil
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from absl.testing import flagsaver
from fastapi.testclient import TestClient

from testing.base import BaseTestCase
from api_server import app, FLAGS, db, theater_manager
from api_server.payments import _is_mock_payment_mode
import api_server.payments as payments
import object_registry


class TestPaymentsFlow(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(flagsaver.flagsaver(allow_mock_payments=True, testing_use_local=True))
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
            "age_attested": True,
            "username": self.username,
            "email": self.email,
            "password": "Password123!"
        })
        self.assertEqual(reg_res.status_code, 200)

    def tearDown(self) -> None:
        super().tearDown()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_mock_mode_detection(self):
        """Verify _is_mock_payment_mode returns True under testing flag or mock payment method."""
        self.assertTrue(_is_mock_payment_mode("card_mock"))
        orig_test_flag = FLAGS.testing_use_local
        try:
            FLAGS.testing_use_local = True
            self.assertTrue(_is_mock_payment_mode("anything"))
        finally:
            FLAGS.testing_use_local = orig_test_flag

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
            FLAGS.testing_use_local = False
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
        user = self.client.get("/api/auth/me").json()["user"]
        starting_credits = user["credits"]
        payload = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_webhook_test_123",
                    "payment_status": "paid",
                    "mode": "payment",
                    "currency": "usd",
                    "amount_total": 500,
                    "metadata": {
                        "user_id": str(user["id"]),
                        "package_id": "starter",
                        "credits_to_add": "100.0",
                        "usd_amount": "5.00"
                    }
                }
            }
        }
        res = self.client.post("/api/payments/webhook", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

        duplicate = self.client.post("/api/payments/webhook", json=payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(db.get_user_by_id(user["id"])["credits"], starting_credits + 100.0)

    def test_verify_session_credits_a_checkout_session_once(self):
        """A browser retry cannot credit an already-settled Checkout session."""
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local = False
        user = self.client.get("/api/auth/me").json()["user"]
        starting_credits = user["credits"]
        checkout_session = MagicMock(
            payment_status="paid",
            mode="payment", currency="usd", amount_total=500,
            metadata={"user_id": str(user["id"]), "package_id": "starter", "credits_to_add": "100", "usd_amount": "5"},
        )
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_example"}), \
             patch("api_server.payments.stripe.checkout.Session.retrieve", return_value=checkout_session):
            first = self.client.get("/api/payments/verify-session?session_id=cs_verify_test_123")
            second = self.client.get("/api/payments/verify-session?session_id=cs_verify_test_123")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["credits_added"], 100.0)
        self.assertEqual(second.json()["credits_added"], 0.0)
        self.assertEqual(db.get_user_by_id(user["id"])["credits"], starting_credits + 100.0)

    def test_fixed_packages_and_default_use_configured_prices(self) -> None:
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local = False
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_example"}), \
             patch("api_server.payments.stripe.checkout.Session.create", return_value=MagicMock(id="cs_fixed", url="https://checkout.example/fixed")) as create:
            for package_id in (None, "starter", "pro", "ultra"):
                with self.subTest(package_id=package_id):
                    purchase = {} if package_id is None else {"package_id": package_id}
                    response = self.client.post("/api/payments/buy-credits", json=purchase)
                    self.assertEqual(response.status_code, 200)
                    selected = package_id or "starter"
                    package = payments.CREDIT_PACKAGES[selected]
                    self.assertEqual(create.call_args.kwargs["line_items"], [{"price": package["price_id"], "quantity": 1}])
                    self.assertEqual(create.call_args.kwargs["metadata"]["package_id"], selected)
                    self.assertEqual(create.call_args.kwargs["metadata"]["credits_to_add"], str(package["credits"]))

    def test_custom_purchases_never_reach_stripe(self) -> None:
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local = False
        with patch("api_server.payments.stripe.checkout.Session.create") as create:
            for purchase in (
                {"custom_credits": 100, "custom_usd": 5},
                {"custom_usd": 5}, {"custom_credits": 100},
                {"package_id": "custom"},
                {"package_id": "starter", "custom_credits": 100, "custom_usd": 5},
                {"custom_credits": 1000000, "custom_usd": 0.001},
                {"custom_credits": 1000000, "custom_usd": 0.50},
                {"custom_usd": 0.49}, {"custom_usd": 1.001},
                {"custom_usd": -5}, {"custom_usd": 1000000},
                {"custom_usd": "Infinity"}, {"custom_usd": "NaN"},
                {"custom_credits": "NaN", "custom_usd": 1},
                {"package_id": "unknown", "custom_usd": 5},
            ):
                with self.subTest(purchase=purchase):
                    response = self.client.post("/api/payments/buy-credits", json=purchase)
                    self.assertIn(response.status_code, (400, 422))
            create.assert_not_called()

    def test_settlement_rejects_underpayment_tampered_credits_and_wrong_currency(self) -> None:
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local = False
        user = self.client.get("/api/auth/me").json()["user"]
        for changes in (
            {"amount_total": 50}, {"currency": "eur"}, {"mode": "subscription"},
            {"metadata": {"user_id": str(user["id"]), "package_id": "starter", "credits_to_add": "1000000", "usd_amount": "5"}},
            {"metadata": {"user_id": str(user["id"]), "package_id": "ultra", "credits_to_add": "1000", "usd_amount": "1.25"}},
            {"metadata": {"user_id": str(user["id"]), "package_id": "custom", "credits_to_add": "100", "usd_amount": "5"}},
            {"metadata": {"user_id": str(user["id"]), "credits_to_add": "100", "usd_amount": "5"}},
        ):
            session_data = {
                "id": "cs_invalid", "payment_status": "paid", "mode": "payment",
                "currency": "usd", "amount_total": 500,
                "metadata": {"user_id": str(user["id"]), "package_id": "starter", "credits_to_add": "100", "usd_amount": "5"},
            }
            session_data.update(changes)
            with self.subTest(changes=changes), \
                 patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_example"}), \
                 patch("api_server.payments.stripe.checkout.Session.retrieve", return_value=MagicMock(**session_data)), \
                 patch("api_server.payments.stripe.Webhook.construct_event", return_value={"type": "checkout.session.completed", "data": {"object": session_data}}), \
                 patch("api_server.payments.db.add_stripe_session_credits") as add:
                response = self.client.get("/api/payments/verify-session?session_id=cs_invalid")
                self.assertEqual(response.status_code, 400)
                with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_example"}):
                    webhook = self.client.post("/api/payments/webhook", content=b"event", headers={"stripe-signature": "signature"})
                self.assertEqual(webhook.status_code, 400)
                add.assert_not_called()

    def test_unpaid_webhook_is_ignored_and_delayed_payment_can_settle(self) -> None:
        user = self.client.get("/api/auth/me").json()["user"]
        session_data = {
            "id": "cs_delayed", "payment_status": "unpaid", "mode": "payment",
            "currency": "usd", "amount_total": 500,
            "metadata": {"user_id": str(user["id"]), "package_id": "starter", "credits_to_add": "100", "usd_amount": "5"},
        }
        payload = {"type": "checkout.session.completed", "data": {"object": session_data}}
        with patch("api_server.payments.db.add_stripe_session_credits") as add:
            response = self.client.post("/api/payments/webhook", json=payload)
            self.assertEqual(response.status_code, 200)
            add.assert_not_called()
            session_data["payment_status"] = "paid"
            payload["type"] = "checkout.session.async_payment_succeeded"
            response = self.client.post("/api/payments/webhook", json=payload)
            self.assertEqual(response.status_code, 200)
            add.assert_called_once_with(user["id"], 100.0, 5.0, "cs_delayed", "stripe_webhook")

    def test_direct_payment_checks_actual_received_amount(self) -> None:
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local = False
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_example"}), \
             patch("api_server.payments.stripe.PaymentMethod.create", return_value=MagicMock(id="pm_example")), \
             patch("api_server.payments.stripe.PaymentIntent.create", return_value=MagicMock(status="succeeded", currency="usd", amount_received=1)), \
             patch("api_server.payments.db.add_user_credits") as add:
            response = self.client.post("/api/payments/buy-credits", json={
                "package_id": "starter", "payment_method": "card", "card_number": "4242424242424242",
            })
            self.assertEqual(response.status_code, 400)
            add.assert_not_called()

    def test_webhook_requires_signature_outside_test_mode(self):
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local = False
        response = self.client.post("/api/payments/webhook", json={"type": "checkout.session.completed"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Stripe webhook signature is required.")

    def test_pricing_endpoint_adventure_mode_calculation(self):
        """Verify /api/pricing endpoint returns adventure rates and performs adventure mode cost/token calculations."""
        res = self.client.get("/api/pricing?adventure_actions=10&adventure_minutes=5")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("adventure_mode_tokens_per_call", data)
        self.assertEqual(data["adventure_mode_tokens_per_call"], 4000.0)
        self.assertEqual(data["adventure_mode_calls_per_minute"], 5.0)

        calc = data.get("calculation", {})
        # 10 actions + (5 mins * 5 calls/min = 25 actions) = 35 total actions
        # 35 * 0.5 = 17.5 credits
        self.assertAlmostEqual(calc["adventure_mode_credits"], 17.5)
        # 35 * 4000 = 140,000 tokens
        self.assertEqual(calc["adventure_mode_estimated_tokens"], 140000)
        self.assertAlmostEqual(calc["usage_credits"], 5.0)

    def test_pricing_endpoint_includes_story_and_voiced_turns(self):
        res = self.client.get("/api/pricing?story_plans=4&character_voiced_turns=3")
        self.assertEqual(res.status_code, 200)
        # 4 planner turns * 0.5 credits + 3 voiced turns * 0.25 credits.
        self.assertAlmostEqual(res.json()["calculation"]["usage_credits"], 2.75)

    def test_pricing_endpoint_includes_interactive_canvas(self):
        res = self.client.get("/api/pricing?interactive_canvas_used=4")
        self.assertEqual(res.status_code, 200)
        # 4 interactive canvas uses * 0.25 credits = 1.0 credit.
        self.assertAlmostEqual(res.json()["calculation"]["usage_credits"], 1.0)
        self.assertEqual(res.json()["interactive_canvas_credit_rate"], 0.25)



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
