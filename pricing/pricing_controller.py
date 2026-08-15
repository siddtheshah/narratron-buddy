"""PricingController module for managing credit consumption rates dynamically and from environment variables."""

import os
from typing import Dict, Any, Optional


class PricingController:
    """Manages credit consumption rates, exchange rates, and cost calculations."""

    def __init__(
        self,
        voice_credit_rate: Optional[float] = None,
        image_credit_rate: Optional[float] = None,
        music_credit_rate: Optional[float] = None,
        story_planning_credit_rate: Optional[float] = None,
        storage_gb_monthly_rate: Optional[float] = None,
        storage_gb_daily_rate: Optional[float] = None,
        credits_per_usd: Optional[float] = None,
    ):
        self.voice_credit_rate = voice_credit_rate if voice_credit_rate is not None else 1.0
        self.image_credit_rate = image_credit_rate if image_credit_rate is not None else 1.0
        self.music_credit_rate = music_credit_rate if music_credit_rate is not None else 2.0
        self.story_planning_credit_rate = story_planning_credit_rate if story_planning_credit_rate is not None else 0.65
        self.storage_gb_monthly_rate = storage_gb_monthly_rate if storage_gb_monthly_rate is not None else 1.0
        self.storage_gb_daily_rate = storage_gb_daily_rate if storage_gb_daily_rate is not None else 0.033
        self.credits_per_usd = credits_per_usd if credits_per_usd is not None else 20.0

    @property
    def usd_per_credit(self) -> float:
        """Calculated USD cost per credit (1.0 / credits_per_usd)."""
        if self.credits_per_usd <= 0:
            raise ValueError("credits_per_usd must be greater than zero.")
        return 1.0 / self.credits_per_usd

    @classmethod
    def from_env(cls) -> "PricingController":
        """Statically initialize PricingController with parameters loaded from environment variables."""
        def _get_float_env(keys: list, default: float) -> float:
            for k in keys:
                val = os.environ.get(k)
                if val is not None:
                    try:
                        return float(val)
                    except ValueError:
                        pass
            return default

        return cls(
            voice_credit_rate=_get_float_env(["VOICE_CREDIT_RATE", "PRICING_VOICE_CREDIT_RATE"], 1.0),
            image_credit_rate=_get_float_env(["IMAGE_CREDIT_RATE", "PRICING_IMAGE_CREDIT_RATE"], 1.0),
            music_credit_rate=_get_float_env(["MUSIC_CREDIT_RATE", "PRICING_MUSIC_CREDIT_RATE"], 2.0),
            story_planning_credit_rate=_get_float_env(
                ["STORY_PLANNING_CREDIT_RATE", "PRICING_STORY_PLANNING_CREDIT_RATE"], 0.65
            ),
            storage_gb_monthly_rate=_get_float_env(["STORAGE_GB_MONTHLY_CREDIT_RATE", "PRICING_STORAGE_GB_MONTHLY_CREDIT_RATE"], 1.0),
            storage_gb_daily_rate=_get_float_env(["STORAGE_GB_DAILY_CREDIT_RATE", "PRICING_STORAGE_GB_DAILY_CREDIT_RATE"], 0.033),
            credits_per_usd=_get_float_env(["CREDITS_PER_USD", "PRICING_CREDITS_PER_USD"], 20.0),
        )

    def get_rates(self) -> Dict[str, float]:
        """Return a dictionary of all current rates for polling by the application or frontend."""
        return {
            "voice_credit_rate": self.voice_credit_rate,
            "image_credit_rate": self.image_credit_rate,
            "music_credit_rate": self.music_credit_rate,
            "story_planning_credit_rate": self.story_planning_credit_rate,
            "storage_gb_monthly_rate": self.storage_gb_monthly_rate,
            "storage_gb_daily_rate": self.storage_gb_daily_rate,
            "credits_per_usd": self.credits_per_usd,
            "usd_per_credit": self.usd_per_credit,
        }

    def calculate_usage_cost(
        self,
        voice_minutes: float = 0.0,
        images_created: int = 0,
        music_created: int = 0,
        story_plans: int = 0,
    ) -> float:
        """Calculate total credit cost for voice, image, music, and story-planning usage."""
        if voice_minutes < 0 or images_created < 0 or music_created < 0 or story_plans < 0:
            raise ValueError(
                "Usage parameters (voice_minutes, images_created, music_created, story_plans) must be non-negative."
            )
        return (
            (voice_minutes * self.voice_credit_rate)
            + (images_created * self.image_credit_rate)
            + (music_created * self.music_credit_rate)
            + (story_plans * self.story_planning_credit_rate)
        )

    def calculate_storage_cost(self, gb_amount: float, days: float = 1.0) -> float:
        """Calculate storage cost in credits given GB size and duration in days."""
        if gb_amount < 0 or days < 0:
            raise ValueError("Storage parameters (gb_amount, days) must be non-negative.")
        return gb_amount * self.storage_gb_daily_rate * days

    def credits_for_usd(self, usd_amount: float) -> float:
        """Calculate credits granted for a given USD purchase amount."""
        if usd_amount < 0:
            raise ValueError("usd_amount must be non-negative.")
        return usd_amount * self.credits_per_usd
