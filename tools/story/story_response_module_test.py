"""Dependency-boundary tests for ``StoryResponseModule``."""

import unittest
from unittest.mock import MagicMock, patch

from google.adk.sessions import InMemorySessionService

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import TextResponseProvider
from tools.story.character_manager import CharacterManager
from tools.story.lore_library import LoreLibrary
from tools.story.story_planning_module import StoryPlanningModule
from tools.story.story_response_module import StoryResponseModule


class TestStoryResponseModuleDependencies(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "response_boundary"
        self.theater.config.return_value = {"story_planning": {"adventure_mode": False}}
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.provider = MagicMock(spec=TextResponseProvider)
        self.lore_library = MagicMock(spec=LoreLibrary)
        self.planning_module = MagicMock(spec=StoryPlanningModule)
        self.character_manager = MagicMock(spec=CharacterManager)
        self.session_service = MagicMock(spec=InMemorySessionService)
        self.session_id = "response-boundary-session"

    def test_uses_injected_dependencies(self) -> None:
        with (
            patch.object(StoryResponseModule, "_create_responder_agent"),
            patch.object(StoryResponseModule, "_read_recent_story_log", return_value=[]),
            patch.object(StoryResponseModule, "reload_from_session_state"),
            patch("tools.story.story_response_module.App"),
            patch("tools.story.story_response_module.Runner"),
        ):
            module = StoryResponseModule(
                theater=self.theater,
                canvas_manager=self.canvas,
                text_response_provider=self.provider,
                planning_module=self.planning_module,
                lore_library=self.lore_library,
                character_manager=self.character_manager,
                session_service=self.session_service,
                session_id=self.session_id,
            )

        self.assertIs(module.text_response_provider, self.provider)
        self.assertIs(module.planning_module, self.planning_module)
        self.assertIs(module.lore_library, self.lore_library)
        self.assertIs(module.character_manager, self.character_manager)
        self.assertIs(module.session_service, self.session_service)
        self.assertEqual(module.session_id, self.session_id)

    def test_rejects_missing_injected_dependencies(self) -> None:
        with self.assertRaisesRegex(ValueError, "planning_module is required"):
            StoryResponseModule(  # type: ignore[arg-type]
                theater=self.theater,
                canvas_manager=self.canvas,
                text_response_provider=self.provider,
                planning_module=None,
                lore_library=self.lore_library,
                character_manager=self.character_manager,
                session_service=self.session_service,
                session_id=self.session_id,
            )
        with self.assertRaisesRegex(ValueError, "lore_library is required"):
            StoryResponseModule(  # type: ignore[arg-type]
                theater=self.theater,
                canvas_manager=self.canvas,
                text_response_provider=self.provider,
                planning_module=self.planning_module,
                lore_library=None,
                character_manager=self.character_manager,
                session_service=self.session_service,
                session_id=self.session_id,
            )
        with self.assertRaisesRegex(ValueError, "character_manager is required"):
            StoryResponseModule(  # type: ignore[arg-type]
                theater=self.theater,
                canvas_manager=self.canvas,
                text_response_provider=self.provider,
                planning_module=self.planning_module,
                lore_library=self.lore_library,
                character_manager=None,
                session_service=self.session_service,
                session_id=self.session_id,
            )
        with self.assertRaisesRegex(ValueError, "session_service is required"):
            StoryResponseModule(  # type: ignore[arg-type]
                theater=self.theater,
                canvas_manager=self.canvas,
                text_response_provider=self.provider,
                planning_module=self.planning_module,
                lore_library=self.lore_library,
                character_manager=self.character_manager,
                session_service=None,
                session_id=self.session_id,
            )
        with self.assertRaisesRegex(ValueError, "session_id is required"):
            StoryResponseModule(
                theater=self.theater,
                canvas_manager=self.canvas,
                text_response_provider=self.provider,
                planning_module=self.planning_module,
                lore_library=self.lore_library,
                character_manager=self.character_manager,
                session_service=self.session_service,
                session_id="",
            )


class TestStoryResponseModuleBehavior(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "response_theater"
        self.theater.lore_documents.return_value = ["lore.txt"]
        self.theater.read_lore_document.return_value = (
            "Lore details about the realm."
        )
        self.config = {
            "adventure_mode": True,
            "initial_sticky_notes": {"HUD": "HP: 100 | MP: 50"},
        }
        self.theater.config.return_value = self.config

        self.canvas = MagicMock(spec=CanvasStateManager)
        self.story_state = MagicMock(spec=StoryState)
        self.canvas.story = self.story_state
        self.canvas.tool_response = MagicMock()
        self.provider = MagicMock(spec=TextResponseProvider)
        self.lore_library = LoreLibrary(theater=self.theater)
        self.character_manager = CharacterManager(
            text_response_provider=self.provider,
            config=self.config,
        )
        self.planning_module = StoryPlanningModule(
            theater=self.theater,
            canvas_manager=self.canvas,
            text_response_provider=self.provider,
            lore_library=self.lore_library,
            character_manager=self.character_manager,
            session_service=InMemorySessionService(),
            session_id="response-test-planning-session",
        )
        self.module = StoryResponseModule(
            theater=self.theater,
            canvas_manager=self.canvas,
            text_response_provider=self.provider,
            planning_module=self.planning_module,
            lore_library=self.lore_library,
            character_manager=self.character_manager,
            session_service=InMemorySessionService(),
            session_id="response-test-session",
        )

    def test_rolls_and_resets_dice(self) -> None:
        roll = self.module.roll_dice(
            sides=20,
            count=2,
            modifier=3,
            reason="perception check",
        )

        self.assertEqual(roll["sides"], 20)
        self.assertEqual(roll["count"], 2)
        self.assertEqual(roll["modifier"], 3)
        self.assertEqual(roll["total"], sum(roll["rolls"]) + 3)
        self.assertEqual(len(self.module.get_die_rolls_this_turn()), 1)

        self.module.reset_die_roll_counts()
        self.assertEqual(self.module.get_die_rolls_this_turn(), [])

    def test_owns_response_lore_budget_and_activity(self) -> None:
        for _ in range(3):
            self.assertIn("Lore details", self.module.read_lore("lore.txt"))

        self.assertIn(
            "Maximum read_lore call limit (3) reached",
            self.module.read_lore("lore.txt"),
        )
        self.assertEqual(len(self.module.get_lore_activity_this_turn()), 1)
        self.assertEqual(
            self.module.get_lore_docs_browsed_this_turn(),
            ["lore.txt"],
        )

        self.assertIn("Lore details", self.lore_library.read_lore("lore.txt"))
        self.assertIn(
            "Lore details",
            self.planning_module.deep_read_lore("lore.txt"),
        )

        self.module.reset_lore_call_counts()
        self.assertIn("Lore details", self.module.read_lore("lore.txt"))

    def test_delegates_character_management_to_shared_manager(self) -> None:
        self.module.generate_character(
            name="Kaelen",
            description="A stoic ranger",
            personality="Taciturn",
            motivation="Protect the woods",
            quirk="Whistles old tunes",
            voice_tags=["male"],
        )

        characters = self.module.get_present_characters()
        self.assertEqual(len(characters), 1)
        self.assertEqual(characters[0]["name"], "Kaelen")
        self.assertEqual(characters[0]["voice_tags"], ["male"])
        self.assertIn("Kaelen", self.module.lookup_character("ranger"))
        self.assertIn("Taciturn", self.module.lookup_character("ranger"))

        self.module.clear_scene()
        self.assertEqual(self.module.get_present_characters(), [])

    def test_resolves_user_action_and_queues_planning(self) -> None:
        scene_delta = {
            "narration": "You slip through the shadowy arches of the ruined shrine.",
            "dialogue": [{"speaker": "Kaelen", "text": "Stay quiet."}],
            "manifested_characters": ["Kaelen"],
            "character_updates": [],
            "planning_signals": ["Shrine discovered", "Kaelen scouted"],
            "scene_label": "Ruined Shrine",
        }

        with (
            patch.object(
                self.module,
                "_run_responder_agent",
                return_value=scene_delta,
            ),
            patch.object(
                self.planning_module,
                "queue_deep_planning",
            ) as queue_planning,
        ):
            result = self.module._resolve_user_action("I sneak into the shrine.")

        self.assertEqual(result["narration"], scene_delta["narration"])
        self.assertEqual(result["scene_label"], "Ruined Shrine")
        self.story_state.set_scene.assert_called_once_with(
            scene_delta["narration"],
            [{"speaker": "Kaelen", "text": "Stay quiet.", "kind": "speech"}],
        )
        queue_planning.assert_called_once_with(
            1,
            "I sneak into the shrine.",
            result,
        )


if __name__ == "__main__":
    unittest.main()
