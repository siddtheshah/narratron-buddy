"""Bounded, reviewable feature switches for canvas help answers."""

from __future__ import annotations

from hashlib import sha256
import re

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, TypeAdapter, field_validator
import yaml


# Only switches with an established theater configuration contract are editable.
FEATURES: dict[str, str] = {
    "image_generation.enabled": "AI image generation",
    "animation.generation_enabled": "AI animation generation",
    "interactive_canvas.enabled": "interactive canvas",
    "game_tool.enabled": "game tool",
    "music.use_generated_music": "generated music",
    "reference_generation_enabled": "reference image generation",
    "observability_tool.enabled": "observability tool",
    "live_agent.enable_regular_observability": "regular observability",
    "story_planning.adventure_mode": "Adventure Mode",
    "story_planning.enable_dice": "dice",
    "story_planning.auto_begin": "automatic opening scene",
    "story_planning.character_voicing": "character voices",
    "story_planning.narration_voicing": "scene narration voices",
    "story_planning.text_beautification": "text effects",
    "user_help.enabled": "agent help tool",
}

FEATURE_ALIASES: dict[str, str] = {
    "images": "image_generation.enabled",
    "image generation": "image_generation.enabled",
    "animations": "animation.generation_enabled",
    "animation generation": "animation.generation_enabled",
    "character voicing": "story_planning.character_voicing",
    "narration voicing": "story_planning.narration_voicing",
    "user help": "user_help.enabled",
}


class FeatureChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting: str
    enabled: StrictBool

    @field_validator("setting")
    @classmethod
    def supported_setting(cls, value: str) -> str:
        if value not in FEATURES:
            raise ValueError("This setting cannot be changed from help.")
        return value


class SettingsProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    changes: list[FeatureChange] = Field(min_length=1, max_length=15)

    @field_validator("changes")
    @classmethod
    def unique_settings(cls, changes: list[FeatureChange]) -> list[FeatureChange]:
        if len({change.setting for change in changes}) != len(changes):
            raise ValueError("Each setting may appear only once in a proposal.")
        return changes

    def summaries(self) -> list[str]:
        return [f"{'Enable' if change.enabled else 'Disable'} {FEATURES[change.setting]}" for change in self.changes]


def config_digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def update_settings(content: bytes, changes: list[FeatureChange]) -> bytes:
    """Merge switches into the latest validated mapping, preserving other values."""
    config = TypeAdapter(dict[str, JsonValue]).validate_python(yaml.safe_load(content))
    for change in changes:
        parts = change.setting.split(".")
        if len(parts) == 1:
            config[parts[0]] = change.enabled
        else:
            section = TypeAdapter(dict[str, JsonValue]).validate_python(config.get(parts[0]) or {})
            section[parts[1]] = change.enabled
            config[parts[0]] = section
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True).encode("utf-8")


def explicit_feature_request(question: str) -> list[FeatureChange]:
    """Free help handles a single unambiguous switch; research handles other wording."""
    match = re.fullmatch(
        r"(?:(?:please|can you|could you|how do i|how can i|i want to|i would like to)\s+)?"
        r"(enable|disable|turn on|turn off)\s+(.+?)[?.!]*",
        question.strip(), re.IGNORECASE,
    )
    if match is None:
        return []
    requested = match[2].casefold()
    for setting, label in FEATURES.items():
        if requested in {setting.casefold(), label.casefold()} or FEATURE_ALIASES.get(requested) == setting:
            return [FeatureChange(setting=setting, enabled=match[1].casefold() in {"enable", "turn on"})]
    return []
