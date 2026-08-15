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
    BenchmarkSpeechPrompt("heroic-rally", "Heroic Rally", "Energy & Clarity", "Hold the bridge! Dawn is behind us, and every soul in this valley is counting on us.", "Speak with resolute urgency, rising into an inspiring battle cry."),
    BenchmarkSpeechPrompt("nervous-alchemist", "Nervous Alchemist", "Character Performance", "My insignia? No, no, you have misunderstood. I built the guardian to protect the city, not threaten it.", "Speak quickly with intelligent but anxious defensiveness."),
    BenchmarkSpeechPrompt("quiet-revelation", "Quiet Revelation", "Emotional Subtlety", "The bells did not ring because the citadel woke. They rang because it finally remembered our names.", "Speak softly and reverently, with a pause before the final sentence."),
    BenchmarkSpeechPrompt("comic-relief", "Comic Relief", "Timing & Expression", "Excellent plan. We sneak past the dragon, take the treasure, and absolutely do not mention that I brought a squeaky sword.", "Speak with dry comic confidence, then let the last phrase land like an embarrassed admission."),
)


def speech_prompt_catalog() -> list[dict[str, str]]:
    return [item.__dict__.copy() for item in SPEECH_PROMPTS]


def get_speech_prompt(prompt_id: str) -> BenchmarkSpeechPrompt:
    for item in SPEECH_PROMPTS:
        if item.id == prompt_id:
            return item
    raise KeyError(prompt_id)
