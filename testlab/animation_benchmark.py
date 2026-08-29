"""Repeatable scene prompts for Qwen-layered animation diagnostics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnimationBenchmarkPrompt:
    id: str
    title: str
    prompt: str


PROMPTS = (
    AnimationBenchmarkPrompt("forest-path", "Forest path", "A 16:9 cinematic painted forest path at dawn. A lone explorer walks toward a distant ruin beneath tall maples, with ferns framing the scene."),
    AnimationBenchmarkPrompt("coastal-light", "Coastal light", "A 16:9 storybook illustration of a lighthouse on a rocky coast at twilight. A small boat approaches the harbor as the sea reflects the beacon."),
    AnimationBenchmarkPrompt("mountain-bridge", "Mountain bridge", "A 16:9 moonlit mountain valley. A traveler crosses a narrow bridge toward a warm lantern in the distance as mist settles among the peaks."),
)


def animation_prompt_catalog() -> list[dict[str, str]]:
    return [{"id": item.id, "title": item.title, "prompt": item.prompt} for item in PROMPTS]


def get_animation_prompt(prompt_id: str) -> AnimationBenchmarkPrompt:
    return next(item for item in PROMPTS if item.id == prompt_id)
