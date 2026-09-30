"""Boundary tests for the story subsystem composition root."""

import unittest
from unittest.mock import MagicMock, patch

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import ImageProvider, SpeechProvider, TextResponseProvider
from tools.story.story_tool import StoryPlanningTools, StoryResponseTool, StoryTool


class TestStoryToolComposition(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "boundary_theater"
        self.theater.config.return_value = {"story_planning": {"session_id": "shared-session"}}
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.canvas.story = MagicMock(spec=StoryState)
        self.canvas.story.speech_provider = MagicMock(spec=SpeechProvider)
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
            notepad=tool.notepad,
            story_state=self.canvas.story,
            image_library=tool.image_library,
            image_provider=None,
            speech_provider=self.canvas.story.speech_provider,
            character_image_style="",
        )
        planning_kwargs = planning_type.call_args.kwargs
        response_kwargs = response_type.call_args.kwargs
        self.assertIs(planning_kwargs["lore_library"], lore)
        self.assertIs(response_kwargs["lore_library"], lore)
        self.assertIs(planning_kwargs["character_manager"], characters)
        self.assertIs(response_kwargs["character_manager"], characters)
        self.assertNotIn("text_response_provider", planning_kwargs)
        self.assertNotIn("text_response_provider", response_kwargs)
        self.assertNotIn("planning_module", response_kwargs)
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
            patch("tools.story.story_tool.StoryPlanningModule"),
            patch("tools.story.story_tool.StoryResponseModule") as response_type,
        ):
            response = response_type.return_value
            tool = StoryTool(self.theater, self.canvas, self.provider)

        self.assertIs(character_type.call_args.kwargs["notepad"], tool.notepad)

        callback = MagicMock()
        tool.on_scene_reaction = callback
        self.assertIs(tool.on_scene_reaction, callback)
        self.assertIs(response.on_scene_reaction.__self__, tool)

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

    def test_process_user_action_cycle_cooldown(self) -> None:
        with (
            patch("tools.story.story_tool.CharacterManager"),
            patch("tools.story.story_tool.LoreLibrary"),
            patch("tools.story.story_tool.StoryPlanningModule"),
            patch("tools.story.story_tool.StoryResponseModule") as response_type,
        ):
            response_type.return_value.process_user_action.return_value = {"narration": "Done"}
            tool = StoryTool(self.theater, self.canvas, self.provider)

        # First call executes immediately
        res1 = tool.process_user_action("First action")
        self.assertEqual(res1, {"narration": "Done"})

        # Second call within cooldown is scheduled
        res2 = tool.process_user_action("Second action")
        self.assertEqual(res2, "Tool 'process_user_action' scheduled for next cycle when cooldown expires.")

        # Third call modifies parameters for the next cycle
        res3 = tool.process_user_action("Third action", nudge="look closely")
        self.assertEqual(res3, "Tool 'process_user_action' parameters updated for next cycle.")

        pending = tool.get_pending_cycle_call("process_user_action")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["args"], ("Third action",))
        self.assertEqual(pending["kwargs"], {"nudge": "look closely"})

    def test_process_user_action_swallows_duplicate_calls(self) -> None:
        with (
            patch("tools.story.story_tool.CharacterManager"),
            patch("tools.story.story_tool.LoreLibrary"),
            patch("tools.story.story_tool.StoryPlanningModule"),
            patch("tools.story.story_tool.StoryResponseModule") as response_type,
        ):
            response_type.return_value.process_user_action.return_value = {"narration": "Done"}
            tool = StoryTool(self.theater, self.canvas, self.provider)

        # First call executes immediately
        res1 = tool.process_user_action("Open the ancient door")
        self.assertEqual(res1, {"narration": "Done"})
        response_type.return_value.process_user_action.assert_called_once_with("Open the ancient door")

        # Second call with slightly different phrasing / casing / punctuation is swallowed,
        # but returns standard scheduling confirmation to keep model interaction smooth
        res2 = tool.process_user_action("I open the ancient door.")
        self.assertEqual(res2, "Tool 'process_user_action' scheduled for next cycle when cooldown expires.")

        # Verify it was NOT enqueued for next cycle
        pending = tool.get_pending_cycle_call("process_user_action")
        self.assertIsNone(pending)

    def test_process_user_action_timeout_failure_disables_swallowing(self) -> None:
        with (
            patch("tools.story.story_tool.CharacterManager"),
            patch("tools.story.story_tool.LoreLibrary"),
            patch("tools.story.story_tool.StoryPlanningModule"),
            patch("tools.story.story_tool.StoryResponseModule") as response_type,
        ):
            response_type.return_value.process_user_action.return_value = {"status": "processing"}
            tool = StoryTool(self.theater, self.canvas, self.provider)

        # First call starts processing
        res1 = tool.process_user_action("Open the ancient door")
        self.assertEqual(res1, {"status": "processing"})

        # Background resolution times out and notifies scene reaction with error
        tool._handle_scene_reaction({
            "error": "Story responder timed out after 35.0 seconds. The story responder agent was killed and restarted."
        })

        # Retry call with identical action must NOT be swallowed because the previous call failed
        res2 = tool.process_user_action("Open the ancient door")
        self.assertEqual(res2, {"status": "processing"})
        self.assertEqual(response_type.return_value.process_user_action.call_count, 2)

        # Now simulate success
        tool._handle_scene_reaction({"narration": "The ancient door creaks open."})

        # Subsequent call with identical action SHOULD now be swallowed
        res3 = tool.process_user_action("Open the ancient door")
        self.assertEqual(res3, "Tool 'process_user_action' scheduled for next cycle when cooldown expires.")
        self.assertEqual(response_type.return_value.process_user_action.call_count, 2)


    def test_requires_text_response_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "text_response_provider is required"):
            StoryTool(self.theater, self.canvas, None)  # type: ignore[arg-type]

    def test_keeps_legacy_story_tool_aliases(self) -> None:
        self.assertIs(StoryPlanningTools, StoryTool)
        self.assertIs(StoryResponseTool, StoryTool)

    def test_exports_reflect_and_retry(self) -> None:
        from tools.story import ReflectAndRetry, ReflectAndRetryToolPlugin
        from google.adk.plugins import ReflectAndRetryToolPlugin as ADKPlugin

        self.assertIs(ReflectAndRetry, ADKPlugin)
        self.assertIs(ReflectAndRetryToolPlugin, ADKPlugin)


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
        self.image_provider = MagicMock(spec=ImageProvider)
        self.speech_provider = MagicMock(spec=SpeechProvider)
        self.speech_provider.select_voice.return_value = "voice_default"
        self.tool = StoryTool(
            theater=self.theater,
            canvas_manager=self.canvas,
            text_response_provider=self.provider,
            image_provider=self.image_provider,
            speech_provider=self.speech_provider,
        )

    def test_saves_and_reloads_story_planning_state(self) -> None:
        self.tool.create_or_update_character(
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
            image_provider=self.image_provider,
            speech_provider=self.speech_provider,
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

    def test_manifested_character_voice_selected_with_gender_in_process_user_action(self) -> None:
        speech_provider = MagicMock()
        speech_provider.select_voice.return_value = "voice_female_1"
        self.story_state.enable_scene_speech(speech_provider)
        self.tool.character_manager.speech_provider = speech_provider

        scene_delta = {
            "narration": "A hooded figure emerges from the fog.",
            "dialogue": [{"speaker": "Vesper", "text": "Who goes there?"}],
            "manifested_characters": ["Vesper"],
            "planning_signals": [],
            "scene_label": "Foggy Crossroads",
        }

        def fake_run(action: str, nudge: str = "") -> dict[str, object]:
            self.tool.create_or_update_character(name="Vesper", gender="female")
            return scene_delta

        with patch.object(self.tool.response_module, "_run_responder_agent", side_effect=fake_run):
            self.tool.response_module._resolve_user_action("I call out.")

        # Vesper's voice was assigned using female tag
        speech_provider.select_voice.assert_called_once_with(
            {
                "voice_tags": ["female"],
                "description": unittest.mock.ANY,
            },
            exclude=set(),
        )
        self.assertEqual(self.story_state.get_character_voice("Vesper"), "voice_female_1")

    def test_manifested_nonbinary_character_voice_selected_in_process_user_action(self) -> None:
        speech_provider = MagicMock()
        speech_provider.select_voice.return_value = "voice_nb_1"
        self.story_state.enable_scene_speech(speech_provider)
        self.tool.character_manager.speech_provider = speech_provider

        scene_delta = {
            "narration": "A spirit drifts near.",
            "dialogue": [{"speaker": "Echo", "text": "Listen closely."}],
            "manifested_characters": ["Echo"],
            "planning_signals": [],
            "scene_label": "Spirit Grove",
        }

        def fake_run(action: str, nudge: str = "") -> dict[str, object]:
            self.tool.create_or_update_character(name="Echo", gender="nonbinary")
            return scene_delta

        with patch.object(self.tool.response_module, "_run_responder_agent", side_effect=fake_run):
            self.tool.response_module._resolve_user_action("I listen.")

        speech_provider.select_voice.assert_called_once_with(
            {
                "voice_tags": ["nonbinary"],
                "description": unittest.mock.ANY,
            },
            exclude=set(),
        )
        self.assertEqual(self.story_state.get_character_voice("Echo"), "voice_nb_1")

    def test_sync_character_voice_tags_updates_story_state(self) -> None:
        self.tool.character_manager.import_characters([
            {"name": "Gwen", "gender": "female"},
            {"name": "Boran", "gender": "male"},
            {"name": "Zephyr", "gender": "nonbinary"},
        ])
        self.tool.sync_character_voice_tags()

        self.assertEqual(self.story_state.get_character_voice_tags("Gwen"), ["female"])
        self.assertEqual(self.story_state.get_character_voice_tags("Boran"), ["male"])
        self.assertEqual(self.story_state.get_character_voice_tags("Zephyr"), ["nonbinary"])


if __name__ == "__main__":
    unittest.main()

