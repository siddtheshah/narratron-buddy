"""PricingController module for managing credit consumption rates dynamically and from environment variables."""

import os
from typing import Dict, Optional

DEFAULT_ADVENTURE_MODE_TOKENS_PER_CALL = 4000
DEFAULT_ADVENTURE_MODE_CALLS_PER_MINUTE = 5.0
DEFAULT_CHARACTER_VOICING_TURN_CREDIT_RATE = 0.25
DEFAULT_INTERACTIVE_CANVAS_CREDIT_RATE = 0.25
DEFAULT_LAYERED_ANIMATION_CREDIT_RATE = 5.0


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
        adventure_mode_tokens_per_call: Optional[int] = None,
        adventure_mode_calls_per_minute: Optional[float] = None,
        character_voicing_turn_credit_rate: Optional[float] = None,
        interactive_canvas_credit_rate: Optional[float] = None,
        layered_animation_credit_rate: Optional[float] = None,
    ):
        self.voice_credit_rate = voice_credit_rate if voice_credit_rate is not None else 1.0
        self.image_credit_rate = image_credit_rate if image_credit_rate is not None else 1.0
        self.music_credit_rate = music_credit_rate if music_credit_rate is not None else 2.0
        self.story_planning_credit_rate = story_planning_credit_rate if story_planning_credit_rate is not None else 0.5
        self.storage_gb_monthly_rate = storage_gb_monthly_rate if storage_gb_monthly_rate is not None else 1.0
        self.storage_gb_daily_rate = storage_gb_daily_rate if storage_gb_daily_rate is not None else 0.033
        self.credits_per_usd = credits_per_usd if credits_per_usd is not None else 20.0
        self.adventure_mode_tokens_per_call = (
            adventure_mode_tokens_per_call
            if adventure_mode_tokens_per_call is not None
            else DEFAULT_ADVENTURE_MODE_TOKENS_PER_CALL
        )
        self.adventure_mode_calls_per_minute = (
            adventure_mode_calls_per_minute
            if adventure_mode_calls_per_minute is not None
            else DEFAULT_ADVENTURE_MODE_CALLS_PER_MINUTE
        )
        self.character_voicing_turn_credit_rate = (
            character_voicing_turn_credit_rate
            if character_voicing_turn_credit_rate is not None
            else DEFAULT_CHARACTER_VOICING_TURN_CREDIT_RATE
        )
        self.interactive_canvas_credit_rate = (
            interactive_canvas_credit_rate
            if interactive_canvas_credit_rate is not None
            else DEFAULT_INTERACTIVE_CANVAS_CREDIT_RATE
        )
        self.layered_animation_credit_rate = (
            layered_animation_credit_rate
            if layered_animation_credit_rate is not None
            else DEFAULT_LAYERED_ANIMATION_CREDIT_RATE
        )

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

        def _get_int_env(keys: list, default: int) -> int:
            for k in keys:
                val = os.environ.get(k)
                if val is not None:
                    try:
                        return int(val)
                    except ValueError:
                        pass
            return default

        return cls(
            voice_credit_rate=_get_float_env(["VOICE_CREDIT_RATE", "PRICING_VOICE_CREDIT_RATE"], 1.0),
            image_credit_rate=_get_float_env(["IMAGE_CREDIT_RATE", "PRICING_IMAGE_CREDIT_RATE"], 1.0),
            music_credit_rate=_get_float_env(["MUSIC_CREDIT_RATE", "PRICING_MUSIC_CREDIT_RATE"], 2.0),
            story_planning_credit_rate=_get_float_env(
                ["STORY_PLANNING_CREDIT_RATE", "PRICING_STORY_PLANNING_CREDIT_RATE"], 0.5
            ),
            storage_gb_monthly_rate=_get_float_env(["STORAGE_GB_MONTHLY_CREDIT_RATE", "PRICING_STORAGE_GB_MONTHLY_CREDIT_RATE"], 1.0),
            storage_gb_daily_rate=_get_float_env(["STORAGE_GB_DAILY_CREDIT_RATE", "PRICING_STORAGE_GB_DAILY_CREDIT_RATE"], 0.033),
            credits_per_usd=_get_float_env(["CREDITS_PER_USD", "PRICING_CREDITS_PER_USD"], 20.0),
            adventure_mode_tokens_per_call=_get_int_env(
                ["ADVENTURE_MODE_TOKENS_PER_CALL", "PRICING_ADVENTURE_MODE_TOKENS_PER_CALL"], DEFAULT_ADVENTURE_MODE_TOKENS_PER_CALL
            ),
            adventure_mode_calls_per_minute=_get_float_env(
                ["ADVENTURE_MODE_CALLS_PER_MINUTE", "PRICING_ADVENTURE_MODE_CALLS_PER_MINUTE"], DEFAULT_ADVENTURE_MODE_CALLS_PER_MINUTE
            ),
            character_voicing_turn_credit_rate=_get_float_env(
                ["CHARACTER_VOICING_TURN_CREDIT_RATE", "PRICING_CHARACTER_VOICING_TURN_CREDIT_RATE"],
                DEFAULT_CHARACTER_VOICING_TURN_CREDIT_RATE,
            ),
            interactive_canvas_credit_rate=_get_float_env(
                [
                    "INTERACTIVE_CANVAS_CREDIT_RATE",
                    "PRICING_INTERACTIVE_CANVAS_CREDIT_RATE",
                    "INTERACTIVE_CANVAS_TOOL_CREDIT_RATE",
                    "PRICING_INTERACTIVE_CANVAS_TOOL_CREDIT_RATE",
                ],
                DEFAULT_INTERACTIVE_CANVAS_CREDIT_RATE,
            ),
            layered_animation_credit_rate=_get_float_env(
                [
                    "LAYERED_ANIMATION_CREDIT_RATE",
                    "PRICING_LAYERED_ANIMATION_CREDIT_RATE",
                    "LAYERED_ANIMATION_TOOL_CREDIT_RATE",
                    "PRICING_LAYERED_ANIMATION_TOOL_CREDIT_RATE",
                ],
                DEFAULT_LAYERED_ANIMATION_CREDIT_RATE,
            ),
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
            "adventure_mode_tokens_per_call": float(self.adventure_mode_tokens_per_call),
            "adventure_mode_calls_per_minute": self.adventure_mode_calls_per_minute,
            "adventure_mode_credit_rate_per_action": self.story_planning_credit_rate,
            "adventure_mode_credit_rate_per_minute": self.story_planning_credit_rate * self.adventure_mode_calls_per_minute,
            "character_voicing_turn_credit_rate": self.character_voicing_turn_credit_rate,
            "interactive_canvas_credit_rate": self.interactive_canvas_credit_rate,
            "interactive_canvas_tool_credit_rate": self.interactive_canvas_credit_rate,
            "layered_animation_credit_rate": self.layered_animation_credit_rate,
            "layered_animation_tool_credit_rate": self.layered_animation_credit_rate,
        }

    def calculate_usage_cost(
        self,
        voice_minutes: float = 0.0,
        images_created: int = 0,
        music_created: int = 0,
        story_plans: int = 0,
        adventure_actions: int = 0,
        character_voiced_turns: int = 0,
        interactive_canvas_used: int = 0,
        layered_animations_created: int = 0,
    ) -> float:
        """Calculate total credit cost for voice, image, music, story-planning/adventure-mode, interactive canvas, and layered animation usage."""
        if (
            voice_minutes < 0
            or images_created < 0
            or music_created < 0
            or story_plans < 0
            or adventure_actions < 0
            or character_voiced_turns < 0
            or interactive_canvas_used < 0
            or layered_animations_created < 0
        ):
            raise ValueError(
                "Usage parameters (voice_minutes, images_created, music_created, story_plans, adventure_actions, character_voiced_turns, interactive_canvas_used, layered_animations_created) must be non-negative."
            )
        total_story_plans = story_plans + adventure_actions
        return (
            (voice_minutes * self.voice_credit_rate)
            + (images_created * self.image_credit_rate)
            + (music_created * self.music_credit_rate)
            + (total_story_plans * self.story_planning_credit_rate)
            + (character_voiced_turns * self.character_voicing_turn_credit_rate)
            + (interactive_canvas_used * self.interactive_canvas_credit_rate)
            + (layered_animations_created * self.layered_animation_credit_rate)
        )

    def calculate_adventure_mode_cost(
        self,
        actions: int = 0,
        duration_minutes: float = 0.0,
        calls_per_minute: Optional[float] = None,
    ) -> float:
        """Calculate credit cost for adventure mode based on user actions and/or session duration in minutes.

        Each user action uses a Gemini 3.7 Flash call (defaulting to 4k tokens/call and 5 calls/min).
        """
        if actions < 0 or duration_minutes < 0:
            raise ValueError("Adventure mode parameters (actions, duration_minutes) must be non-negative.")
        if calls_per_minute is not None and calls_per_minute < 0:
            raise ValueError("calls_per_minute must be non-negative.")

        cpm = calls_per_minute if calls_per_minute is not None else self.adventure_mode_calls_per_minute
        total_actions = float(actions) + (duration_minutes * cpm)
        return total_actions * self.story_planning_credit_rate

    def estimate_adventure_mode_tokens(
        self,
        actions: int = 0,
        duration_minutes: float = 0.0,
        calls_per_minute: Optional[float] = None,
        tokens_per_call: Optional[int] = None,
    ) -> int:
        """Estimate total tokens consumed in adventure mode based on user actions or session duration."""
        if actions < 0 or duration_minutes < 0:
            raise ValueError("Adventure mode parameters (actions, duration_minutes) must be non-negative.")
        if calls_per_minute is not None and calls_per_minute < 0:
            raise ValueError("calls_per_minute must be non-negative.")
        if tokens_per_call is not None and tokens_per_call < 0:
            raise ValueError("tokens_per_call must be non-negative.")

        cpm = calls_per_minute if calls_per_minute is not None else self.adventure_mode_calls_per_minute
        tpc = tokens_per_call if tokens_per_call is not None else self.adventure_mode_tokens_per_call
        total_actions = float(actions) + (duration_minutes * cpm)
        return int(total_actions * tpc)

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
