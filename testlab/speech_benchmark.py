"""Repeatable dialogue lines for text-to-speech provider comparison."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkSpeechPrompt:
    id: str
    title: str
    dimension: str
    text: str
    voice_instruction: str


SPEECH_PROMPTS = (
    BenchmarkSpeechPrompt("heroic-rally", "Heroic Rally", "Sustained style metadata", "Hold the bridge! Dawn is behind us, and every soul in this valley is counting on us.", "Resolute urgency that builds into an inspiring battle cry."),
    BenchmarkSpeechPrompt("nervous-alchemist", "Nervous Alchemist", "Character Performance", "My insignia? No, no, you have misunderstood. I built the guardian to protect the city, not threaten it.", "Quick, intelligent, anxiously defensive delivery."),
    BenchmarkSpeechPrompt("quiet-revelation", "Quiet Revelation", "Emotion and vocal event", "The bells did not ring because the citadel woke. <short pause> They rang because it finally remembered our names.", "Soft, reverent, and breath-held, growing emotional at the end."),
    BenchmarkSpeechPrompt("comic-relief", "Comic Relief", "Timing and expression", "Excellent plan. We sneak past the dragon, take the treasure, and absolutely do not mention that I brought a squeaky sword.", "Dry comic confidence, ending as an embarrassed admission."),
)


def speech_prompt_catalog() -> list[dict[str, str]]:
    return [item.__dict__.copy() for item in SPEECH_PROMPTS]


def get_speech_prompt(prompt_id: str) -> BenchmarkSpeechPrompt:
    for item in SPEECH_PROMPTS:
        if item.id == prompt_id:
            return item
    raise KeyError(prompt_id)
