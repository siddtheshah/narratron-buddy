"""Unit tests for ReferenceTool in tools/reference_tool.py."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image, PngImagePlugin

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.reference_manager import (
    Character,
    CharacterLookupResult,
    ReferenceManager,
)
from components.theater_manager import Theater, TheaterManager
from tools.reference_tool import ReferenceTool


class TestReferenceTool(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "test-reference-tool"
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
        self.mock_ref_mgr = MagicMock(spec=ReferenceManager)

    def test_init_with_reference_manager(self) -> None:
        tool = ReferenceTool(
            self.theater,
            reference_manager=self.mock_ref_mgr,
            canvas_manager=self.canvas,
        )
        self.assertIs(tool.reference_manager, self.mock_ref_mgr)

    def test_init_raises_when_reference_manager_is_none(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceTool(self.theater, reference_manager=None)  # type: ignore

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
        self.mock_ref_mgr.create_or_update_character.return_value = dummy_char

        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.create_or_update_character(
            name="Aria",
            description="A wandering minstrel.",
            personality="Charming and resourceful",
            motivation="Seek ancient ballads",
            quirk="Taps her lute nervously",
            gender="female",
            voice_tags="female, calm",
        )
        self.assertIn("Aria", result)
        self.mock_ref_mgr.create_or_update_character.assert_called_once_with(
            name="Aria",
            description="A wandering minstrel.",
            personality="Charming and resourceful",
            motivation="Seek ancient ballads",
            quirk="Taps her lute nervously",
            gender="female",
            voice_tags=["female", "calm"],
        )

    def test_create_or_update_character_empty_name(self) -> None:
        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.create_or_update_character(name="   ")
        self.assertIn("Error: Character name cannot be empty.", result)
        self.mock_ref_mgr.create_or_update_character.assert_not_called()

    def test_create_or_update_character_failed_generation(self) -> None:
        self.mock_ref_mgr.create_or_update_character.return_value = None

        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.create_or_update_character(name="Ghost")
        self.assertIn("Error: Failed to create or update character 'Ghost'.", result)

    def test_lookup_character(self) -> None:
        dummy_char = Character(
            name="Cedric",
            gender="male",
            personality="Brave knight",
        )
        self.mock_ref_mgr.lookup_character.return_value = CharacterLookupResult(
            query="Cedric",
            characters=[dummy_char],
            player=None,
        )

        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.lookup_character("Cedric")
        self.assertIn("Cedric", result)
        self.mock_ref_mgr.lookup_character.assert_called_once_with(query="Cedric")

    def test_clear_characters(self) -> None:
        self.mock_ref_mgr.clear_scene.return_value = 3

        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.clear_characters()
        self.assertIn("Cleared 3 character(s)", result)
        self.mock_ref_mgr.clear_scene.assert_called_once()

    def test_delegated_state_accessors(self) -> None:
        dummy_char = Character(name="Kael", gender="male")
        self.mock_ref_mgr.get_present_characters.return_value = [dummy_char]
        self.mock_ref_mgr.get_character_references.return_value = ["kael_ref"]
        self.mock_ref_mgr.count.return_value = 1

        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        self.assertEqual(len(tool.get_present_characters()), 1)
        self.assertEqual(tool.get_present_characters()[0].name, "Kael")
        self.assertEqual(tool.get_character_references(), ["kael_ref"])
        self.assertEqual(tool.count(), 1)

    def test_create_or_update_scene_success(self) -> None:
        self.mock_ref_mgr.create_or_update_scene.return_value = {
            "name": "Ruined_Shrine_1",
            "alias": "output/artifacts/updated_references/scenes/Ruined_Shrine/1.png",
            "path": "/path/to/1.png",
        }
        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.create_or_update_scene(
            name="Ruined Shrine",
            description="Ancient stone shrine covered in moss",
        )
        self.assertEqual(result, "Created scene reference for 'Ruined Shrine'.")
        self.assertNotIn("/path/to/1.png", result)
        self.mock_ref_mgr.create_or_update_scene.assert_called_once_with(
            name="Ruined Shrine",
            description="Ancient stone shrine covered in moss",
        )

    def test_create_or_update_scene_empty_name(self) -> None:
        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.create_or_update_scene(name="   ")
        self.assertIn("Error: Scene name cannot be empty.", result)
        self.mock_ref_mgr.create_or_update_scene.assert_not_called()

    def test_create_or_update_scene_failure(self) -> None:
        self.mock_ref_mgr.create_or_update_scene.return_value = None
        tool = ReferenceTool(
            self.theater,
            canvas_manager=self.canvas,
            reference_manager=self.mock_ref_mgr,
        )
        result = tool.create_or_update_scene(name="Deep Cave")
        self.assertIn("Error: Failed to create or update scene 'Deep Cave'.", result)


if __name__ == "__main__":
    unittest.main()


def test_reference_discovery_searches_metadata_and_latest_versions(tmp_path: Path) -> None:
    theater = TheaterManager(base_theaters_dir=str(tmp_path)).theater("reference-search")
    theater.directory().mkdir(parents=True)
    (theater.directory() / "theater.yaml").write_text(
        "reference_generation_enabled: false\n", encoding="utf-8"
    )
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Title", "The Candlelit Scribe")
    metadata.add_text("Description", "A chrysolic monk writing by candlelight")
    authored = theater.references_dir() / "scribe.png"
    old_scene = theater.scenes_dir() / "Harbor" / "1.png"
    latest_scene = theater.updated_scenes_dir() / "Harbor" / "2.png"
    portrait = theater.characters_dir() / "Mira" / "1.png"
    output = theater.image_artifacts_dir() / "generated_harbor.png"
    for path in (authored, old_scene, latest_scene, portrait, output):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(path, pnginfo=metadata if path == authored else None)
    manager = ReferenceManager(theater)
    canvas = MagicMock(spec=CanvasStateManager)
    tool = ReferenceTool(theater, manager, canvas_manager=canvas)

    entries = tool.list_references()
    assert {entry["path"] for entry in entries} == {str(authored), str(latest_scene), str(portrait)}
    assert tool.search_references("SCRIBE")[0]["title"] == "The Candlelit Scribe"
    assert tool.search_references("chrysolic")[0]["description"] == "A chrysolic monk writing by candlelight"
    assert tool.search_references("<Harbor>")[0]["path"] == str(latest_scene)
    assert tool.search_references("Mira")[0]["alias"] == "<Mira>"
    assert tool.search_references("generated_harbor") == []
    assert tool.search_references("") == entries
    canvas.visual.update_visual.assert_not_called()
