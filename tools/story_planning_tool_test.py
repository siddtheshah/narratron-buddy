import asyncio
import json
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from google.adk.apps.app import EventsCompactionConfig
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from tools.story_planning_tool import (
    DEFAULT_MAX_NAMED_ELEMENTS,
    DEFAULT_STORY_PLANNING_STYLE,
    MAX_LORE_DOCUMENTS_LISTED,
    MAX_PLAYER_ACTION_CHARS,
    MAX_STORY_PLANNING_STYLE_CHARS,
    SceneReaction,
    StoryPlanningTools,
    VertexGemini,
    build_scene_reaction_prompt,
    build_story_context_prompt,
)
from components.canvas_state_service import CanvasStateService
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
        self.assertIn("Story-planning style: balanced", prompt)
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
            mock_run.assert_called_once_with("I cross the bridge.")

        self.assertEqual(tools.style, "harsh and unforgiving, but never arbitrary")
        prompt = tools._build_planner_instruction()
        self.assertIn("Story-planning style: harsh and unforgiving, but never arbitrary", prompt)

    def test_story_planning_style_is_bounded_when_loaded_from_advanced_config(self):
        tools = self._make_tools(config={"style": "x" * (MAX_STORY_PLANNING_STYLE_CHARS + 1)})

        self.assertEqual(len(tools.style), MAX_STORY_PLANNING_STYLE_CHARS)

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
        self.assertEqual(result["reason"], "Leap across the chasm")
        canvas_state_service.set_tool_activity.assert_called_once_with(
            "dice", active=True, theater_id="dice", recent_seconds=2.5,
        )

    def test_dice_roll_rejects_unsafe_ranges(self):
        tools = self._make_tools(theater_id="dice")
        self.assertIn("sides", tools.roll_dice(sides=1)["error"])
        self.assertIn("count", tools.roll_dice(count=11)["error"])

    def test_browse_lore_lists_and_reads_valid_documents(self):
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

            self.assertIn("characters.txt", tools.browse_lore())
            self.assertIn("factions/guild.txt", tools.browse_lore())
            self.assertIn("royal cartographer", tools.browse_lore("characters.txt"))
            self.assertIn("The Iron Guild", tools.browse_lore("factions/guild.txt"))
            self.assertIn("factions/guild.txt", tools.browse_lore("factions"))
            self.assertTrue(tools.browse_lore("characters.md").startswith("Error:"))

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

    def test_character_profile_uses_explicit_text_response_provider(self):
        mock_provider = MagicMock(spec=TextResponseProvider)
        mock_provider.generate.return_value = TextResponseResult(
            text=json.dumps({
                "personality": "Bold explorer",
                "motivation": "Discover uncharted territories",
            }),
            provider="mock-provider",
            model="mock-model",
        )
        tools = self._make_tools(text_response_provider=mock_provider)
        profile = tools.generate_character_profile(name="Kael")

        self.assertEqual(profile["personality"], "Bold explorer")
        self.assertEqual(profile["motivation"], "Discover uncharted territories")
        mock_provider.generate.assert_called_once()
        request = mock_provider.generate.call_args[0][0]
        self.assertIsInstance(request, TextResponseRequest)
        self.assertIn("Character name: Kael", request.prompt)
        self.assertIn("character design assistant", request.system_instruction)

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
        for index in range(DEFAULT_MAX_NAMED_ELEMENTS + 1):
            tools.update_or_insert_named_element(f"key_{index}", f"value_{index}")
        self.assertEqual(len(tools.get_present_elements()), DEFAULT_MAX_NAMED_ELEMENTS)
        self.assertEqual(tools.get_present_elements()[0]["name"], "key_1")

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
            mock_run.assert_called_once_with("I light my torch.")
            self.assertEqual(results[0]["narration"], "The hidden doorway opens.")
            self.assertEqual(len(tools.get_plot_beats()), 2)
            self.assertEqual(billed_plans, [1])
            self.assertIn("plot_beats", tools.export_story_planning_state())
            self.assertEqual(tools.get_present_characters()[0]["name"], "Lantern Warden")
            state.get.return_value.set_scene_dialogue.assert_called_once_with(results[0]["dialogue"])
            state.get.return_value.set_narration.assert_called_once_with(results[0]["narration"])
            self.assertIn("process_user_action is on cooldown", tools.process_user_action("I open the doorway."))

    def test_clear_scene_removes_plot_beats_and_characters(self):
        tools = self._make_tools(config={"adventure_mode": True}, theater_id="clear")
        tools._characters["Mara"] = {"name": "Mara", "description": "", "personality": "Bold", "motivation": "Explore", "quirk": "Hums"}
        tools._plot_beats = [{"plot_beat": "The tide rises."}]
        tools.clear_scene()
        self.assertEqual(tools.get_present_characters(), [])
        self.assertEqual(tools.get_plot_beats(), [])

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


if __name__ == "__main__":
    unittest.main()
