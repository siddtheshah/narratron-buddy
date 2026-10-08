"""Unit tests for CharacterTool in tools/character_tool.py."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.reference_manager import (
    Character,
    CharacterLookupResult,
    ReferenceManager,
)
from components.theater_manager import Theater
from tools.character_tool import CharacterTool


class TestCharacterTool(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "test-character-tool"
        self.theater.config.return_value = {
            "story_planning": {
                "adventure_mode": False,
                "text_provider": "gemini-3",
                "planner_model": "gemini-3.7-flash",
            },
            "visuals": {
                "model": "hybrid-flux-gemini",
                "style": "watercolor",
            },
        }
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.canvas.story = StoryState()
        self.mock_char_mgr = MagicMock(spec=ReferenceManager)

    def test_init_with_reference_manager(self) -> None:
        tool = CharacterTool(
            self.theater,
            reference_manager=self.mock_char_mgr,
            canvas_manager=self.canvas,
        )
        self.assertIs(tool.reference_manager, self.mock_char_mgr)

    def test_init_raises_when_reference_manager_is_none(self) -> None:
        with self.assertRaises(ValueError):
            CharacterTool(self.theater, reference_manager=None)  # type: ignore

    def test_create_or_update_character_success(self) -> None:
        dummy_char = Character(
            name="Aria",
            gender="female",
            description="A wandering minstrel.",
            personality="Charming and resourceful",
            motivation="Seek ancient ballads",
            quirk="Taps her lute nervously",
            voice_tags=["female", "calm"],
            image_reference="aria_ref",
        )
        self.mock_char_mgr.create_or_update_character.return_value = dummy_char

        tool = CharacterTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_char_mgr,
        )
        result = tool.create_or_update_character(
            name="Aria",
            description="A wandering minstrel.",
            personality="Charming and resourceful",
            motivation="Seek ancient ballads",
            quirk="Taps her lute nervously",
            gender="female",
            voice_tags="female, calm",
            image_reference="aria_ref",
        )
        self.assertIn("Aria", result)
        self.mock_char_mgr.create_or_update_character.assert_called_once_with(
            name="Aria",
            description="A wandering minstrel.",
            personality="Charming and resourceful",
            motivation="Seek ancient ballads",
            quirk="Taps her lute nervously",
            gender="female",
            voice_tags=["female", "calm"],
            image_reference="aria_ref",
        )

    def test_create_or_update_character_empty_name(self) -> None:
        tool = CharacterTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_char_mgr,
        )
        result = tool.create_or_update_character(name="   ")
        self.assertIn("Error: Character name cannot be empty.", result)
        self.mock_char_mgr.create_or_update_character.assert_not_called()

    def test_create_or_update_character_failed_generation(self) -> None:
        self.mock_char_mgr.create_or_update_character.return_value = None

        tool = CharacterTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_char_mgr,
        )
        result = tool.create_or_update_character(name="Ghost")
        self.assertIn("Error: Failed to create or update character 'Ghost'.", result)

    def test_lookup_character(self) -> None:
        dummy_char = Character(
            name="Cedric",
            gender="male",
            personality="Brave knight",
        )
        self.mock_char_mgr.lookup_character.return_value = CharacterLookupResult(
            query="Cedric",
            characters=[dummy_char],
            player=None,
        )

        tool = CharacterTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_char_mgr,
        )
        result = tool.lookup_character("Cedric")
        self.assertIn("Cedric", result)
        self.mock_char_mgr.lookup_character.assert_called_once_with(query="Cedric")

    def test_clear_characters(self) -> None:
        self.mock_char_mgr.clear_scene.return_value = 3

        tool = CharacterTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_char_mgr,
        )
        result = tool.clear_characters()
        self.assertIn("Cleared 3 character(s)", result)
        self.mock_char_mgr.clear_scene.assert_called_once()

    def test_delegated_state_accessors(self) -> None:
        dummy_char = Character(name="Kael", gender="male")
        self.mock_char_mgr.get_present_characters.return_value = [dummy_char]
        self.mock_char_mgr.get_character_references.return_value = ["kael_ref"]
        self.mock_char_mgr.count.return_value = 1

        tool = CharacterTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_char_mgr,
        )
        self.assertEqual(len(tool.get_present_characters()), 1)
        self.assertEqual(tool.get_present_characters()[0].name, "Kael")
        self.assertEqual(tool.get_character_references(), ["kael_ref"])
        self.assertEqual(tool.count(), 1)


if __name__ == "__main__":
    unittest.main()
