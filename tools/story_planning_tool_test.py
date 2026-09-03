import asyncio
import inspect
import json
import threading
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from google.adk.models.google_llm import Gemini
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from tools.story_planning_tool import (
    DEFAULT_MAX_STICKY_NOTES,
    DEFAULT_STORY_PLANNING_STYLE,
    DEFAULT_THINKING_BUDGET,
    MAX_LORE_DOCUMENTS_LISTED,
    MAX_NUDGE_CHARS,
    MAX_PLAYER_ACTION_CHARS,
    MAX_STICKY_NOTE_TOPIC_CHARS,
    MAX_STICKY_NOTE_INFO_CHARS,
    MAX_STORY_PLANNING_STYLE_CHARS,
    SceneReaction,
    StoryLogDieRoll,
    StoryLogEntry,
    StoryPlanOutput,
    StoryPlanningTools,
    VertexGemini,
    build_scene_reaction_prompt,
    build_story_context_prompt,
)
from components.theater_manager import TheaterManager
from providers import TextResponseProvider, TextResponseRequest, TextResponseResult


class TestStoryPlanningTools(unittest.TestCase):
    def _make_tools(
        self,
        config=None,
        theater_id="test-theater",
        canvas_state_service=None,
        theater_manager=None,
        text_response_provider=None,
    ):
        return StoryPlanningTools(
            config=config or {},
            theater_id=theater_id,
            canvas_state_service=canvas_state_service or MagicMock(),
            theater_manager=theater_manager or MagicMock(),
            text_response_provider=text_response_provider or MagicMock(),
        )

    def test_required_arguments_are_enforced(self):
        mock_canvas = MagicMock()
        mock_theater_mgr = MagicMock()
        mock_provider = MagicMock()

        with self.assertRaises(TypeError):
            StoryPlanningTools()

        with self.assertRaises(ValueError):
            StoryPlanningTools(config={}, theater_id="", canvas_state_service=mock_canvas, theater_manager=mock_theater_mgr, text_response_provider=mock_provider)

        with self.assertRaises(ValueError):
            StoryPlanningTools(config={}, theater_id=None, canvas_state_service=mock_canvas, theater_manager=mock_theater_mgr, text_response_provider=mock_provider)

        with self.assertRaises(ValueError):
            StoryPlanningTools(config={}, theater_id="t", canvas_state_service=None, theater_manager=mock_theater_mgr, text_response_provider=mock_provider)

        with self.assertRaises(ValueError):
            StoryPlanningTools(config={}, theater_id="t", canvas_state_service=mock_canvas, theater_manager=None, text_response_provider=mock_provider)

        with self.assertRaises(ValueError):
            StoryPlanningTools(config={}, theater_id="t", canvas_state_service=mock_canvas, theater_manager=mock_theater_mgr, text_response_provider=None)

    def test_story_log_entries_are_written_to_theater_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            theater_manager = TheaterManager(base_theaters_dir=temp_dir)
            tools = self._make_tools(theater_id="story-log", theater_manager=theater_manager)

            tools._append_story_log_entry(StoryLogEntry(type="user_action", action="I open the door."))

            log_path = theater_manager.theater("story-log").output_dir() / "story_log.jsonl"
            entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(entries[0]["type"], "user_action")
            self.assertEqual(entries[0]["action"], "I open the door.")
            self.assertNotIn("nudge", entries[0])

    def test_planner_initialization_reads_the_last_200_story_log_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            theater_manager = TheaterManager(base_theaters_dir=temp_dir)
            log_path = theater_manager.theater("story-history").output_dir() / "story_log.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                "".join(
                    json.dumps({"type": "user_action", "action": f"Action {index}"}) + "\n"
                    for index in range(205)
                ),
                encoding="utf-8",
            )

            tools = self._make_tools(theater_id="story-history", theater_manager=theater_manager)

            self.assertEqual(len(tools._recent_story_log), 200)
            self.assertEqual(tools._recent_story_log[0]["action"], "Action 5")
            self.assertEqual(tools._recent_story_log[0].action, "Action 5")
            instruction = tools._build_planner_instruction()
            self.assertIn("Player action: Action 5", instruction)
            self.assertNotIn("Player action: Action 4\n", instruction)

    def test_story_log_pydantic_schema_validation_and_serialization(self):
        # Test StoryLogEntry model validation for user_action
        user_action_entry = StoryLogEntry(type="user_action", action="I cast a spell")
        self.assertEqual(user_action_entry.type, "user_action")
        self.assertEqual(user_action_entry.action, "I cast a spell")
        self.assertEqual(user_action_entry["action"], "I cast a spell")
        dumped = user_action_entry.model_dump(mode="json", exclude_none=True)
        self.assertEqual(dumped["type"], "user_action")
        self.assertEqual(dumped["action"], "I cast a spell")
        self.assertNotIn("output", dumped)

        # Test StoryLogEntry with StoryPlanOutput and StoryLogDieRoll
        plan_entry = StoryLogEntry.model_validate({
            "type": "story_plan",
            "output": {
                "narration": "A beam of light shoots forward.",
                "dialogue": [{"speaker": "Mage", "text": "Behold!"}],
                "die_rolls": [{"notation": "1d20+5", "total": 19, "tier": "high", "reason": "Spell hit"}],
            },
        })
        self.assertEqual(plan_entry.output.narration, "A beam of light shoots forward.")
        self.assertEqual(plan_entry.output["narration"], "A beam of light shoots forward.")
        self.assertEqual(len(plan_entry.output.die_rolls), 1)
        self.assertEqual(plan_entry.output.die_rolls[0].total, 19)
        self.assertEqual(plan_entry.output.die_rolls[0]["reason"], "Spell hit")

    def test_story_log_appends_die_roll_results_with_story_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            theater_manager = TheaterManager(base_theaters_dir=temp_dir)
            tools = self._make_tools(
                config={"adventure_mode": True, "nodes_ahead": 1},
                theater_id="dice-log-test",
                theater_manager=theater_manager,
            )

            # Roll dice during turn
            roll_res = tools.roll_dice(sides=20, count=1, modifier=2, reason="Unlock the chest")
            self.assertEqual(len(tools.get_die_rolls_this_turn()), 1)
            self.assertEqual(tools.get_die_rolls_this_turn()[0]["reason"], "Unlock the chest")

            with patch.object(
                tools,
                "_run_planner_agent",
                return_value={
                    "narration": "The lock clicks open.",
                    "plot_beats": ["Inside is an ancient scroll."],
                },
            ):
                result = tools._resolve_user_action("I pick the lock.")
                self.assertIn("die_rolls", result)
                self.assertEqual(len(result["die_rolls"]), 1)
                self.assertEqual(result["die_rolls"][0]["reason"], "Unlock the chest")

                # Test async process_user_action appends story_plan with die rolls
                tools._append_story_log_entry(
                    StoryLogEntry(
                        type="story_plan",
                        output=StoryPlanOutput(
                            narration=result.get("narration") or "",
                            dialogue=result.get("dialogue") or [],
                            die_rolls=result.get("die_rolls") or [],
                        ),
                    )
                )

            log_path = theater_manager.theater("dice-log-test").output_dir() / "story_log.jsonl"
            entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["type"], "story_plan")
            self.assertIn("die_rolls", entries[0]["output"])
            self.assertEqual(entries[0]["output"]["die_rolls"][0]["reason"], "Unlock the chest")

    def test_story_log_formats_die_rolls_for_planner_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            theater_manager = TheaterManager(base_theaters_dir=temp_dir)
            tools = self._make_tools(theater_id="format-dice-test", theater_manager=theater_manager)
            tools._recent_story_log = [
                StoryLogEntry.model_validate({"type": "user_action", "action": "I jump over the pit"}),
                StoryLogEntry.model_validate({
                    "type": "story_plan",
                    "output": {
                        "narration": "You land safely.",
                        "die_rolls": [
                            {"notation": "1d20+3", "total": 18, "tier": "high", "reason": "Jump distance"}
                        ],
                    },
                }),
                StoryLogEntry.model_validate({
                    "type": "die_roll",
                    "result": {"notation": "2d6", "total": 8, "tier": "middle", "reason": "Damage check"},
                }),
            ]
            formatted = tools._format_recent_story_log()
            self.assertIn("Player action: I jump over the pit", formatted)
            self.assertIn("Die roll result for 'Jump distance': 1d20+3 -> 18 [high]", formatted)
            self.assertIn("Narration: You land safely.", formatted)
            self.assertIn("Die roll result for 'Damage check': 2d6 -> 8 [middle]", formatted)

    def test_die_rolls_are_tracked_and_reset_per_turn(self):
        tools = self._make_tools(theater_id="reset-dice-test")
        self.assertEqual(tools.get_die_rolls_this_turn(), [])

        tools.roll_dice(sides=6, count=2, modifier=0, reason="Stealth check")
        self.assertEqual(len(tools.get_die_rolls_this_turn()), 1)

        tools.reset_die_roll_counts()
        self.assertEqual(tools.get_die_rolls_this_turn(), [])

        tools.roll_dice(sides=20, count=1, modifier=0, reason="Perception")
        self.assertEqual(len(tools.get_die_rolls_this_turn()), 1)

        tools.reset_lore_call_counts()
        self.assertEqual(tools.get_die_rolls_this_turn(), [])

    def test_planner_instruction_reserves_player_agency(self):
        prompt = build_scene_reaction_prompt(
            context="Current scene elements: None",
            style="balanced",
            nodes_ahead=5,
            lore_context="- world.txt\n- factions/ (directory)",
        )
        self.assertIn("Never invent the player's actions, speech, thoughts, feelings, decisions", prompt)
        self.assertIn("Dialogue may be spoken only by NPCs", prompt)
        self.assertIn("'yes, and' improv posture", prompt)
        self.assertIn("Story-Planning Style (User Specified)", prompt)
        self.assertIn("balanced", prompt)
        self.assertIn("Include exactly 5 general plot beats", prompt)
        self.assertIn("Available theater lore (top-level documents and directories):", prompt)
        self.assertIn("- world.txt", prompt)
        self.assertIn("- factions/ (directory)", prompt)
        self.assertIn("narration should normally be 20-50 words that also describe the visual resolution and immediate outcome of the character's action rather than just scenery alone", prompt)
        self.assertIn("Scene Labeling", prompt)
        self.assertIn("Ensure the scene has a label.", prompt)
        self.assertIn("Reference Usage", prompt)
        self.assertIn("communicate them via the reference_images field", prompt)

    def test_scene_reaction_schema_describes_action_resolution(self):
        field = SceneReaction.model_fields["narration"]
        self.assertIn("resolution", field.description.lower())
        self.assertIn("action", field.description.lower())

    def test_scene_reaction_schema_includes_scene_label_and_reference_images(self):
        label_field = SceneReaction.model_fields["scene_label"]
        self.assertEqual(label_field.default, "")
        self.assertIn("label for this scene", label_field.description.lower())

        ref_field = SceneReaction.model_fields["reference_images"]
        self.assertIn("reference image", ref_field.description.lower())

        reaction = SceneReaction(
            narration="The gatekeeper steps aside.",
            scene_label="Courtyard",
            reference_images=["gate.png", "guard_portrait.png"],
        )
        self.assertEqual(reaction.scene_label, "Courtyard")
        self.assertEqual(reaction.reference_images, ["gate.png", "guard_portrait.png"])

    def test_planner_model_uses_explicit_vertex_client(self):
        with patch("tools.story_planning_tool.genai.Client") as client:
            model = VertexGemini(model="gemini-2.5-flash", project_id="test-project", location="global")
            _ = model.api_client
        client.assert_called_once_with(vertexai=True, project="test-project", location="global")

    def test_planner_model_creates_separate_clients_per_event_loop(self):
        with patch("tools.story_planning_tool.genai.Client", side_effect=[MagicMock(), MagicMock(), MagicMock()]) as mock_client_factory:
            model = VertexGemini(model="gemini-2.5-flash", project_id="test-project", location="global")
            
            # Outside loop
            client_none = model.api_client
            client_none_again = model.api_client
            self.assertIs(client_none, client_none_again)

            # In loop 1
            loop1 = asyncio.new_event_loop()
            async def get_client_in_loop():
                return model.api_client
            
            client_loop1 = loop1.run_until_complete(get_client_in_loop())
            loop1.close()

            # In loop 2
            loop2 = asyncio.new_event_loop()
            client_loop2 = loop2.run_until_complete(get_client_in_loop())
            loop2.close()

            self.assertIsNot(client_none, client_loop1)
            self.assertIsNot(client_loop1, client_loop2)
            self.assertEqual(mock_client_factory.call_count, 3)

    def test_story_context_renders_characters_and_plot_beats(self):
        context = build_story_context_prompt(
            elements=[],
            characters=[{"name": "Mara", "personality": "Bold", "motivation": "Explore", "quirk": "Hums"}],
            reused_nodes=[{"node_index": 0, "plot_beat": "The tide rises."}],
        )
        self.assertIn("- Mara: Personality: Bold", context)
        self.assertIn("Node 0: Plot beat: The tide rises.", context)

    def test_story_planning_style_defaults_and_is_supplied_to_the_planner(self):
        default_tools = self._make_tools()
        self.assertEqual(default_tools.style, DEFAULT_STORY_PLANNING_STYLE)

        tools = self._make_tools(config={
            "adventure_mode": True,
            "nodes_ahead": 1,
            "style": "harsh and unforgiving, but never arbitrary",
        })
        with patch.object(tools, "_run_planner_agent", return_value={"narration": "The bridge groans.", "plot_beats": ["The storm worsens."]}) as mock_run:
            tools._resolve_user_action("I cross the bridge.")
            mock_run.assert_called_once_with("I cross the bridge.", nudge="")

        self.assertEqual(tools.style, "harsh and unforgiving, but never arbitrary")
        prompt = tools._build_planner_instruction()
        self.assertIn("Story-Planning Style (User Specified)", prompt)
        self.assertIn("harsh and unforgiving, but never arbitrary", prompt)

    def test_story_planning_style_is_bounded_when_loaded_from_advanced_config(self):
        tools = self._make_tools(config={"style": "x" * (MAX_STORY_PLANNING_STYLE_CHARS + 1)})

        self.assertEqual(len(tools.style), MAX_STORY_PLANNING_STYLE_CHARS)

    def test_thinking_budget_defaults_and_is_configurable(self):
        default_tools = self._make_tools()
        self.assertEqual(default_tools.thinking_budget, DEFAULT_THINKING_BUDGET)
        self.assertIsNotNone(default_tools._planner_agent.generate_content_config)
        self.assertEqual(
            default_tools._planner_agent.generate_content_config.thinking_config.thinking_budget,
            1024,
        )

        custom_tools = self._make_tools(config={"thinking_budget": 512})
        self.assertEqual(custom_tools.thinking_budget, 512)
        self.assertEqual(
            custom_tools._planner_agent.generate_content_config.thinking_config.thinking_budget,
            512,
        )

        disabled_tools = self._make_tools(config={"thinking_budget": None})
        self.assertIsNone(disabled_tools.thinking_budget)
        self.assertIsNone(disabled_tools._planner_agent.generate_content_config.thinking_config)

    def test_character_profile_tool_does_not_change_story_state(self):
        tools = self._make_tools(theater_id="character-profile")
        profile = tools.generate_character_profile(
            name="Mara",
            description="Cartographer",
            personality="Bold",
            motivation="Find the archive",
            quirk="Hums sea shanties",
        )
        self.assertEqual(profile["name"], "Mara")
        self.assertEqual(profile["quirk"], "Hums sea shanties")
        self.assertEqual(tools.get_present_characters(), [])

    def test_character_profile_generation_assigns_random_quirk_when_unspecified(self):
        tools = self._make_tools(theater_id="quirk-theater")
        profile = tools.generate_character_profile(
            name="Orin",
            description="Hermit",
            personality="Quiet",
            motivation="Guard the forest",
        )
        self.assertTrue(len(profile["quirk"]) > 0)
        self.assertEqual(profile["name"], "Orin")

    def test_dice_roll_returns_a_dnd_style_total(self):
        canvas_state_service = MagicMock()
        tools = self._make_tools(theater_id="dice", canvas_state_service=canvas_state_service)

        result = tools.roll_dice(sides=20, count=2, modifier=3, reason="Leap across the chasm")

        self.assertEqual(result["notation"], "2d20+3")
        self.assertEqual(len(result["rolls"]), 2)
        self.assertTrue(all(1 <= roll <= 20 for roll in result["rolls"]))
        self.assertEqual(result["total"], sum(result["rolls"]) + 3)
        self.assertIn(result["tier"], {"low", "middle", "high"})
        self.assertEqual(result["reason"], "Leap across the chasm")
        canvas_state_service.set_tool_activity.assert_called_once_with(
            "dice", active=True, theater_id="dice", recent_seconds=2.5, result=result,
        )

    def test_dice_roll_rejects_unsafe_ranges(self):
        tools = self._make_tools(theater_id="dice")
        self.assertIn("sides", tools.roll_dice(sides=1)["error"])
        self.assertIn("count", tools.roll_dice(count=11)["error"])

    def test_read_lore_lists_and_reads_valid_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lore Theater",
                theater_id="lore-theater",
                lore_files=[
                    ("lore/characters.txt", b"Mara is the royal cartographer."),
                    ("lore/factions/guild.txt", b"The Iron Guild controls the gates."),
                ],
            )
            tools = self._make_tools(theater_id="lore-theater", theater_manager=theater_manager)

            self.assertIn("characters.txt", tools.read_lore())
            tools.reset_lore_call_counts()
            self.assertIn("factions/guild.txt", tools.read_lore())
            tools.reset_lore_call_counts()
            self.assertIn("royal cartographer", tools.read_lore("characters.txt"))
            tools.reset_lore_call_counts()
            self.assertIn("The Iron Guild", tools.read_lore("factions/guild.txt"))
            tools.reset_lore_call_counts()
            self.assertIn("factions/guild.txt", tools.read_lore("factions"))
            tools.reset_lore_call_counts()
            self.assertTrue(tools.read_lore("characters.md").startswith("Error:"))

    def test_read_lore_enforces_hard_limit_and_pushes_to_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lore Theater",
                theater_id="lore-theater",
                lore_files=[
                    ("lore/characters.txt", b"Mara is the royal cartographer."),
                    ("lore/world.txt", b"The world floats in the clouds."),
                    ("lore/factions/guild.txt", b"The Iron Guild controls the gates."),
                ],
            )
            tools = self._make_tools(theater_id="lore-theater", theater_manager=theater_manager)

            # Call 1: Lists documents, limit note NOT present
            res1 = tools.read_lore()
            self.assertIn("characters.txt", res1)
            self.assertNotIn("maximum limit of 3", res1)

            # Call 2: Reads a document, limit note NOT present
            res2 = tools.read_lore("characters.txt")
            self.assertIn("Mara is the royal cartographer", res2)
            self.assertNotIn("maximum limit of 3", res2)

            # Call 3: Reads another document, limit note IS appended pushing to answer
            res3 = tools.read_lore("world.txt")
            self.assertIn("The world floats in the clouds", res3)
            self.assertIn("maximum limit of 3 read_lore calls", res3)
            self.assertIn("Proceed immediately to finalize and return the scene reaction JSON", res3)

            # Call 4: Blocked by hard limit
            res4 = tools.read_lore("factions/guild.txt")
            self.assertTrue(res4.startswith("Error: Maximum read_lore call limit (3) reached"))
            self.assertIn("Finalize and return the scene reaction now", res4)
            self.assertNotIn("The Iron Guild", res4)

            # Resetting for a new turn allows reading again
            tools.reset_lore_call_counts()
            res5 = tools.read_lore("factions/guild.txt")
            self.assertIn("The Iron Guild", res5)
            self.assertNotIn("Maximum read_lore call limit", res5)

    def test_search_lore_keyword_scoring_and_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Search Lore Theater",
                theater_id="search-lore-theater",
                lore_files=[
                    ("lore/01_pricing.txt", b"Tradeable objects and pricing in Silk Road towns. Silk costs 50 gold."),
                    ("lore/02_mechanics.txt", b"Haggling mechanics and trade routes across Asia."),
                    ("lore/03_hazards.txt", b"Hazards, bandits, and cargo vulnerability on desert tracks."),
                ],
            )
            tools = self._make_tools(theater_id="search-lore-theater", theater_manager=theater_manager)

            # Keyword search for pricing
            res = tools.search_lore("pricing silk gold")
            self.assertIn("01_pricing.txt", res)
            self.assertIn("score:", res)
            self.assertIn("Snippet:", res)

            # Check ranking: 01_pricing.txt should rank before 02_mechanics.txt for "pricing silk"
            lines = res.split("\n")
            pricing_idx = next(i for i, line in enumerate(lines) if "01_pricing.txt" in line)
            self.assertEqual(pricing_idx, 1)

            # Search for hazards
            tools.reset_lore_call_counts()
            res_hazards = tools.search_lore("hazards bandits")
            self.assertIn("03_hazards.txt", res_hazards)

            # Test caching
            tools.reset_lore_call_counts()
            first_search = tools.search_lore("pricing silk gold")
            second_search = tools.search_lore("pricing silk gold")
            self.assertEqual(first_search, second_search)

    def test_read_lore_and_search_lore_capped_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lore Theater",
                theater_id="lore-theater",
                lore_files=[
                    ("lore/characters.txt", b"Mara is the royal cartographer."),
                    ("lore/world.txt", b"The world floats in the clouds."),
                    ("lore/factions/guild.txt", b"The Iron Guild controls the gates."),
                ],
            )
            tools = self._make_tools(theater_id="lore-theater", theater_manager=theater_manager)

            # Exhaust read_lore limit (3 calls)
            tools.read_lore("characters.txt")
            tools.read_lore("world.txt")
            tools.read_lore("factions/guild.txt")
            blocked_read = tools.read_lore("characters.txt")
            self.assertTrue(blocked_read.startswith("Error: Maximum read_lore call limit (3) reached"))

            # search_lore should still be available (capped separately)
            search_res = tools.search_lore("cartographer")
            self.assertIn("characters.txt", search_res)
            self.assertNotIn("Maximum search_lore call limit", search_res)

    def test_scene_reaction_prompt_contains_read_and_search_lore_limits(self):
        prompt = build_scene_reaction_prompt(
            context="Scene context here",
            style="action-packed",
            nodes_ahead=3,
            lore_context="- characters.txt",
        )
        self.assertIn("search_lore", prompt)
        self.assertIn("read_lore", prompt)
        self.assertIn("proceed immediately to return the scene reaction", prompt)

    def test_scene_reaction_prompt_handles_no_lore_context(self):
        prompt = build_scene_reaction_prompt(
            context="Scene context here",
            style="action-packed",
            nodes_ahead=3,
            lore_context="",
        )
        self.assertIn("No lore documents are available for this theater", prompt)
        self.assertIn("Invent the lore", prompt)
        self.assertNotIn("Available theater lore (top-level documents and directories):", prompt)

    def test_read_lore_logs_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lore Theater",
                theater_id="lore-theater",
                lore_files=[
                    ("lore/characters.txt", b"Mara is the royal cartographer."),
                ],
            )
            tools = self._make_tools(theater_id="lore-theater", theater_manager=theater_manager)

            with self.assertLogs("tools.story_planning_tool", level="DEBUG") as cm:
                tools.read_lore()
                tools.read_lore("characters.txt")
                tools.read_lore("invalid.md")

            logs = "\n".join(cm.output)
            self.assertIn("Listing lore documents for theater=lore-theater", logs)
            self.assertIn("Read lore document 'characters.txt' for theater=lore-theater", logs)
            self.assertIn("Failed to read lore document 'invalid.md' for theater=lore-theater", logs)

    def test_lore_context_lists_top_level_documents_and_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lore Theater",
                theater_id="lore-theater",
                lore_files=[
                    ("lore/characters.txt", b"Mara is the royal cartographer."),
                    ("lore/world.txt", b"The capital floats above the sea."),
                    ("lore/factions/guild.txt", b"The guild controls trade."),
                ],
            )
            tools = self._make_tools(theater_id="lore-theater", theater_manager=theater_manager)

            context = tools._get_lore_context()

            self.assertIn("- characters.txt", context)
            self.assertIn("- world.txt", context)
            self.assertIn("- factions/ (directory)", context)
            self.assertNotIn("guild.txt", context)
            self.assertNotIn("royal cartographer", context)

    def test_lore_context_expands_files_prefixed_with_read(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Read Lore Theater",
                theater_id="read-lore-theater",
                lore_files=[
                    ("lore/characters.txt", b"Mara is the royal cartographer."),
                    ("lore/read_world.txt", b"The capital floats above the sea."),
                    ("lore/factions/read_guild.txt", b"The guild controls all trade routes."),
                ],
            )
            tools = self._make_tools(theater_id="read-lore-theater", theater_manager=theater_manager)

            context = tools._get_lore_context()

            self.assertIn("- characters.txt", context)
            self.assertNotIn("royal cartographer", context)
            self.assertIn("- read_world.txt:\nThe capital floats above the sea.", context)
            self.assertIn("- factions/read_guild.txt:\nThe guild controls all trade routes.", context)
            self.assertIn("- factions/ (directory)", context)

    def test_lore_context_fetches_readfirst_file(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Lesovik Station",
                theater_id="lesovik-station",
                lore_files=[
                    ("lore/readfirst.txt", b"This is a story about a researcher arriving at Lesovik Station."),
                    ("lore/researchers/casper.txt", b"Casper Ramahura info."),
                ],
            )
            tools = self._make_tools(theater_id="lesovik-station", theater_manager=theater_manager)
            context = tools._get_lore_context()
            self.assertIn("- readfirst.txt:\nThis is a story about a researcher arriving at Lesovik Station.", context)
            activity = tools.get_lore_activity_this_turn()
            self.assertTrue(any(a.get("type") == "preloaded" and a.get("document") == "readfirst.txt" for a in activity))

    def test_lore_context_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            theater_manager = TheaterManager(base_theaters_dir=directory)
            theater_manager.create_theater(
                name="Large Lore Theater",
                theater_id="large-lore",
                lore_files=[(f"lore/doc_{i}.txt", b"content") for i in range(MAX_LORE_DOCUMENTS_LISTED + 5)],
            )
            tools = self._make_tools(theater_id="large-lore", theater_manager=theater_manager)

            context = tools._get_lore_context()

            self.assertIn("- doc_0.txt", context)
            self.assertNotIn("5 additional lore documents omitted", context)

    def test_overlong_player_actions_are_rejected_before_a_planner_turn(self):
        tools = self._make_tools(config={
            "adventure_mode": True,
        })

        with patch.object(tools, "_resolve_user_action") as mock_resolve:
            result = tools.process_user_action("x" * (MAX_PLAYER_ACTION_CHARS + 1))
            self.assertIn("characters or fewer", result["error"])
            mock_resolve.assert_not_called()

    def test_overlong_nudge_is_rejected_before_a_planner_turn(self):
        tools = self._make_tools(config={
            "adventure_mode": True,
        })

        with patch.object(tools, "_resolve_user_action") as mock_resolve:
            result = tools.process_user_action("I search the room", nudge="x" * (MAX_NUDGE_CHARS + 1))
            self.assertIn("Nudge must be", result["error"])
            mock_resolve.assert_not_called()

    def test_process_user_action_forwards_nudge_to_planner(self):
        tools = self._make_tools(config={
            "adventure_mode": True,
            "nodes_ahead": 1,
            "action_cooldown_base_seconds": 0.0,
        })

        with patch.object(tools, "_resolve_user_action") as mock_resolve:
            result = tools.process_user_action("I examine the mural", nudge="Reveal a hidden compartment")
            self.assertEqual(result["status"], "processing")
            time.sleep(0.05)
            mock_resolve.assert_called_once_with("I examine the mural", nudge="Reveal a hidden compartment")

    def test_system_action_log_label_does_not_change_the_planner_prompt(self):
        tools = self._make_tools(config={"adventure_mode": True, "nodes_ahead": 1})
        tools._append_story_log_entry = MagicMock()

        self.assertNotIn(
            "message_type", inspect.signature(tools.process_user_action).parameters
        )

        with patch.object(tools, "_resolve_user_action", return_value={"error": "ignored"}):
            result = tools.process_system_action(
                "Begin or resume the adventure.", "Starting/Resuming Adventure"
            )

        self.assertEqual(result["status"], "processing")
        log_entry = tools._append_story_log_entry.call_args_list[0].args[0]
        self.assertEqual(log_entry.action, "<Starting/Resuming Adventure>")

    def test_run_planner_agent_includes_nudge_in_user_message(self):
        tools = self._make_tools(config={"nodes_ahead": 1, "adventure_mode": True})

        passed_messages = []

        async def fake_run_async(user_id, session_id, new_message, run_config=None):
            passed_messages.append(new_message.parts[0].text)
            reaction = SceneReaction(
                narration="You find a key.",
                plot_beats=["Beat 1"],
            )
            event = MagicMock()
            event.is_final_response.return_value = True
            event.content = types.Content(role="model", parts=[types.Part(text=reaction.model_dump_json())])
            yield event

        with patch.object(tools._planner_runner, "run_async", side_effect=fake_run_async):
            # Without nudge
            tools._run_planner_agent("I search the wall")
            self.assertEqual(passed_messages[-1], "I search the wall")

            # With nudge
            tools._run_planner_agent("I search the wall", nudge="A shadow looms behind them")
            self.assertIn("I search the wall", passed_messages[-1])
            self.assertIn("[Live Agent Nudge to Accommodate]: A shadow looms behind them", passed_messages[-1])

    def test_character_profile_uses_explicit_text_response_provider(self):
        mock_provider = MagicMock(spec=TextResponseProvider)
        mock_provider.generate.return_value = TextResponseResult(
            text=json.dumps({
                "personality": "Bold explorer",
                "motivation": "Discover uncharted territories",
                "voice_tags": ["female"],
            }),
            provider="mock-provider",
            model="mock-model",
        )
        tools = self._make_tools(text_response_provider=mock_provider)
        profile = tools.generate_character_profile(name="Kael")

        self.assertEqual(profile["personality"], "Bold explorer")
        self.assertEqual(profile["motivation"], "Discover uncharted territories")
        self.assertEqual(profile["voice_tags"], ["female"])
        mock_provider.generate.assert_called_once()
        request = mock_provider.generate.call_args[0][0]
        self.assertIsInstance(request, TextResponseRequest)
        self.assertIn("Character name: Kael", request.prompt)
        self.assertIn("voice_tags", request.prompt)
        self.assertIn("character design assistant", request.system_instruction)

    def test_character_creation_and_planner_prompt_includes_voice_tags(self):
        tools = self._make_tools(theater_id="voice-tags-test")
        result = tools.generate_character(
            name="Vane",
            description="Captain",
            personality="Ruthless",
            motivation="Claim the seas",
            quirk="Taps cutlass",
            voice_tags=["male", "unsupported_tag"],
        )
        self.assertIn("Voice Tags: male", result)
        char = tools.get_present_characters()[0]
        self.assertEqual(char["voice_tags"], ["male"])

        prompt = tools._build_planner_instruction()
        self.assertIn("voice_tags", prompt)
        self.assertIn("Voice Tags: male", prompt)

    def test_character_profile_surfaces_provider_errors(self):
        mock_provider = MagicMock(spec=TextResponseProvider)
        mock_provider.generate.side_effect = RuntimeError("Provider connection failed")
        tools = self._make_tools(text_response_provider=mock_provider)

        with self.assertRaises(RuntimeError) as ctx:
            tools.generate_character_profile(name="Kael")

        self.assertIn("Provider connection failed", str(ctx.exception))

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
        tools = self._make_tools(theater_id="elements")
        for index in range(DEFAULT_MAX_STICKY_NOTES + 1):
            tools.update_or_insert_named_element(f"key_{index}", f"value_{index}")
        self.assertEqual(len(tools.get_present_elements()), DEFAULT_MAX_STICKY_NOTES)
        self.assertEqual(tools.get_present_elements()[0]["name"], "key_1")

    def test_update_sticky_note_insert_update_and_bounding(self):
        tools = self._make_tools(theater_id="sticky_theater")
        # Insert
        res = tools.update_sticky_note("setting", "Dark forest at midnight")
        self.assertIn("Added sticky note 'setting'", res)
        self.assertEqual(len(tools.get_present_sticky_notes()), 1)
        self.assertEqual(tools.get_present_sticky_notes()[0]["topic"], "setting")
        self.assertEqual(tools.get_present_sticky_notes()[0]["info"], "Dark forest at midnight")

        # Update
        res_upd = tools.update_sticky_note("setting", "Sunny glade at noon")
        self.assertIn("Updated sticky note 'setting'", res_upd)
        self.assertEqual(len(tools.get_present_sticky_notes()), 1)
        self.assertEqual(tools.get_present_sticky_notes()[0]["info"], "Sunny glade at noon")

        # Bounding
        last_res = ""
        for i in range(DEFAULT_MAX_STICKY_NOTES):
            last_res = tools.update_sticky_note(f"topic_{i}", f"info_{i}")
        self.assertEqual(len(tools.get_present_sticky_notes()), DEFAULT_MAX_STICKY_NOTES)
        # 'setting' should have been evicted and warning emitted
        self.assertIn("Warning: Maximum limit of", last_res)
        self.assertIn("Oldest sticky note 'setting' was dropped", last_res)
        topics = [n["topic"] for n in tools.get_present_sticky_notes()]
        self.assertNotIn("setting", topics)
        self.assertIn("topic_0", topics)

    def test_update_sticky_note_size_limits_and_validation(self):
        tools = self._make_tools(theater_id="sticky_limits")
        # Empty topic
        res = tools.update_sticky_note("", "valid info")
        self.assertIn("Error: Sticky note topic cannot be empty", res)

        # Empty info
        res = tools.update_sticky_note("valid_topic", "")
        self.assertIn("Error: Sticky note info cannot be empty", res)

        # Topic too long
        long_topic = "t" * (MAX_STICKY_NOTE_TOPIC_CHARS + 1)
        res = tools.update_sticky_note(long_topic, "valid info")
        self.assertIn("Error: Sticky note topic must be", res)

        # Info too long
        long_info = "i" * (MAX_STICKY_NOTE_INFO_CHARS + 1)
        res = tools.update_sticky_note("valid_topic", long_info)
        self.assertIn("Error: Sticky note info must be", res)

        # Divider '|' count mismatch validation
        tools.update_sticky_note("HUD", "HP: 100 | ATK: 20 | DEF: 10")
        mismatch_res = tools.update_sticky_note("HUD", "HP: 100 | ATK: 25")
        self.assertIn("Error: Sticky note divider count mismatch for 'HUD'", mismatch_res)
        self.assertIn("Expected valid update for 'HP: 100 | ATK: 20 | DEF: 10'", mismatch_res)
        self.assertEqual(
            next(n["info"] for n in tools.get_present_sticky_notes() if n["topic"] == "HUD"),
            "HP: 100 | ATK: 20 | DEF: 10",
        )

        matching_res = tools.update_sticky_note("HUD", "HP: 90 | ATK: 25 | DEF: 10")
        self.assertIn("Updated sticky note 'HUD'", matching_res)
        self.assertEqual(
            next(n["info"] for n in tools.get_present_sticky_notes() if n["topic"] == "HUD"),
            "HP: 90 | ATK: 25 | DEF: 10",
        )

    def test_story_planning_agent_has_update_sticky_note_tool(self):
        tools = self._make_tools(theater_id="planner_tools_check")
        agent = tools._planner_agent
        tool_callables = [t for t in agent.tools]
        self.assertIn(tools.update_sticky_note, tool_callables)

    def test_sticky_notes_automatically_added_to_story_context(self):
        tools = self._make_tools(theater_id="context_check")
        tools.update_sticky_note("quest_item", "Golden Amulet of Ra")
        tools.update_sticky_note("weather", "Thunderstorm")

        instruction = tools._build_planner_instruction()
        self.assertIn("Your sticky notes:", instruction)
        self.assertIn("- quest_item: Golden Amulet of Ra", instruction)
        self.assertIn("- weather: Thunderstorm", instruction)

    def test_live_agent_tools_gated_by_adventure_mode(self):
        # Non-adventure mode: live agent can manage sticky notes only.
        tools_normal = self._make_tools(config={"adventure_mode": False})
        normal_exposed = tools_normal.get_tools()
        self.assertIn(tools_normal.update_sticky_note, normal_exposed)
        self.assertNotIn(tools_normal.clear_scene, normal_exposed)
        self.assertNotIn(tools_normal.process_user_action, normal_exposed)

        # Adventure mode: live agent only gets process_user_action
        tools_adv = self._make_tools(config={"adventure_mode": True})
        adv_exposed = tools_adv.get_tools()
        self.assertIn(tools_adv.process_user_action, adv_exposed)
        self.assertNotIn(tools_adv.update_sticky_note, adv_exposed)
        self.assertNotIn(tools_adv.clear_scene, adv_exposed)

    def test_required_stickies_loaded_from_list_of_keys(self):
        config = {
            "adventure_mode": True,
            "required_stickies": [
                "HUD",
                "Radar",
                "Reputation",
            ],
            "initial_elements": {
                "HUD": "Funk synthesizer display",
                "Radar": "Turntable scanner",
            },
        }
        tools = self._make_tools(config=config, theater_id="req_test_1")
        self.assertEqual(tools.get_required_sticky_notes(), ["HUD", "Radar", "Reputation"])
        present = tools.get_present_sticky_notes()
        present_topics = [n["topic"] for n in present]
        self.assertIn("HUD", present_topics)
        self.assertIn("Radar", present_topics)
        self.assertIn("Reputation", present_topics)
        self.assertEqual(len(present), 3)
        hud_info = next(n["info"] for n in present if n["topic"] == "HUD")
        self.assertEqual(hud_info, "Funk synthesizer display")

    def test_required_stickies_never_dropped_on_bounding(self):
        config = {
            "adventure_mode": True,
            "max_sticky_notes": 4,
            "required_stickies": ["Required_A", "Required_B"],
            "initial_elements": {
                "Required_A": "Vital Tracker A",
                "Required_B": "Vital Tracker B",
            },
        }
        tools = self._make_tools(config=config, theater_id="req_test_eviction")
        # Add 2 dynamic notes (total: 4 notes = max capacity)
        tools.update_sticky_note("Dynamic_1", "Temp clue 1")
        tools.update_sticky_note("Dynamic_2", "Temp clue 2")
        self.assertEqual(len(tools.get_present_sticky_notes()), 4)

        # Adding Dynamic_3 should drop the oldest NON-REQUIRED note (Dynamic_1), NOT Required_A or Required_B
        res3 = tools.update_sticky_note("Dynamic_3", "Temp clue 3")
        self.assertIn("Oldest sticky note 'Dynamic_1' was dropped", res3)
        topics = [n["topic"] for n in tools.get_present_sticky_notes()]
        self.assertIn("Required_A", topics)
        self.assertIn("Required_B", topics)
        self.assertNotIn("Dynamic_1", topics)
        self.assertIn("Dynamic_2", topics)
        self.assertIn("Dynamic_3", topics)

        # Adding Dynamic_4 should drop Dynamic_2, still preserving Required_A and Required_B
        res4 = tools.update_sticky_note("Dynamic_4", "Temp clue 4")
        self.assertIn("Oldest sticky note 'Dynamic_2' was dropped", res4)
        topics = [n["topic"] for n in tools.get_present_sticky_notes()]
        self.assertIn("Required_A", topics)
        self.assertIn("Required_B", topics)
        self.assertNotIn("Dynamic_2", topics)
        self.assertIn("Dynamic_3", topics)
        self.assertIn("Dynamic_4", topics)

    def test_clear_scene_preserves_all_stickies_and_removes_characters(self):
        config = {
            "adventure_mode": True,
            "required_stickies": ["HUD", "Scanner"],
            "initial_elements": {
                "HUD": "Active status",
                "Scanner": "Radar ping",
            },
        }
        tools = self._make_tools(config=config, theater_id="req_clear_test")
        tools.update_sticky_note("Room_Clue", "A secret lever on the wall")
        tools.generate_character(name="Guard", personality="alert", motivation="stop intruders")

        self.assertEqual(len(tools.get_present_sticky_notes()), 3)
        self.assertEqual(len(tools.get_present_characters()), 1)

        # Clear scene: all stickies persist across scenes, while characters clear.
        res = tools.clear_scene()
        self.assertIn("Cleared 1 character(s)", res)
        present = tools.get_present_sticky_notes()
        present_topics = [n["topic"] for n in present]
        self.assertEqual(sorted(present_topics), ["HUD", "Room_Clue", "Scanner"])
        self.assertEqual(len(tools.get_present_characters()), 0)

    def test_required_stickies_preserved_on_import_and_reload(self):
        config = {
            "adventure_mode": True,
            "required_stickies": ["Core_HUD"],
            "initial_elements": {"Core_HUD": "Default HUD text"},
        }
        tools = self._make_tools(config=config, theater_id="req_import_test")

        # Import a state that does not contain Core_HUD
        external_state = {
            "sticky_notes": [{"topic": "Other_Item", "info": "Something else"}],
            "characters": [],
            "plot_beats": [],
        }
        tools.import_story_planning_state(external_state)
        present_topics = [n["topic"] for n in tools.get_present_sticky_notes()]
        self.assertIn("Other_Item", present_topics)
        self.assertIn("Core_HUD", present_topics)

    def test_required_stickies_updated_in_place_without_losing_protection(self):
        config = {
            "adventure_mode": True,
            "max_sticky_notes": 3,
            "required_stickies": ["HUD"],
            "initial_elements": {"HUD": "Old HUD state"},
        }
        tools = self._make_tools(config=config, theater_id="req_update_test")
        tools.update_sticky_note("Clue1", "Info 1")
        tools.update_sticky_note("Clue2", "Info 2")
        self.assertEqual(len(tools.get_present_sticky_notes()), 3)

        # Update HUD
        tools.update_sticky_note("HUD", "New HUD state with ATK +5")
        hud_info = next(n["info"] for n in tools.get_present_sticky_notes() if n["topic"] == "HUD")
        self.assertEqual(hud_info, "New HUD state with ATK +5")

        # Now add Clue3; HUD was updated and moved to end, but Clue1 (oldest non-required) should be dropped
        res = tools.update_sticky_note("Clue3", "Info 3")
        self.assertIn("Oldest sticky note 'Clue1' was dropped", res)
        topics = [n["topic"] for n in tools.get_present_sticky_notes()]
        self.assertIn("HUD", topics)
        self.assertNotIn("Clue1", topics)
        self.assertIn("Clue2", topics)
        self.assertIn("Clue3", topics)

    def test_required_stickies_all_required_capacity(self):
        config = {
            "adventure_mode": True,
            "max_sticky_notes": 2,
            "required_stickies": ["R1", "R2", "R3"],
            "initial_elements": {"R1": "1", "R2": "2", "R3": "3"},
        }
        tools = self._make_tools(config=config, theater_id="req_cap_test")
        # max_sticky_notes automatically adjusted to at least len(required_stickies)
        self.assertGreaterEqual(tools.max_sticky_notes, 3)
        topics = [n["topic"] for n in tools.get_present_sticky_notes()]
        self.assertEqual(sorted(topics), ["R1", "R2", "R3"])

    def test_planner_action_is_nonblocking_and_commits_plot_beats(self):
        completed = threading.Event()
        results = []
        billed_plans = []

        mock_reaction = {
            "narration": "The hidden doorway opens.",
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
        tools = self._make_tools(
            config={
                "adventure_mode": True,
                "nodes_ahead": 2,
                "on_scene_reaction": on_scene_reaction,
                "on_story_plan_completed": lambda: billed_plans.append(1),
            },
            theater_id="planner",
            canvas_state_service=state,
        )

        with patch.object(tools, "_run_planner_agent", return_value=mock_reaction) as mock_run:
            acknowledgement = tools.process_user_action("I light my torch.")

            self.assertEqual(acknowledgement["status"], "processing")
            self.assertTrue(completed.wait(timeout=1))
            mock_run.assert_called_once_with("I light my torch.", nudge="")
            self.assertEqual(results[0]["narration"], "The hidden doorway opens.")
            self.assertEqual(len(tools.get_plot_beats()), 2)
            self.assertEqual(billed_plans, [1])
            self.assertIn("plot_beats", tools.export_story_planning_state())
            self.assertEqual(tools.get_present_characters()[0]["name"], "Lantern Warden")
            state.get.return_value.set_scene_dialogue.assert_called_once_with(results[0]["dialogue"])
            state.get.return_value.set_narration.assert_called_once_with(results[0]["narration"])
            self.assertIn("process_user_action is on cooldown", tools.process_user_action("I open the doorway."))

    def test_clear_scene_preserves_plot_beats_and_removes_characters(self):
        tools = self._make_tools(config={"adventure_mode": True}, theater_id="clear")
        tools._characters["Mara"] = {"name": "Mara", "description": "", "personality": "Bold", "motivation": "Explore", "quirk": "Hums"}
        tools._plot_beats = [{"plot_beat": "The tide rises."}]
        tools.clear_scene()
        self.assertEqual(tools.get_present_characters(), [])
        self.assertEqual(tools.get_plot_beats(), [{"plot_beat": "The tide rises."}])

    def test_import_uses_plot_beats_contract(self):
        tools = self._make_tools(theater_id="import")
        tools.import_story_planning_state({
            "characters": [],
            "plot_beats": [{"plot_beat": "A bell rings."}],
        })
        self.assertEqual(tools.get_plot_beats(), [{"plot_beat": "A bell rings."}])

    def test_action_cooldown_scales_with_response_word_count(self):
        tools = self._make_tools(config={
            "action_cooldown_base_seconds": 10,
            "action_cooldown_words_per_second": 10,
            "action_cooldown_max_seconds": 20,
        })
        tools._last_action_response_word_count = 26

        self.assertEqual(tools.get_user_action_cooldown_seconds(), 13)

    def test_session_id_is_instance_variable_and_configurable(self):
        tools = self._make_tools(theater_id="theater-1")
        self.assertTrue(tools.session_id.startswith("planner_theater-1_"))

        custom_tools = self._make_tools(config={"session_id": "custom-session-999"}, theater_id="theater-1")
        self.assertEqual(custom_tools.session_id, "custom-session-999")

    def test_compaction_config_is_initialized_and_customizable(self):
        tools = self._make_tools()
        self.assertIsNotNone(tools.compaction_config)
        self.assertEqual(tools.compaction_config.compaction_interval, 3)
        self.assertEqual(tools.compaction_config.overlap_size, 1)
        self.assertEqual(tools.compaction_config.token_threshold, 12000)
        self.assertEqual(tools.compaction_config.event_retention_size, 6)
        self.assertIsNotNone(tools._run_compression_config)
        self.assertEqual(tools._run_compression_config.trigger_tokens, 12000)
        self.assertEqual(tools._run_compression_config.sliding_window.target_tokens, 6000)

        custom_tools = self._make_tools(config={
            "compaction": {
                "compaction_interval": 5,
                "overlap_size": 2,
                "token_threshold": 10000,
                "target_tokens": 4000,
            }
        })
        self.assertIsNotNone(custom_tools.compaction_config)
        self.assertEqual(custom_tools.compaction_config.compaction_interval, 5)
        self.assertEqual(custom_tools.compaction_config.overlap_size, 2)
        self.assertEqual(custom_tools.compaction_config.token_threshold, 10000)
        self.assertIsNotNone(custom_tools._run_compression_config)
        self.assertEqual(custom_tools._run_compression_config.trigger_tokens, 10000)
        self.assertEqual(custom_tools._run_compression_config.sliding_window.target_tokens, 4000)

        disabled_tools = self._make_tools(config={"compaction": False})
        self.assertIsNone(disabled_tools.compaction_config)
        self.assertIsNone(disabled_tools._run_compression_config)

    def test_planner_agent_resumes_from_adk_session_across_turns(self):
        turn_count = 0
        tools = self._make_tools(config={"nodes_ahead": 1, "adventure_mode": True})

        async def fake_generate_content_async(self, llm_request, stream=False):
            nonlocal turn_count
            turn_count += 1
            reaction = SceneReaction(
                narration=f"Narration for turn {turn_count}",
                plot_beats=[f"Beat {turn_count}"],
            )
            content = types.Content(role="model", parts=[types.Part(text=reaction.model_dump_json())])
            gen_resp = types.GenerateContentResponse(
                candidates=[types.Candidate(content=content, finish_reason="STOP")]
            )
            yield LlmResponse.create(gen_resp)

        with patch.object(Gemini, "generate_content_async", fake_generate_content_async):
            res1 = tools._run_planner_agent("I open the gate.")
            self.assertEqual(res1["narration"], "Narration for turn 1")

            session = asyncio.run(
                tools.session_service.get_session(
                    app_name="narratron_story_planner",
                    user_id="story_planner",
                    session_id=tools.session_id,
                )
            )
            self.assertIsNotNone(session)
            self.assertEqual(len(session.events), 2)

            res2 = tools._run_planner_agent("I step inside.")
            self.assertEqual(res2["narration"], "Narration for turn 2")

            session_after_2 = asyncio.run(
                tools.session_service.get_session(
                    app_name="narratron_story_planner",
                    user_id="story_planner",
                    session_id=tools.session_id,
                )
            )
            self.assertGreaterEqual(len(session_after_2.events), 4)

    def test_planner_compaction_triggers_on_interval(self):
        turn_count = 0
        tools = self._make_tools(config={
            "nodes_ahead": 1,
            "adventure_mode": True,
            "compaction": {"compaction_interval": 2, "overlap_size": 1},
        })

        async def fake_generate_content_async(self, llm_request, stream=False):
            nonlocal turn_count
            turn_count += 1
            reaction = SceneReaction(
                narration=f"Narration for turn {turn_count}",
                plot_beats=[f"Beat {turn_count}"],
            )
            content = types.Content(role="model", parts=[types.Part(text=reaction.model_dump_json())])
            gen_resp = types.GenerateContentResponse(
                candidates=[types.Candidate(content=content, finish_reason="STOP")]
            )
            yield LlmResponse.create(gen_resp)

        with patch.object(Gemini, "generate_content_async", fake_generate_content_async):
            tools._run_planner_agent("Action 1")
            tools._run_planner_agent("Action 2")

            session = asyncio.run(
                tools.session_service.get_session(
                    app_name="narratron_story_planner",
                    user_id="story_planner",
                    session_id=tools.session_id,
                )
            )
            compaction_events = [e for e in session.events if e.actions and e.actions.compaction]
            self.assertEqual(len(compaction_events), 1)

    def test_planner_agent_and_runner_initialized_once_at_init(self):
        tools = self._make_tools(config={"nodes_ahead": 1, "adventure_mode": True})
        self.assertIsNotNone(tools._planner_agent)
        self.assertIsNotNone(tools._planner_app)
        self.assertIsNotNone(tools._planner_runner)

        agent_ref = tools._planner_agent
        app_ref = tools._planner_app
        runner_ref = tools._planner_runner

        # Verify _get_or_create_planner_runner returns the exact instance created at init
        self.assertIs(tools._get_or_create_planner_runner(), runner_ref)
        self.assertIs(tools._planner_agent, agent_ref)
        self.assertIs(tools._planner_app, app_ref)

    def test_process_user_action_rejects_concurrent_in_flight_calls(self):
        unblock_event = threading.Event()
        started_event = threading.Event()

        def slow_resolve(action, **kwargs):
            started_event.set()
            unblock_event.wait(timeout=5)
            return {
                "narration": "Resolved",
                "dialogue": [],
                "manifested_characters": [],
                "plot_beats": [{"plot_beat": "Beat 1"}],
            }

        canvas_state_service = MagicMock()
        tools = self._make_tools(
            config={"adventure_mode": True, "nodes_ahead": 1, "action_cooldown_base_seconds": 0.0},
            theater_id="concurrent_test",
            canvas_state_service=canvas_state_service,
        )

        with patch.object(tools, "_resolve_user_action", side_effect=slow_resolve):
            res1 = tools.process_user_action("First action")
            self.assertEqual(res1["status"], "processing")
            self.assertTrue(started_event.wait(timeout=2))
            self.assertTrue(tools.is_action_in_flight)
            canvas_state_service.set_tool_activity.assert_called_once_with(
                "user_action", active=True, theater_id="concurrent_test"
            )

            # Second concurrent call while first is in flight
            res2 = tools.process_user_action("Second action")
            self.assertIn("error", res2)
            self.assertIn("already being processed", res2["error"])

            # Unblock first
            unblock_event.set()
            # Wait for in-flight to clear
            for _ in range(50):
                if not tools.is_action_in_flight:
                    break
                time.sleep(0.02)
            self.assertFalse(tools.is_action_in_flight)
            canvas_state_service.set_tool_activity.assert_called_with(
                "user_action", active=False, theater_id="concurrent_test"
            )

    def test_run_planner_agent_timeout_restarts_agent(self):
        tools = self._make_tools(
            config={
                "nodes_ahead": 1,
                "adventure_mode": True,
            },
            theater_id="timeout_test",
        )
        self.assertEqual(tools.user_action_timeout_seconds, 20.0)
        tools.user_action_timeout_seconds = 0.05
        old_runner = tools._planner_runner
        old_agent = tools._planner_agent

        async def hanging_run(*args, **kwargs):
            await asyncio.sleep(1.0)
            if False:
                yield None

        with patch.object(tools._planner_runner, "run_async", side_effect=hanging_run):
            result = tools._run_planner_agent("Action that hangs")
            self.assertIsInstance(result, dict)
            self.assertIn("error", result)
            self.assertIn("timed out after", result["error"])
            self.assertIn("killed and restarted", result["error"])

            # Agent and runner should have been restarted (new instances)
            self.assertIsNot(tools._planner_runner, old_runner)
            self.assertIsNot(tools._planner_agent, old_agent)

    def test_process_user_action_timeout_delivers_error_and_clears_in_flight(self):
        results = []
        completed = threading.Event()

        def on_reaction(res):
            results.append(res)
            completed.set()

        tools = self._make_tools(
            config={
                "adventure_mode": True,
                "nodes_ahead": 1,
                "on_scene_reaction": on_reaction,
            },
            theater_id="action_timeout_test",
        )
        tools.user_action_timeout_seconds = 0.05

        async def hanging_run(*args, **kwargs):
            await asyncio.sleep(1.0)
            if False:
                yield None

        with patch.object(tools._planner_runner, "run_async", side_effect=hanging_run):
            res = tools.process_user_action("Action")
            self.assertEqual(res["status"], "processing")
            self.assertTrue(completed.wait(timeout=2))
            self.assertEqual(len(results), 1)
            self.assertIn("error", results[0])
            self.assertIn("timed out after", results[0]["error"])
            self.assertIn("killed and restarted", results[0]["error"])
            self.assertFalse(tools.is_action_in_flight)

    def test_process_user_action_requires_voice_input_when_configured(self):
        tools = self._make_tools(
            config={
                "adventure_mode": True,
                "require_voice_input": True,
                "action_cooldown_base_seconds": 0.0,
            },
            theater_id="voice_req_test",
        )
        self.assertTrue(tools.require_voice_input)
        self.assertFalse(tools.is_voice_input_detected)

        with patch.object(tools, "_resolve_user_action") as mock_resolve:
            # First attempt without voice input is rejected
            res = tools.process_user_action("I inspect the doorway.")
            self.assertIn("error", res)
            self.assertIn("No voice input", res["error"])
            mock_resolve.assert_not_called()

            # Record voice input detection
            tools.record_voice_input()
            self.assertTrue(tools.is_voice_input_detected)

            # Processing is now allowed
            res = tools.process_user_action("I inspect the doorway.")
            self.assertEqual(res["status"], "processing")
            # Flag is consumed upon submission
            self.assertFalse(tools.is_voice_input_detected)

            # Subsequent call without new voice input is rejected
            res2 = tools.process_user_action("I walk through.")
            self.assertIn("error", res2)
            self.assertIn("No voice input", res2["error"])

            # New voice input re-enables it
            tools.record_voice_input()
            self.assertTrue(tools.is_voice_input_detected)
            res3 = tools.process_user_action("I walk through.")
            self.assertEqual(res3["status"], "processing")

    def test_search_lore_ranking_and_snippets(self):
        lore_files = [
            ("lore/01_tradeable_objects_and_pricing.txt", b"Pricing details for tradeable objects and merchants in the bazaar."),
            ("lore/02_history_and_origins.txt", b"Ancient history of the desert kingdoms and pyramids."),
            ("lore/03_hazards_and_cargo_vulnerability.txt", b"Hazards and cargo vulnerability when crossing the stormy mountains."),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            tm = TheaterManager(base_theaters_dir=temp_dir)
            tm.create_theater(
                name="Test Lore Theater",
                theater_id="test-lore-theater",
                lore_files=lore_files,
            )
            tools = self._make_tools(theater_id="test-lore-theater", theater_manager=tm)

            # Search for tradeable pricing
            res = tools.search_lore("pricing tradeable objects")
            self.assertIn("01_tradeable_objects_and_pricing.txt", res)
            self.assertIn("score:", res)

            # Search for hazards
            tools.reset_lore_call_counts()
            res_hazards = tools.search_lore("hazards vulnerability cargo")
            self.assertIn("03_hazards_and_cargo_vulnerability.txt", res_hazards)

    def test_lookup_character_empty_when_no_characters_exist(self):
        tools = self._make_tools()
        res = tools.lookup_character()
        self.assertEqual(res, "No characters have been created in this session yet.")

    def test_lookup_character_lists_all_when_query_empty(self):
        tools = self._make_tools()
        tools.generate_character(name="Captain Vance", description="Grizzled sea captain", personality="Stern", motivation="Protect crew")
        tools.generate_character(name="Elena", description="Mysterious scholar", personality="Curious", motivation="Uncover ancient truths")

        res = tools.lookup_character()
        self.assertIn("Known characters in this session (2 total):", res)
        self.assertIn("Captain Vance", res)
        self.assertIn("Elena", res)
        self.assertIn("Grizzled sea captain", res)

    def test_lookup_character_queries_by_name_or_traits(self):
        tools = self._make_tools()
        tools.generate_character(name="Marcus", description="Roman merchant", personality="Greedy", motivation="Gold", quirk="Counts coins")
        tools.generate_character(name="Aurelia", description="Patrician noble", personality="Proud", motivation="Influence", quirk="Adjusts ring")

        # Match by name
        res_marcus = tools.lookup_character("Marcus")
        self.assertIn("Found 1 character(s) matching 'Marcus':", res_marcus)
        self.assertIn("Personality: Greedy", res_marcus)
        self.assertIn("Counts coins", res_marcus)

        # Match by trait (case-insensitive)
        res_trait = tools.lookup_character("patrician")
        self.assertIn("Found 1 character(s) matching 'patrician':", res_trait)
        self.assertIn("Aurelia", res_trait)

    def test_lookup_character_returns_not_found_message(self):
        tools = self._make_tools()
        tools.generate_character(name="Marcus", description="Roman merchant")

        res = tools.lookup_character("NonexistentPerson")
        self.assertIn("No character matching 'NonexistentPerson' found. Known characters: Marcus", res)

    def test_lookup_character_registered_in_planner_agent(self):
        tools = self._make_tools(config={"adventure_mode": True})
        agent = tools._planner_agent
        tool_names = [getattr(t, "__name__", str(t)) for t in agent.tools]
        self.assertIn("lookup_character", tool_names)

    def test_max_active_characters_limits_automatically_shown_context(self):
        tools = self._make_tools(config={"max_active_characters": 2})
        tools.generate_character(name="Char1", description="First")
        tools.generate_character(name="Char2", description="Second")
        tools.generate_character(name="Char3", description="Third")

        # get_present_characters should only return the 2 most recent
        present = tools.get_present_characters()
        self.assertEqual(len(present), 2)
        self.assertEqual([c["name"] for c in present], ["Char2", "Char3"])

        # Prompt context should mention total characters and indicate truncation
        prompt = tools._build_planner_instruction()
        self.assertIn("Showing 2 most recent of 3 total session characters", prompt)
        self.assertIn("Char2", prompt)
        self.assertIn("Char3", prompt)
        self.assertNotIn("Char1", prompt)


if __name__ == "__main__":
    unittest.main()


