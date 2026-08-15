import json
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tools.story_planning_tool import (
    DEFAULT_MAX_NAMED_ELEMENTS,
    SCENE_REACTION_SYSTEM_INSTRUCTION,
    SceneReaction,
    StoryPlanningTools,
    VertexGemini,
    build_story_context_prompt,
)
from components.theater_manager import TheaterManager


class TestStoryPlanningTools(unittest.TestCase):
    def test_planner_instruction_reserves_player_agency(self):
        self.assertIn("Never invent the player's actions, speech, thoughts, feelings, decisions", SCENE_REACTION_SYSTEM_INSTRUCTION)
        self.assertIn("Dialogue may be spoken only by NPCs", SCENE_REACTION_SYSTEM_INSTRUCTION)
        self.assertIn("'yes, and' improv posture", SCENE_REACTION_SYSTEM_INSTRUCTION)

    def test_planner_model_uses_explicit_vertex_client(self):
        with patch("tools.story_planning_tool.genai.Client") as client:
            model = VertexGemini(model="gemini-2.5-flash", project_id="test-project", location="global")
            _ = model.api_client
        client.assert_called_once_with(vertexai=True, project="test-project", location="global")

    def test_story_context_renders_characters_and_plot_beats(self):
        context = build_story_context_prompt(
            elements=[],
            characters=[{"name": "Mara", "personality": "Bold", "motivation": "Explore", "quirk": "Hums"}],
            reused_nodes=[{"node_index": 0, "plot_beat": "The tide rises."}],
            recent_turns=[{"action": "I light the beacon.", "response": "The harbor answers with bells."}],
        )
        self.assertIn("- Mara: Personality: Bold", context)
        self.assertIn("Node 0: Plot beat: The tide rises.", context)
        self.assertIn("Player action: I light the beacon.", context)
        self.assertIn("Story response: The harbor answers with bells.", context)

    def test_character_profile_tool_does_not_change_story_state(self):
        tools = StoryPlanningTools(theater_id="character-profile")
        profile = tools.generate_character_profile(
            name="Mara",
            description="Cartographer",
            personality="Bold",
            motivation="Find the archive",
            quirk="Hums maps",
        )
        self.assertEqual(profile["name"], "Mara")
        self.assertEqual(profile["personality"], "Bold")
        self.assertEqual(tools.get_present_characters(), [])

    def test_dice_roll_returns_a_dnd_style_total(self):
        canvas_state_service = MagicMock()
        tools = StoryPlanningTools(theater_id="dice", canvas_state_service=canvas_state_service)

        result = tools.roll_dice(sides=20, count=2, modifier=3, reason="Leap across the chasm")

        self.assertEqual(result["notation"], "2d20+3")
        self.assertEqual(len(result["rolls"]), 2)
        self.assertTrue(all(1 <= roll <= 20 for roll in result["rolls"]))
        self.assertEqual(result["total"], sum(result["rolls"]) + 3)
        self.assertEqual(result["reason"], "Leap across the chasm")
        canvas_state_service.set_tool_activity.assert_called_once_with(
            "dice", active=True, theater_id="dice", recent_seconds=2.5,
        )

    def test_dice_roll_rejects_unsafe_ranges(self):
        tools = StoryPlanningTools(theater_id="dice")
        self.assertIn("sides", tools.roll_dice(sides=1)["error"])
        self.assertIn("count", tools.roll_dice(count=11)["error"])

    def test_planner_can_list_and_read_text_only_lore_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lore Theater",
                theater_id="lore-theater",
                lore_files=[("lore/characters.txt", b"Mara is the royal cartographer.")],
            )
            tools = StoryPlanningTools(theater_id="lore-theater", theater_manager=theater_manager)

            self.assertIn("characters.txt", tools.browse_lore())
            self.assertIn("royal cartographer", tools.browse_lore("characters.txt"))
            self.assertTrue(tools.browse_lore("characters.md").startswith("Error:"))

    def test_empty_script_automatically_injects_all_lore_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lore Theater",
                theater_id="lore-theater",
                lore_files=[
                    ("lore/characters.txt", b"Mara is the royal cartographer."),
                    ("lore/world.txt", b"The capital floats above the sea."),
                ],
            )
            tools = StoryPlanningTools(theater_id="lore-theater", theater_manager=theater_manager)

            context = tools._get_initial_lore_context()

            self.assertIn("Lore document: characters.txt", context)
            self.assertIn("royal cartographer", context)
            self.assertIn("Lore document: world.txt", context)
            self.assertIn("capital floats", context)

    def test_character_profile_uses_the_planner_vertex_model(self):
        with patch("tools.story_planning_tool.genai.Client") as client:
            client.return_value.models.generate_content.return_value.text = json.dumps({
                "personality": "Careful",
                "motivation": "Find the missing map",
            })
            tools = StoryPlanningTools(config={
                "planner_model": "gemini-3.7-flash",
                "vertex_project": "test-project",
                "vertex_location": "global",
            })
            profile = tools.generate_character_profile(name="Mara")

        client.assert_called_once_with(vertexai=True, project="test-project", location="global")
        self.assertEqual(
            client.return_value.models.generate_content.call_args.kwargs["model"],
            "gemini-3.7-flash",
        )
        self.assertEqual(profile["personality"], "Careful")

    def test_scene_reaction_supports_direct_structured_plot_beat_fallback(self):
        reaction = SceneReaction(
            narration="The storm doors open.",
            plot_beats=["Rain enters the hall.", "A courier arrives."],
            character_updates=[{"name": "Courier", "description": "Soaked messenger"}],
        )
        self.assertEqual(reaction.plot_beats, ["Rain enters the hall.", "A courier arrives."])
        self.assertEqual(reaction.character_updates[0].name, "Courier")

    def test_normalize_plot_beats_accepts_typed_scene_delta_strings(self):
        self.assertEqual(
            StoryPlanningTools._normalize_plot_beats(["A bell rings.", "The doors open."]),
            [{"plot_beat": "A bell rings."}, {"plot_beat": "The doors open."}],
        )

    def test_named_elements_are_bounded(self):
        tools = StoryPlanningTools(theater_id="elements")
        for index in range(DEFAULT_MAX_NAMED_ELEMENTS + 1):
            tools.update_or_insert_named_element(f"key_{index}", f"value_{index}")
        self.assertEqual(len(tools.get_present_elements()), DEFAULT_MAX_NAMED_ELEMENTS)
        self.assertEqual(tools.get_present_elements()[0]["name"], "key_1")

    def test_planner_action_is_nonblocking_and_commits_plot_beats(self):
        completed = threading.Event()
        results = []
        billed_plans = []

        def planner_executor(action, snapshot):
            self.assertEqual(action, "I light my torch.")
            self.assertEqual(snapshot["elements"], [])
            self.assertEqual(snapshot["plot_beats"], [])
            return {
                "narration": "The hidden doorway opens.",
                "scene_description": "Torchlight shivers across the mossy archway.",
                "dialogue": [{"speaker": "Mara", "text": "There!", "kind": "speech"}],
                "character_updates": [{
                    "name": "Lantern Warden", "description": "Armored keeper", "personality": "Stern",
                    "motivation": "Protect the archive", "quirk": "Polishes lanterns",
                }],
                "plot_beats": [
                    {"plot_beat": "Floodwater enters the archive."},
                    {"plot_beat": "The Warden reveals a sealed map."},
                ],
            }

        def on_scene_reaction(result):
            results.append(result)
            completed.set()

        state = MagicMock()
        tools = StoryPlanningTools(
            config={
                "adventure_mode": True,
                "nodes_ahead": 2,
                "planner_executor": planner_executor,
                "on_scene_reaction": on_scene_reaction,
                "on_story_plan_completed": lambda: billed_plans.append(1),
            },
            theater_id="planner",
            canvas_state_service=state,
        )

        acknowledgement = tools.process_user_action("I light my torch.")

        self.assertEqual(acknowledgement["status"], "processing")
        self.assertTrue(completed.wait(timeout=1))
        self.assertEqual(results[0]["narration"], "The hidden doorway opens.")
        self.assertEqual(results[0]["scene_description"], "Torchlight shivers across the mossy archway.")
        self.assertEqual(len(tools.get_plot_beats()), 2)
        self.assertEqual(billed_plans, [1])
        self.assertIn("plot_beats", tools.export_story_planning_state())
        self.assertEqual(tools.get_present_characters()[0]["name"], "Lantern Warden")
        state.get.return_value.set_scene_dialogue.assert_called_once_with(results[0]["dialogue"])
        state.get.return_value.set_scene_description.assert_called_once_with(results[0]["scene_description"])
        self.assertIn("process_user_action is on cooldown", tools.process_user_action("I open the doorway."))

    def test_clear_scene_removes_plot_beats_and_characters(self):
        tools = StoryPlanningTools(config={"adventure_mode": True}, theater_id="clear")
        tools._characters["Mara"] = {"name": "Mara", "description": "", "personality": "Bold", "motivation": "Explore", "quirk": "Hums"}
        tools._plot_beats = [{"plot_beat": "The tide rises."}]
        tools.clear_scene()
        self.assertEqual(tools.get_present_characters(), [])
        self.assertEqual(tools.get_plot_beats(), [])

    def test_import_uses_plot_beats_contract(self):
        tools = StoryPlanningTools(theater_id="import")
        tools.import_story_planning_state({
            "characters": [],
            "plot_beats": [{"plot_beat": "A bell rings."}],
        })
        self.assertEqual(tools.get_plot_beats(), [{"plot_beat": "A bell rings."}])

    def test_planner_receives_the_three_most_recent_resolved_turns(self):
        snapshots = []

        def planner_executor(_action, snapshot):
            snapshots.append(snapshot)
            return {
                "narration": "The world changes.",
                "plot_beats": ["A new consequence gathers."],
            }

        tools = StoryPlanningTools(config={
            "adventure_mode": True,
            "nodes_ahead": 1,
            "planner_executor": planner_executor,
        })

        for action in ("Action 1", "Action 2", "Action 3", "Action 4"):
            tools._resolve_user_action(action)

        self.assertEqual(
            [turn["action"] for turn in snapshots[-1]["recent_turns"]],
            ["Action 1", "Action 2", "Action 3"],
        )
        self.assertEqual(
            [turn["action"] for turn in tools.get_recent_story_turns()],
            ["Action 2", "Action 3", "Action 4"],
        )

    def test_recent_turns_survive_story_state_round_trip(self):
        tools = StoryPlanningTools(theater_id="history")
        for index in range(4):
            tools._append_recent_story_turn(
                f"Action {index}", {"narration": f"Response {index}", "dialogue": []},
            )

        restored = StoryPlanningTools(theater_id="history-restored")
        restored.import_story_planning_state(tools.export_story_planning_state())

        self.assertEqual(
            restored.get_recent_story_turns(),
            [
                {"action": "Action 1", "response": "Response 1"},
                {"action": "Action 2", "response": "Response 2"},
                {"action": "Action 3", "response": "Response 3"},
            ],
        )

    def test_scene_description_is_compact_and_action_cooldown_scales_with_response(self):
        tools = StoryPlanningTools(config={
            "action_cooldown_base_seconds": 10,
            "action_cooldown_words_per_second": 10,
            "action_cooldown_max_seconds": 20,
        })
        long_description = " ".join(f"word{index}" for index in range(60))

        self.assertEqual(len(tools._clean_scene_description(long_description).split()), 45)
        tools._last_action_response_word_count = 26

        self.assertEqual(tools.get_user_action_cooldown_seconds(), 13)


if __name__ == "__main__":
    unittest.main()
