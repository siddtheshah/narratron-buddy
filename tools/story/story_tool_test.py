"""Boundary tests for the story subsystem composition root."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.reference_manager import ReferenceManager
from components.theater_manager import Theater, TheaterManager
from providers import ImageProvider, SpeechProvider, TextResponseProvider
from tools.story.story_tool import StoryTool


class TestStoryToolStateIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.story_state = StoryState()
        self.text_beautifier = MagicMock()
        self.text_beautifier.beautify_scene.side_effect = (
            lambda narration, dialogue: {
                "narration": narration,
                "narration_spans": (
                    [{"text": narration, "effect": "none", "font": "default", "color": None}]
                    if narration
                    else []
                ),
                "dialogue": [
                    {
                        **item,
                        "spans": [
                            {
                                "text": str(item.get("text") or ""),
                                "effect": "none",
                                "font": "default",
                                "color": None,
                            }
                        ],
                    }
                    for item in (dialogue or [])
                ],
            }
        )
        self.story_state.text_beautifier = self.text_beautifier
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.canvas.notepad = None
        self.canvas.story = self.story_state
        self.canvas.tool_response = MagicMock()

        self.theater_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.theater_directory.cleanup)
        self.theater = MagicMock(
            spec=Theater,
            wraps=TheaterManager(Path(self.theater_directory.name)).theater("test_theater"),
        )
        self.theater.theater_id = "integration_theater"
        self.theater.lore_documents.return_value = []
        self.theater.config.return_value = {
            "story_planning": {"adventure_mode": True},
        }
        self.theater.read_planning_schema.return_value = {
            "HUD": {"owner": "recorder", "initial": "HP: 100 | MP: 50"},
            "Quest": {"owner": "planner", "initial": "Investigate the tower"},
        }
        self.provider = MagicMock(spec=TextResponseProvider)
        self.image_provider = MagicMock(spec=ImageProvider)
        self.speech_provider = MagicMock(spec=SpeechProvider)
        self.speech_provider.select_voice.return_value = "voice_default"
        self.reference_manager = ReferenceManager(
            theater=self.theater,
            text_response_provider=self.provider,
            canvas_manager=self.canvas,
            story_state=self.story_state,
            image_provider=self.image_provider,
            speech_provider=self.speech_provider,
        )
        self.tool = StoryTool(
            theater=self.theater,
            canvas_manager=self.canvas,
            text_response_provider=self.provider,
            reference_manager=self.reference_manager,
        )

    def tearDown(self) -> None:
        self.tool.close()

    def test_saves_and_reloads_story_planning_state(self) -> None:
        self.tool.create_or_update_character(
            name="Lyra",
            description="Mystic scholar",
            personality="Curious",
            motivation="Seek forbidden knowledge",
            quirk="Always examining books",
            voice_tags=["female"],
        )
        self.tool.reference_manager.wait_for_reference_updates(["Lyra"], timeout=5)
        self.tool.notepad.update_sticky_note(
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
            for sticky in self.tool.get_present_sticky_notes()
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

        reloaded_reference_manager = ReferenceManager(
            theater=self.theater,
            text_response_provider=self.provider,
            canvas_manager=self.canvas,
            story_state=self.story_state,
            image_provider=self.image_provider,
            speech_provider=self.speech_provider,
        )
        reloaded_tool = StoryTool(
            theater=self.theater,
            canvas_manager=self.canvas,
            text_response_provider=self.provider,
            reference_manager=reloaded_reference_manager,
        )
        self.addCleanup(reloaded_tool.close)
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
        self.tool.reference_manager.speech_provider = speech_provider

        scene_delta = {
            "narration": "A hooded figure emerges from the fog.",
            "dialogue": [{"speaker": "Vesper", "text": "Who goes there?"}],
            "manifested_characters": ["Vesper"],
            "planning_signals": [],
            "scene_reference": "Foggy Crossroads",
        }

        def fake_run(action: str, nudge: str = "") -> dict[str, object]:
            self.tool.create_or_update_character(name="Vesper", gender="female")
            return scene_delta

        with patch.object(self.tool.response_module, "_run_responder_agent", side_effect=fake_run):
            self.tool.response_module._resolve_user_action("I call out.")
        self.tool.reference_manager.wait_for_reference_updates(["Vesper"], timeout=5)

        # Vesper's voice was assigned using female tag
        speech_provider.select_voice.assert_called_with(
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
        self.tool.reference_manager.speech_provider = speech_provider

        scene_delta = {
            "narration": "A spirit drifts near.",
            "dialogue": [{"speaker": "Echo", "text": "Listen closely."}],
            "manifested_characters": ["Echo"],
            "planning_signals": [],
            "scene_reference": "Spirit Grove",
        }

        def fake_run(action: str, nudge: str = "") -> dict[str, object]:
            self.tool.create_or_update_character(name="Echo", gender="nonbinary")
            return scene_delta

        with patch.object(self.tool.response_module, "_run_responder_agent", side_effect=fake_run):
            self.tool.response_module._resolve_user_action("I listen.")
        self.tool.reference_manager.wait_for_reference_updates(["Echo"], timeout=5)

        speech_provider.select_voice.assert_called_with(
            {
                "voice_tags": ["nonbinary"],
                "description": unittest.mock.ANY,
            },
            exclude=set(),
        )
        self.assertEqual(self.story_state.get_character_voice("Echo"), "voice_nb_1")

    def test_sync_character_voice_tags_updates_story_state(self) -> None:
        self.tool.reference_manager.import_characters([
            {"name": "Gwen", "gender": "female"},
            {"name": "Boran", "gender": "male"},
            {"name": "Zephyr", "gender": "nonbinary"},
        ])
        self.tool.sync_character_voice_tags()

        self.assertEqual(self.story_state.get_character_voice_tags("Gwen"), ["female"])
        self.assertEqual(self.story_state.get_character_voice_tags("Boran"), ["male"])
        self.assertEqual(self.story_state.get_character_voice_tags("Zephyr"), ["nonbinary"])

    def test_create_or_update_scene_delegation(self) -> None:
        with patch.object(self.tool.response_module, "create_or_update_scene", return_value="Scene 'Citadel' is set.") as mock_scene:
            result = self.tool.create_or_update_scene("Citadel", "Ancient fortress")
            mock_scene.assert_called_once_with(name="Citadel", description="Ancient fortress")
            self.assertEqual(result, "Scene 'Citadel' is set.")


if __name__ == "__main__":
    unittest.main()

