"""Only supported, explicit feature requests become bounded configuration edits."""

import pytest
from pydantic import ValidationError
import yaml

from services.user_help_settings import FeatureChange, explicit_feature_request, update_settings


@pytest.mark.parametrize("question,enabled", [
    ("Enable Adventure Mode", True), ("How do I turn off Adventure Mode?", False),
    ("please disable story_planning.adventure_mode", False),
])
def test_explicit_requests(question: str, enabled: bool) -> None:
    assert explicit_feature_request(question) == [FeatureChange(setting="story_planning.adventure_mode", enabled=enabled)]


@pytest.mark.parametrize("question", ["What is Adventure Mode?", "enable dice and disable voices", "enable unknown"])
def test_ambiguous_requests_do_not_guess(question: str) -> None:
    assert explicit_feature_request(question) == []


def test_polite_feature_request_uses_supported_alias() -> None:
    assert explicit_feature_request("Can you disable images?") == [
        FeatureChange(setting="image_generation.enabled", enabled=False),
    ]


def test_merge_retains_other_settings_and_adds_missing_sections() -> None:
    updated = update_settings(b"live_agent:\n  special_instructions: Be playful\nimage_generation:\n  enabled: true\n  cooldown_duration: 9\n", [
        FeatureChange(setting="image_generation.enabled", enabled=False),
        FeatureChange(setting="interactive_canvas.enabled", enabled=True),
        FeatureChange(setting="reference_generation_enabled", enabled=False),
    ])
    assert yaml.safe_load(updated) == {
        "live_agent": {"special_instructions": "Be playful"},
        "image_generation": {"enabled": False, "cooldown_duration": 9},
        "interactive_canvas": {"enabled": True}, "reference_generation_enabled": False,
    }


def test_rejects_arbitrary_keys_and_non_boolean_values() -> None:
    with pytest.raises(ValidationError):
        FeatureChange(setting="live_agent.special_instructions", enabled=True)
    with pytest.raises(ValidationError):
        FeatureChange.model_validate({"setting": "image_generation.enabled", "enabled": "false"})
