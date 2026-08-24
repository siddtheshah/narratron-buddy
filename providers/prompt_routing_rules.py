"""Deterministic safety rules shared by corpus labelling and runtime routing."""

from __future__ import annotations

import re


TEXT_DISPLAYED = re.compile(r"\b(?:readable\s+)?(?:text|words?|letters?|lettering|caption|subtitle|logo|sign|poster|label|title|typography|slogan)\b", re.I)
OBJECT_INTERACTION = re.compile(r"\b(?:carry|carries|carrying|hold|holds|holding|touch|touches|touching|grab|grabs|grabbing|pick(?:s|ed|ing)?\s+up|read(?:s|ing)?|writ(?:e|es|ing)|cook(?:s|ing)?|play(?:s|ing)?|use(?:s|ing)|wield(?:s|ing)|drink(?:s|ing)?|eat(?:s|ing)?|examin(?:e|es|ing))\b", re.I)
CREATURE_INTERACTION = re.compile(r"\b(?:fight(?:s|ing)?|dance(?:s|ing)?|rid(?:e|es|ing)|chase(?:s|ing)?|hug(?:s|ging)?|kiss(?:es|ing)?|embrac(?:e|es|ing)|attack(?:s|ing)?|meet(?:s|ing)?|talk(?:s|ing)?\s+(?:with|to))\b", re.I)
MULTIPLE_CHARACTERS = re.compile(r"\b(?:two|three|four|five|six|seven|eight|nine|ten|several|many|multiple)\s+(?:people|men|women|children|characters|figures|creatures|animals|dogs|cats|wolves|dragons|robots|astronauts|knights|soldiers)\b|\b(?:pair|couple|crowd|group|famil(?:y|ies)|people|men|women|children|characters|figures|creatures|animals|dogs|cats|wolves|dragons|robots|astronauts|knights|soldiers|souls|demons)\b|\b(?:mother|father)\s+and\s+child\b", re.I)


def label_prompt(prompt: str) -> dict[str, int]:
    """Return the four policy labels and their derived complex target."""
    labels = {
        "multiple_characters": int(bool(MULTIPLE_CHARACTERS.search(prompt))),
        "creature_creature_interaction": int(bool(CREATURE_INTERACTION.search(prompt))),
        "creature_object_interaction": int(bool(OBJECT_INTERACTION.search(prompt))),
        "text_displayed": int(bool(TEXT_DISPLAYED.search(prompt))),
    }
    labels["complex"] = int(any(labels.values()))
    return labels


def routing_reasons(labels: dict[str, int]) -> list[str]:
    """Map corpus labels to the stable routing reason vocabulary."""
    return [
        reason
        for key, reason in (
            ("multiple_characters", "multiple_subjects"),
            ("creature_creature_interaction", "creature_creature_interaction"),
            ("creature_object_interaction", "creature_object_interaction"),
            ("text_displayed", "text_rendering"),
        )
        if labels[key]
    ]
