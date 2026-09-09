"""Boundary tests for the story subsystem composition root."""

import unittest
from unittest.mock import MagicMock, patch

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import TextResponseProvider
from tools.story.story_tool import StoryPlanningTools, StoryResponseTool, StoryTool


class TestStoryToolComposition(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "boundary_theater"
        self.theater.config.return_value = {"story_planning": {"session_id": "shared-session"}}
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.provider = MagicMock(spec=TextResponseProvider)

    def test_initializes_modules_with_shared_dependencies(self) -> None:
        with (
            patch("tools.story.story_tool.CharacterManager") as character_type,
            patch("tools.story.story_tool.LoreLibrary") as lore_type,
            patch("tools.story.story_tool.StoryPlanningModule") as planning_type,
            patch("tools.story.story_tool.StoryResponseModule") as response_type,
        ):
            lore = lore_type.return_value
            characters = character_type.return_value
            planning = planning_type.return_value
            response = response_type.return_value

            tool = StoryTool(self.theater, self.canvas, self.provider)

        lore_type.assert_called_once_with(theater=self.theater)
        character_type.assert_called_once_with(
            text_response_provider=self.provider,
            config={"session_id": "shared-session"},
        )
        planning_kwargs = planning_type.call_args.kwargs
        response_kwargs = response_type.call_args.kwargs
        self.assertIs(planning_kwargs["lore_library"], lore)
        self.assertIs(response_kwargs["lore_library"], lore)
        self.assertIs(planning_kwargs["character_manager"], characters)
        self.assertIs(response_kwargs["character_manager"], characters)
        self.assertIs(planning_kwargs["text_response_provider"], self.provider)
        self.assertIs(response_kwargs["text_response_provider"], self.provider)
        self.assertIs(response_kwargs["planning_module"], planning)
        self.assertIs(planning_kwargs["notepad"], tool.notepad)
        self.assertIs(response_kwargs["notepad"], tool.notepad)
        self.assertIsNot(
            planning_kwargs["session_service"],
            response_kwargs["session_service"],
        )
        self.assertEqual(planning_kwargs["session_id"], "shared-session_planning")
        self.assertEqual(response_kwargs["session_id"], "shared-session_response")
        self.assertNotEqual(planning_kwargs["session_id"], response_kwargs["session_id"])
        self.assertNotIn("config", planning_kwargs)
        self.assertIs(tool.lore_library, lore)
        self.assertIs(tool.character_manager, characters)
        self.assertIs(tool.planning_module, planning)
        self.assertIs(tool.response_module, response)

    def test_wires_cross_module_callbacks_after_construction(self) -> None:
        with (
            patch("tools.story.story_tool.CharacterManager") as character_type,
            patch("tools.story.story_tool.LoreLibrary"),
            patch("tools.story.story_tool.StoryPlanningModule") as planning_type,
            patch("tools.story.story_tool.StoryResponseModule") as response_type,
        ):
            planning = planning_type.return_value
            response = response_type.return_value
            characters = character_type.return_value
            tool = StoryTool(self.theater, self.canvas, self.provider)

        self.assertIs(characters.elements_provider.__self__, tool)
        self.assertIs(
            characters.elements_provider.__func__,
            StoryTool.get_present_elements,
        )
        self.assertIs(characters.on_change, response.save_to_session_state)
        self.assertIs(planning.recent_story_log_fn, response._format_recent_story_log)
        self.assertIs(planning.on_save_state, response.save_to_session_state)

        callback = MagicMock()
        tool.on_scene_reaction = callback
        self.assertIs(response.on_scene_reaction, callback)

    def test_delegates_public_surface_to_response_module(self) -> None:
        with (
            patch("tools.story.story_tool.CharacterManager"),
            patch("tools.story.story_tool.LoreLibrary"),
            patch("tools.story.story_tool.StoryPlanningModule"),
            patch("tools.story.story_tool.StoryResponseModule") as response_type,
        ):
            response_type.return_value.process_user_action.return_value = {"narration": "Done"}
            tool = StoryTool(self.theater, self.canvas, self.provider)

        result = tool.process_user_action("Open the door")

        self.assertEqual(result, {"narration": "Done"})
        response_type.return_value.process_user_action.assert_called_once_with("Open the door")

    def test_requires_text_response_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "text_response_provider is required"):
            StoryTool(self.theater, self.canvas, None)  # type: ignore[arg-type]

    def test_keeps_legacy_story_tool_aliases(self) -> None:
        self.assertIs(StoryPlanningTools, StoryTool)
        self.assertIs(StoryResponseTool, StoryTool)


class TestStoryToolStateIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.story_state = StoryState()
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.canvas.story = self.story_state
        self.canvas.tool_response = MagicMock()

        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "integration_theater"
        self.theater.lore_documents.return_value = []
        self.theater.config.return_value = {
            "adventure_mode": True,
            "initial_sticky_notes": {
                "HUD": "HP: 100 | MP: 50",
                "Quest": "Investigate the tower",
            },
        }
        self.provider = MagicMock(spec=TextResponseProvider)
        self.tool = StoryTool(
            theater=self.theater,
            canvas_manager=self.canvas,
            text_response_provider=self.provider,
        )

    def test_saves_and_reloads_story_planning_state(self) -> None:
        self.tool.generate_character(
            name="Lyra",
            description="Mystic scholar",
            personality="Curious",
            motivation="Seek forbidden knowledge",
            quirk="Always examining books",
            voice_tags=["female"],
        )
        self.tool.update_sticky_note(
            "Quest",
            "Tower investigated; key retrieved",
        )

        self.tool.save_to_session_state()

        persisted = self.story_state.get_story_planning_state()
        self.assertIn("sticky_notes", persisted)
        self.assertEqual(len(persisted["characters"]), 1)
        self.assertEqual(persisted["characters"][0]["name"], "Lyra")

        quest_sticky = next(
            sticky
            for sticky in self.story_state.sticky_notes()
            if (sticky.get("topic") or sticky.get("name")) == "Quest"
        )
        self.assertEqual(
            quest_sticky.get("info") or quest_sticky.get("content"),
            "Tower investigated; key retrieved",
        )
        self.assertEqual(
            self.story_state.get_character_voice_tags("Lyra"),
            ["female"],
        )
        self.assertIn(
            "Mystic scholar",
            self.story_state.get_character_description("Lyra"),
        )

        reloaded_tool = StoryTool(
            theater=self.theater,
            canvas_manager=self.canvas,
            text_response_provider=self.provider,
        )
        reloaded_tool.reload_from_session_state()

        characters = reloaded_tool.get_present_characters()
        self.assertEqual(len(characters), 1)
        self.assertEqual(characters[0]["name"], "Lyra")
        reloaded_quest = next(
            sticky
            for sticky in reloaded_tool.get_present_sticky_notes()
            if sticky["topic"] == "Quest"
        )
        self.assertEqual(
            reloaded_quest["info"],
            "Tower investigated; key retrieved",
        )

    def test_publishes_scene_to_story_state(self) -> None:
        narration = "The gates of the iron citadel open with a shuddering roar."
        dialogue = [
            {
                "speaker": "Lyra",
                "text": "Prepare yourselves.",
                "kind": "speech",
            }
        ]

        self.tool._publish_scene(narration, dialogue)

        self.assertEqual(self.story_state.narration, narration)
        self.assertEqual(len(self.story_state.scene_dialogue), 1)
        self.assertEqual(self.story_state.scene_dialogue[0]["speaker"], "Lyra")
        self.assertEqual(
            self.story_state.scene_dialogue[0]["text"],
            "Prepare yourselves.",
        )


if __name__ == "__main__":
    unittest.main()
