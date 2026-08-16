"""Unit tests for pricing/pricing_controller.py."""

import os
import unittest
from unittest.mock import patch

from testing.base import BaseTestCase
from pricing.pricing_controller import PricingController
"""Unit tests for pricing/pricing_controller.py."""

import os
import unittest
from unittest.mock import patch

from testing.base import BaseTestCase
from pricing.pricing_controller import PricingController
from storage.database import LocalDatabaseManager


class TestPricingController(BaseTestCase):
    """Test PricingController initialization, env overrides, cost calculation, and rates polling."""

    def test_default_initialization(self):
        controller = PricingController()
        rates = controller.get_rates()
        self.assertEqual(rates["voice_credit_rate"], 1.0)
        self.assertEqual(rates["image_credit_rate"], 1.0)
        self.assertEqual(rates["music_credit_rate"], 2.0)
        self.assertEqual(rates["story_planning_credit_rate"], 0.5)
        self.assertEqual(rates["storage_gb_monthly_rate"], 1.0)
        self.assertEqual(rates["storage_gb_daily_rate"], 0.033)
        self.assertEqual(rates["credits_per_usd"], 20.0)
        self.assertEqual(rates["usd_per_credit"], 0.05)
        self.assertEqual(rates["adventure_mode_tokens_per_call"], 4000.0)
        self.assertEqual(rates["adventure_mode_calls_per_minute"], 5.0)
        self.assertEqual(rates["adventure_mode_credit_rate_per_action"], 0.5)
        self.assertEqual(rates["adventure_mode_credit_rate_per_minute"], 2.5)

    def test_from_env_overrides(self):
        env_vars = {
            "VOICE_CREDIT_RATE": "2.5",
            "IMAGE_CREDIT_RATE": "0.5",
            "MUSIC_CREDIT_RATE": "1.5",
            "STORY_PLANNING_CREDIT_RATE": "0.75",
            "STORAGE_GB_MONTHLY_CREDIT_RATE": "1.5",
            "STORAGE_GB_DAILY_CREDIT_RATE": "0.05",
            "CREDITS_PER_USD": "10.0",
            "ADVENTURE_MODE_TOKENS_PER_CALL": "5000",
            "ADVENTURE_MODE_CALLS_PER_MINUTE": "6.0",
        }
        with patch.dict(os.environ, env_vars):
            controller = PricingController.from_env()
            rates = controller.get_rates()
            self.assertEqual(rates["voice_credit_rate"], 2.5)
            self.assertEqual(rates["image_credit_rate"], 0.5)
            self.assertEqual(rates["music_credit_rate"], 1.5)
            self.assertEqual(rates["story_planning_credit_rate"], 0.75)
            self.assertEqual(rates["storage_gb_monthly_rate"], 1.5)
            self.assertEqual(rates["storage_gb_daily_rate"], 0.05)
            self.assertEqual(rates["credits_per_usd"], 10.0)
            self.assertEqual(rates["usd_per_credit"], 0.10)
            self.assertEqual(rates["adventure_mode_tokens_per_call"], 5000.0)
            self.assertEqual(rates["adventure_mode_calls_per_minute"], 6.0)

    def test_usd_per_credit_property(self):
        controller = PricingController(credits_per_usd=25.0)
        # 1.0 / 25.0 = 0.04
        self.assertEqual(controller.usd_per_credit, 0.04)

        invalid_controller = PricingController(credits_per_usd=0.0)
        with self.assertRaises(ValueError):
            _ = invalid_controller.usd_per_credit

    def test_calculate_usage_cost(self):
        controller = PricingController(voice_credit_rate=2.0, image_credit_rate=1.5, music_credit_rate=1.0, story_planning_credit_rate=0.5)
        # 10 mins * 2.0 + 4 images * 1.5 + 3 music * 1.0 + 2 plans * 0.5 = 30.0
        self.assertEqual(controller.calculate_usage_cost(10.0, 4, 3, 2), 30.0)
        # With adventure_actions: 2 plans + 10 actions = 12 total * 0.5 = 6.0
        self.assertEqual(controller.calculate_usage_cost(0.0, 0, 0, 2, adventure_actions=10), 6.0)

        with self.assertRaises(ValueError):
            controller.calculate_usage_cost(-1.0, 4, 1)

        with self.assertRaises(ValueError):
            controller.calculate_usage_cost(10.0, -2, 1)

        with self.assertRaises(ValueError):
            controller.calculate_usage_cost(10.0, 4, -1)

        with self.assertRaises(ValueError):
            controller.calculate_usage_cost(10.0, 4, 1, -1)

        with self.assertRaises(ValueError):
            controller.calculate_usage_cost(10.0, 4, 1, 1, -1)

    def test_calculate_adventure_mode_cost_and_tokens(self):
        controller = PricingController(
            story_planning_credit_rate=0.5,
            adventure_mode_tokens_per_call=4000,
            adventure_mode_calls_per_minute=5.0,
        )
        # 10 actions * 0.5 = 5.0 credits
        self.assertEqual(controller.calculate_adventure_mode_cost(actions=10), 5.0)
        self.assertEqual(controller.estimate_adventure_mode_tokens(actions=10), 40000)

        # 30 mins @ 5 calls/min = 150 actions * 0.5 = 75.0 credits
        self.assertEqual(controller.calculate_adventure_mode_cost(duration_minutes=30.0), 75.0)
        # 150 actions * 4k tokens = 600,000 tokens
        self.assertEqual(controller.estimate_adventure_mode_tokens(duration_minutes=30.0), 600000)

        with self.assertRaises(ValueError):
            controller.calculate_adventure_mode_cost(actions=-1)

        with self.assertRaises(ValueError):
            controller.calculate_adventure_mode_cost(duration_minutes=-5.0)

        with self.assertRaises(ValueError):
            controller.estimate_adventure_mode_tokens(actions=-1)

    def test_calculate_storage_cost_and_credits_for_usd(self):
        controller = PricingController(storage_gb_daily_rate=0.1, credits_per_usd=20.0)
        # 5 GB * 0.1 * 3 days = 1.5 credits
        self.assertAlmostEqual(controller.calculate_storage_cost(5.0, 3.0), 1.5)
        self.assertEqual(controller.credits_for_usd(10.0), 200.0)

        with self.assertRaises(ValueError):
            controller.calculate_storage_cost(-1.0, 1.0)

        with self.assertRaises(ValueError):
            controller.credits_for_usd(-5.0)

    def test_database_manager_pricing_controller_integration(self):
        custom_controller = PricingController(voice_credit_rate=3.0, image_credit_rate=2.0)
        db = LocalDatabaseManager(":memory:", pricing_controller=custom_controller)
        self.assertIs(db.pricing_controller, custom_controller)
        self.assertEqual(db.get_pricing_rates()["voice_credit_rate"], 3.0)

        # Register user and test usage calculation with custom controller rates
        user = db.register_user("pricinguser", "pricing@test.com", "Pass12345")
        # 10 voice mins * 3.0 + 2 images * 2.0 = 34.0 credits deducted from 0.0 -> -34.0
        updated = db.record_user_usage(user["id"], voice_minutes=10.0, images_created=2)
        self.assertEqual(updated["credits"], -34.0)
        db.close()


if __name__ == "__main__":
    unittest.main()
