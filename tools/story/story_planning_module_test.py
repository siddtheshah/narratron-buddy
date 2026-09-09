"""Dependency-boundary tests for ``StoryPlanningModule``."""

import unittest
from unittest.mock import MagicMock, patch

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from google.adk.sessions import InMemorySessionService
from tools.story.character_manager import CharacterManager
from tools.story.lore_library import LoreLibrary
from tools.story.notepad import Notepad
from tools.story.story_planning_module import StoryPlanningModule, VertexGemini


class TestStoryPlanningModuleDependencies(unittest.TestCase):
    def test_vertex_gemini_initializes_and_reuses_client_cache(self) -> None:
        model = VertexGemini(
            model="gemini-test",
            project_id="test-project",
            location="global",
        )
        client = MagicMock()
        with patch(
            "tools.story.story_models.genai.Client",
            return_value=client,
        ) as create_client:
            self.assertIs(model.api_client, client)
            self.assertIs(model.api_client, client)

        create_client.assert_called_once_with(
            project="test-project",
            location="global",
            vertexai=True,
        )

    def _make_module(
        self,
        lore_library: LoreLibrary,
        config: dict | None = None,
    ) -> StoryPlanningModule:
        theater = MagicMock(spec=Theater)
        theater.theater_id = "planning-boundary"
        theater.config.return_value = config or {"adventure_mode": False}
        theater.read_planning_schema.return_value = None
        with (
            patch.object(StoryPlanningModule, "_create_deep_planner_agent"),
            patch("tools.story.story_planning_module.App"),
            patch("tools.story.story_planning_module.Runner"),
        ):
            canvas = MagicMock(spec=CanvasStateManager)
            return StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                character_manager=MagicMock(spec=CharacterManager),
                session_service=MagicMock(spec=InMemorySessionService),
                session_id="planning-boundary-session",
                notepad=Notepad(theater, canvas_manager=canvas),
            )

    def test_uses_injected_provider_and_lore_library(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        character_manager = MagicMock(spec=CharacterManager)
        theater = MagicMock(spec=Theater)
        theater.theater_id = "planning-boundary"
        theater.config.return_value = {"story_planning": {"max_sticky_notes": 7}}
        theater.read_planning_schema.return_value = None
        canvas = MagicMock(spec=CanvasStateManager)
        session_service = MagicMock(spec=InMemorySessionService)

        with (
            patch.object(StoryPlanningModule, "_create_deep_planner_agent") as create_agent,
            patch("tools.story.story_planning_module.App"),
            patch("tools.story.story_planning_module.Runner"),
        ):
            module = StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                character_manager=character_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
                notepad=Notepad(theater, canvas_manager=canvas),
            )

        self.assertIs(module.lore_library, lore_library)
        self.assertIs(module.character_manager, character_manager)
        self.assertIs(module.theater, theater)
        self.assertIs(module.canvas_manager, canvas)
        self.assertIs(module.session_service, session_service)
        self.assertEqual(module.session_id, "planning-boundary-session")
        self.assertEqual(module.notepad.max_sticky_notes, 7)
        theater.config.assert_called_once_with()
        create_agent.assert_called_once_with()

        character_manager.lookup_character.return_value = "Lyra: Mystic scholar"
        self.assertEqual(module._lookup_character("Lyra"), "Lyra: Mystic scholar")
        character_manager.lookup_character.assert_called_once_with("Lyra")

    def test_wraps_shared_lore_operations_with_its_own_run_budgets(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        lore_library.read_lore.return_value = "read result"
        lore_library.search_lore.return_value = "search result"
        module = self._make_module(lore_library)

        self.assertEqual([module.deep_read_lore("lore.txt") for _ in range(3)], [
            "read result",
            "read result",
            "read result",
        ])
        self.assertIn("Maximum deep_read_lore limit (3) reached", module.deep_read_lore("lore.txt"))
        self.assertEqual([module.deep_search_lore("clue") for _ in range(3)], [
            "search result",
            "search result",
            "search result",
        ])
        self.assertIn("Maximum deep_search_lore limit (3) reached", module.deep_search_lore("clue"))
        self.assertEqual(lore_library.read_lore.call_count, 3)
        self.assertEqual(lore_library.search_lore.call_count, 3)

        module.reset_deep_lore_call_counts()
        self.assertEqual(module.deep_read_lore("lore.txt"), "read result")
        self.assertEqual(module.deep_search_lore("clue"), "search result")

    def test_rejects_missing_injected_dependencies(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        character_manager = MagicMock(spec=CharacterManager)
        theater = MagicMock(spec=Theater)
        theater.theater_id = "planning-boundary"
        theater.config.return_value = {}
        theater.read_planning_schema.return_value = None
        canvas = MagicMock(spec=CanvasStateManager)
        session_service = MagicMock(spec=InMemorySessionService)
        notepad = Notepad(theater, canvas_manager=canvas)

        with self.assertRaisesRegex(ValueError, "theater is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=None,
                canvas_manager=canvas,
                lore_library=lore_library,
                character_manager=character_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
                notepad=notepad,
            )
        with self.assertRaisesRegex(ValueError, "canvas_manager is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=None,
                lore_library=lore_library,
                character_manager=character_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
                notepad=notepad,
            )
        with self.assertRaisesRegex(ValueError, "lore_library is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=None,
                character_manager=character_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
                notepad=notepad,
            )
        with self.assertRaisesRegex(ValueError, "character_manager is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                character_manager=None,
                session_service=session_service,
                session_id="planning-boundary-session",
                notepad=notepad,
            )
        with self.assertRaisesRegex(ValueError, "session_service is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                character_manager=character_manager,
                session_service=None,
                session_id="planning-boundary-session",
                notepad=notepad,
            )
        with self.assertRaisesRegex(ValueError, "session_id is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                character_manager=character_manager,
                session_service=session_service,
                session_id="",
                notepad=notepad,
            )
        with self.assertRaisesRegex(ValueError, "notepad is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                character_manager=character_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
                notepad=None,
            )


class TestStoryPlanningModuleState(unittest.TestCase):
    def setUp(self) -> None:
        self.lore_library = MagicMock(spec=LoreLibrary)
        self.character_manager = MagicMock(spec=CharacterManager)
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "planning_theater"
        self.theater.read_planning_schema.return_value = None
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.session_service = InMemorySessionService()
        self.config = {
            "adventure_mode": True,
            "max_sticky_notes": 4,
            "required_stickies": ["HUD", "Location"],
            "initial_sticky_notes": {
                "HUD": "HP: 100 | MP: 50",
                "Location": "Whispering Woods",
                "Quest": "Find the ancient relic",
            },
        }
        self.theater.config.return_value = self.config
        self.module = self._make_module(session_id="planning-state-session")

    def _make_module(
        self,
        *,
        theater: Theater | None = None,
        session_id: str,
    ) -> StoryPlanningModule:
        return StoryPlanningModule(
            theater=theater or self.theater,
            canvas_manager=self.canvas,
            lore_library=self.lore_library,
            character_manager=self.character_manager,
            session_service=self.session_service,
            session_id=session_id,
            notepad=Notepad(theater or self.theater, canvas_manager=self.canvas),
        )

    def test_initial_sticky_notes_and_required(self) -> None:
        notes = self.module.notepad.get_present_sticky_notes()

        self.assertEqual(len(notes), 3)
        self.assertEqual(
            self.module.notepad.get_required_sticky_notes(),
            ["HUD", "Location"],
        )
        self.assertEqual(
            {note["topic"] for note in notes},
            {"HUD", "Location", "Quest"},
        )

    def test_updates_structured_sticky_note_and_validates_dividers(self) -> None:
        result = self.module.notepad.update_sticky_note("HUD", "HP: 80 | MP: 40")

        self.assertIn("Updated sticky note 'HUD'", result)
        hud_note = next(
            note
            for note in self.module.notepad.get_present_sticky_notes()
            if note["topic"] == "HUD"
        )
        self.assertEqual(hud_note["info"], "HP: 80 | MP: 40")
        self.assertIn(
            "Error: Sticky note divider count mismatch",
            self.module.notepad.update_sticky_note("HUD", "HP: 80"),
        )

    def test_capacity_enforcement_drops_oldest_non_required_note(self) -> None:
        self.module.notepad.update_sticky_note("Clue1", "Found a footprint")
        self.module.notepad.update_sticky_note("Clue2", "Found a dagger")

        topics = [
            note["topic"] for note in self.module.notepad.get_present_sticky_notes()
        ]
        self.assertEqual(topics, ["HUD", "Location", "Clue1", "Clue2"])

    def test_loads_structured_sticky_definitions_from_config(self) -> None:
        self.theater.config.return_value = {
            "adventure_mode": True,
            "planning_schema": {
                "Stats": {
                    "required": True,
                    "fields": {
                        "hp": "Hit points",
                        "level": "Player level",
                    },
                    "initial": {"hp": "100", "level": "1"},
                    "render": "HP: {hp} | LVL: {level}",
                },
                "Inventory": {
                    "required": False,
                    "description": "Simple string list of items",
                    "initial": "Sword, Shield",
                },
            },
        }
        module = self._make_module(session_id="structured-planning-session")

        notes = module.notepad.get_present_sticky_notes()
        stats_note = next(note for note in notes if note["topic"] == "Stats")
        inventory_note = next(
            note for note in notes if note["topic"] == "Inventory"
        )
        self.assertEqual(stats_note["info"], "HP: 100 | LVL: 1")
        self.assertEqual(inventory_note["info"], "Sword, Shield")
        self.assertEqual(
            module.notepad.get_present_structured_sticky_notes(),
            {
                "Stats": {"hp": "100", "level": "1"},
                "Inventory": "Sword, Shield",
            },
        )

    def test_loads_theater_schema_without_filtering_canvas_hidden_stickies(self) -> None:
        theater = MagicMock(spec=Theater)
        theater.theater_id = "schema_theater"
        theater.read_planning_schema.return_value = {
            "Clock": {
                "required": True,
                "fields": {
                    "time": "Current time",
                    "deadline": "Summit deadline",
                },
                "initial": {"time": "9:00 AM", "deadline": "3:00 PM"},
                "render": "Time: {time} | Deadline: {deadline}",
            },
            "Contraband": {
                "required": False,
                "initial": "Quill, Turnip",
            },
            "Secret Plot": {
                "required": False,
                "initial": "Mole investigating",
            },
        }
        theater.config.return_value = {
            "adventure_mode": True,
            "hidden_stickies": ["Secret Plot"],
        }
        module = self._make_module(
            theater=theater,
            session_id="theater-schema-session",
        )

        self.assertEqual(len(module.notepad.get_present_sticky_notes()), 3)
        self.assertEqual(
            {note["topic"] for note in module.notepad.get_present_sticky_notes()},
            {"Clock", "Contraband", "Secret Plot"},
        )

        exported = module.export_planning_state()
        self.assertIn(
            "Secret Plot",
            {note["topic"] for note in exported["sticky_notes"]},
        )
        self.assertIn(
            "Secret Plot",
            {note["topic"] for note in exported["all_sticky_notes"]},
        )

        committed = module._commit_deep_plan_update(
            1,
            {
                "sticky_notes": {
                    "Clock": {"time": "10:00 AM", "deadline": "3:00 PM"},
                    "Contraband": "Quill, Poison Phial",
                    "Secret Plot": "Mole discovered in archives",
                },
            },
        )
        self.assertTrue(committed)
        updated_notes = module.notepad.get_present_sticky_notes()
        clock_note = next(
            note for note in updated_notes if note["topic"] == "Clock"
        )
        contraband_note = next(
            note for note in updated_notes if note["topic"] == "Contraband"
        )
        self.assertEqual(
            clock_note["info"],
            "Time: 10:00 AM | Deadline: 3:00 PM",
        )
        self.assertEqual(contraband_note["info"], "Quill, Poison Phial")

    def test_commit_deep_plan_update_discards_legacy_plot_beats(self) -> None:
        committed = self.module._commit_deep_plan_update(
            1,
            {
                "plot_beats": [
                    "A bat screeches overhead.",
                    "Water drips from the ceiling.",
                ],
                "sticky_notes": [
                    {"topic": "HUD", "info": "HP: 100 | MP: 50"},
                    {"topic": "Location", "info": "Deep Cavern"},
                ],
            },
        )

        self.assertTrue(committed)
        plan = self.module.get_deep_plan()
        self.assertEqual(plan["revision"], 1)
        self.assertEqual(plan["through_turn_id"], 1)
        self.assertNotIn("plot_beats", plan)
        self.assertNotIn("plot_beats", self.module.export_planning_state())

    def test_tool_driven_commit_keeps_unmodified_stickies(self) -> None:
        self.module.notepad.update_sticky_note("Quest", "The relic is now guarded")

        self.assertTrue(self.module._commit_deep_plan_update(1, {"tool_updates": True}))
        notes = {
            note["topic"]: note["info"]
            for note in self.module.notepad.get_present_sticky_notes()
        }
        self.assertEqual(notes["Quest"], "The relic is now guarded")
        self.assertEqual(notes["HUD"], "HP: 100 | MP: 50")
        self.assertEqual(self.module.get_deep_plan()["through_turn_id"], 1)


if __name__ == "__main__":
    unittest.main()
