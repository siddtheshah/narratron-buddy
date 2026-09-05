"""Fixed, repeatable video prompts used to benchmark video generation models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BenchmarkVideoPrompt:
    id: str
    title: str
    category: str
    prompt: str


PROMPTS = (
    BenchmarkVideoPrompt(
        "dragon-flight",
        "Dragon over foggy peaks",
        "Cinematic Action",
        "A magnificent emerald dragon swoops down through swirling mountain mist at sunset. Dramatic slow-motion wingbeats, golden light reflecting on scales, sweeping cinematic aerial camera tracking.",
    ),
    BenchmarkVideoPrompt(
        "cyberpunk-pan",
        "Cyberpunk rain alley",
        "Atmosphere & Lighting",
        "Smooth forward dolly shot moving through a neon-lit futuristic alley in heavy rain. Holographic signs glow purple and cyan, puddles splash as a hooded figure walks briskly into the distance.",
    ),
    BenchmarkVideoPrompt(
        "ocean-waves",
        "Bioluminescent waves",
        "Nature & Fluid Dynamics",
        "Gentle ocean waves crashing onto a dark volcanic sand beach at midnight. The foam glows bright electric blue with bioluminescent algae under a starry sky. Steady tripod shot.",
    ),
    BenchmarkVideoPrompt(
        "wizard-spell",
        "Alchemist summoning portal",
        "Visual Effects & Magic",
        "An ancient alchemist in embroidered robes gestures with both hands in a candlelit stone laboratory. A swirling vortex of golden sparks and violet plasma opens between his palms, illuminating ancient books and glassware.",
    ),
    BenchmarkVideoPrompt(
        "kitten-butterflies",
        "Playful kitten in meadow",
        "Character Motion",
        "A fluffy calico kitten bounds playfully through a sunlit clover field chasing two fluttering yellow butterflies. Soft afternoon sunlight, shallow depth of field, natural fluid movement.",
    ),
)


def video_prompt_catalog() -> list[dict[str, str]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "category": item.category,
            "prompt": item.prompt,
        }
        for item in PROMPTS
    ]


def get_video_prompt(prompt_id: str) -> BenchmarkVideoPrompt:
    for item in PROMPTS:
        if item.id == prompt_id:
            return item
    raise KeyError(prompt_id)
