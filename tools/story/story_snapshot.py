"""Point-in-time snapshot and diff tracking built from StoryState and Notepad together."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Optional

from components.canvas.story_state import (
    CharacterState,
    PlayerCharacterState,
    StoryState,
)
from components.notepad import Notepad

ImageRecord = dict[str, str | int | float | bool]


@dataclass
class StoryStateSnapshot:
    stickies: dict[str, str] = field(default_factory=dict)
    player_character: Optional[PlayerCharacterState] = None
    characters: dict[str, CharacterState] = field(default_factory=dict)
    known_image_names: set[str] = field(default_factory=set)
    deep_plan_revision: int = 0


StorySnapshot = StoryStateSnapshot


@dataclass
class StoryStateDiff:
    sticky_diffs: list[str] = field(default_factory=list)
    player_character_diff: str = ""
    character_diffs: list[str] = field(default_factory=list)
    new_images: list[ImageRecord] = field(default_factory=list)
    deep_plan_diff: str = ""

    def __iter__(self) -> Iterator[list[str] | str | list[ImageRecord]]:
        yield self.sticky_diffs
        yield self.player_character_diff
        yield self.character_diffs
        yield self.new_images
        yield self.deep_plan_diff


StorySnapshotDiff = StoryStateDiff


def create_snapshot(
    story_state: StoryState,
    notepad: Notepad,
    *,
    stickies: Optional[dict[str, str]] = None,
    player_character: Optional[PlayerCharacterState] = None,
    characters: Optional[dict[str, CharacterState]] = None,
    known_image_names: Optional[set[str]] = None,
    deep_plan_revision: int = 0,
) -> StoryStateSnapshot:
    """Create a point-in-time snapshot of story state and notepad together."""
    if stickies is not None:
        current_stickies = stickies
    else:
        current_stickies = {
            note.get("topic") or note.get("name", ""): note.get("info") or note.get("content", "")
            for note in notepad.get_present_sticky_notes()
            if note.get("topic") or note.get("name")
        }
    current_pc = player_character if player_character is not None else story_state.player_character
    current_chars = characters if characters is not None else story_state.characters
    return StoryStateSnapshot(
        stickies=dict(current_stickies),
        player_character=current_pc,
        characters=dict(current_chars),
        known_image_names=set(known_image_names or set()),
        deep_plan_revision=deep_plan_revision,
    )


def diff(
    previous: StoryStateSnapshot,
    story_state: Optional[StoryState] = None,
    notepad: Optional[Notepad] = None,
    *,
    current_stickies: Optional[dict[str, str]] = None,
    current_pc: Optional[PlayerCharacterState] = None,
    current_chars: Optional[dict[str, CharacterState]] = None,
    recent_images: Optional[list[ImageRecord]] = None,
    current_deep_plan_revision: int = 0,
    current_through_turn: int = 0,
    current_snapshot: Optional[StoryStateSnapshot] = None,
) -> StoryStateDiff:
    """Compute diffs between a previous snapshot and current story state + notepad."""
    if current_snapshot is not None:
        stickies = current_snapshot.stickies
        pc = current_snapshot.player_character
        chars = current_snapshot.characters
        rev = current_snapshot.deep_plan_revision
    else:
        if current_stickies is not None:
            stickies = current_stickies
        elif notepad is not None:
            stickies = {
                note.get("topic") or note.get("name", ""): note.get("info") or note.get("content", "")
                for note in notepad.get_present_sticky_notes()
                if note.get("topic") or note.get("name")
            }
        else:
            stickies = {}

        pc = current_pc if current_pc is not None else (story_state.player_character if story_state is not None else None)
        chars = current_chars if current_chars is not None else (dict(story_state.characters) if story_state is not None else {})
        rev = current_deep_plan_revision

    sticky_diffs: list[str] = []
    for topic, info in stickies.items():
        if topic not in previous.stickies:
            sticky_diffs.append(f"+ Added [{topic}]: {info}")
    for topic, info in stickies.items():
        if topic in previous.stickies and previous.stickies[topic] != info:
            sticky_diffs.append(
                f"~ Updated [{topic}]: was \"{previous.stickies[topic]}\" -> now \"{info}\""
            )
    for topic in previous.stickies:
        if topic not in stickies:
            sticky_diffs.append(f"- Removed [{topic}]")

    pc_diff = ""
    if pc != previous.player_character:
        if pc is not None:
            parts: list[str] = [f"Name: {pc.name or 'Unnamed Explorer'}"]
            if pc.image_description:
                parts.append(f"Visual: {pc.image_description}")
            pc_diff = " | ".join(parts)
        else:
            pc_diff = "(Player character removed)"

    character_diffs: list[str] = []
    for name, c in chars.items():
        if name not in previous.characters:
            character_diffs.append(
                f"+ Added {name}: Personality: {c.personality or 'N/A'} | "
                f"Motivation: {c.motivation or 'N/A'} | Quirk: {c.quirk or 'N/A'}"
            )
    for name, c in chars.items():
        if name in previous.characters and c != previous.characters[name]:
            character_diffs.append(
                f"~ Updated {name}: Personality: {c.personality or 'N/A'} | "
                f"Motivation: {c.motivation or 'N/A'} | Quirk: {c.quirk or 'N/A'}"
            )
    for name in previous.characters:
        if name not in chars:
            character_diffs.append(f"- Removed {name}")

    images = recent_images or []
    new_images: list[ImageRecord] = [
        img for img in images
        if str(img.get("name") or "") not in previous.known_image_names
    ]

    dp_diff = ""
    if rev != previous.deep_plan_revision:
        turn_info = f" (through turn {current_through_turn})" if current_through_turn > 0 else ""
        dp_diff = (
            f"Deep plan revision advanced from {previous.deep_plan_revision} "
            f"to {rev}{turn_info}."
        )

    return StoryStateDiff(
        sticky_diffs=sticky_diffs,
        player_character_diff=pc_diff,
        character_diffs=character_diffs,
        new_images=new_images,
        deep_plan_diff=dp_diff,
    )


__all__ = [
    "ImageRecord",
    "StorySnapshot",
    "StorySnapshotDiff",
    "StoryStateDiff",
    "StoryStateSnapshot",
    "create_snapshot",
    "diff",
]
