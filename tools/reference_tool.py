"""Reference tool for character and scene management in narration sessions."""

from __future__ import annotations

import logging
from typing import Optional

from components.canvas_state import CanvasStateManager
from components.reference_manager import (
    Character,
    CharacterLookupResult,
    ReferenceManager,
)
from components.theater_manager import Theater
from tools.base_tool import BaseTools, logged_tool_call
from tools.tool_metadata import terminal

logger = logging.getLogger(__name__)


class ReferenceTool(BaseTools):
    """Expose character and scene reference management capabilities for narration sessions."""

    def __init__(
        self,
        theater: Theater,
        reference_manager: ReferenceManager,
        canvas_manager: Optional[CanvasStateManager] = None,
    ) -> None:
        super().__init__(theater=theater, canvas_manager=canvas_manager)
        if reference_manager is None:
            raise ValueError("reference_manager is required.")
        self.reference_manager = reference_manager

    @logged_tool_call
    def create_or_update_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        gender: str = "",
        voice_tags: str = "",
    ) -> str:
        """Add or update an NPC character in the current narration session.

        Args:
            name: Character's canonical name.
            description: Visual appearance and concept description. Includes clothing, physical attributes, expressions.
            personality: Character's personality traits and behavioral style.
            motivation: Core goals, drives, or motivations in the story.
            quirk: Distinguishing habit, mannerism, or verbal quirk.
            gender: Explicit character gender ('male', 'female', or 'nonbinary').
            voice_tags: Comma-separated speech voice tags (e.g. 'female, calm').

        Returns:
            A human-readable confirmation with the updated character profile.
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            return "Error: Character name cannot be empty."

        clean_tags: list[str] = [t.strip() for t in voice_tags.split(",") if t.strip()] if voice_tags else []
        character = self.reference_manager.create_or_update_character(
            name=clean_name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            gender=gender if gender else None,
            voice_tags=clean_tags if clean_tags else None,
        )
        if character is None:
            return f"Error: Failed to create or update character '{clean_name}'."
        return character.describe()


    @logged_tool_call
    def create_or_update_scene(
        self,
        name: str,
        description: str = "",
    ) -> str:
        """Add or update a scene reference in the current narration session.

        Args:
            name: Scene's canonical name or location.
            description: Visual concept and environment description.

        Returns:
            A human-readable confirmation of the scene reference.
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            return "Error: Scene name cannot be empty."

        entry = self.reference_manager.create_or_update_scene(
            name=clean_name,
            description=description,
        )
        if entry is None:
            return f"Error: Failed to create or update scene '{clean_name}'."
        return f"Created scene reference for '{clean_name}'."

    @logged_tool_call
    def lookup_character(self, query: str = "") -> str:
        """Search for characters by name or traits, or list all characters in the session.

        Args:
            query: Name, keyword, or trait to search for. If empty, returns all session characters.

        Returns:
            Human-readable list and descriptions of matching characters.
        """
        result: CharacterLookupResult = self.reference_manager.lookup_character(query=query)
        return result.describe()

    @terminal
    @logged_tool_call
    def clear_characters(self) -> str:
        """Clear all active characters from the scene.

        Returns:
            Status message indicating how many characters were cleared.
        """
        count = self.reference_manager.clear_scene()
        return f"Cleared {count} character(s) from the scene."

    def get_present_characters(self) -> list[Character]:
        """Return the active characters currently present in the scene."""
        return self.reference_manager.get_present_characters()

    def get_character_references(self) -> list[str]:
        """Return unique image reference identifiers for active characters."""
        return self.reference_manager.get_character_references()

    def count(self) -> int:
        """Return the total count of known characters."""
        return self.reference_manager.count()


__all__ = ["ReferenceTool"]
