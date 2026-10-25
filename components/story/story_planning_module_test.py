"""Dependency-boundary tests for ``StoryPlanningModule``."""

from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
import tempfile
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from components.canvas import StoryState
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.sessions import InMemorySessionService
from google.adk.agents.run_config import RunConfig
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field, JsonValue
from components.story.story_session import StorySession
from components.reference_manager import Character, CharacterLookupResult, ReferenceManager, PlayerCharacter
from components.lore_library import LoreLibrary
from components.notepad import Notepad
from testing.game_fixtures import make_game, exchange_definition
from components.story.story_planning_module import (
    StoryPlanningModule,
    VertexGemini,
    _DEEP_PLANNING_INSTRUCTION_TEMPLATE,
    _DEEP_PLANNING_TURN_PROMPT_TEMPLATE,
    PlannerStateSnapshot,
    PlayerCharacterState,
    CharacterState,
)


def read_planning_hint() -> str:
    """Read another detail for planning."""
    return "A guard may investigate."


class BudgetLoopModel(BaseLlm):
    model: str = "budget-loop"
    requests: list[LlmRequest] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request.model_copy(deep=True))
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="read_planning_hint", args={}, id=f"hint-{len(self.requests)}",
            ))]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                candidates_token_count=min(4, llm_request.config.max_output_tokens or 4),
            ),
        )


class TestStoryPlanningModuleDependencies(unittest.TestCase):
    def test_story_budget_ends_an_agent_that_keeps_calling_tools(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary), {"deep_planning": {"token_budget_per_event": 10}})
        model = BudgetLoopModel()
        with tempfile.TemporaryDirectory() as directory:
            session = StorySession(Path(directory) / "session.sqlite", "budget")
            session.bind([Agent(
                name="story_deep_planner", model=model, tools=[read_planning_hint],
                before_model_callback=module._limit_story_budget,
                after_model_callback=module._count_story_tokens,
            )])
            try:
                assert "budget reached" in session.run("planner", "Plan around the guard.")
                assert [request.config.max_output_tokens for request in model.requests] == [10, 6, 2]
            finally:
                session.close()

    def test_story_budget_counts_generation_across_tools_and_resets_for_new_content(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary), {"deep_planning": {"token_budget_per_event": 10}})
        context = MagicMock(spec=CallbackContext)
        request = LlmRequest()
        assert module._limit_story_budget(context, request) is None
        assert request.config.max_output_tokens == 10
        response = LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=500, candidates_token_count=3, thoughts_token_count=4,
        ))
        module._count_story_tokens(context, response)
        request = LlmRequest()
        assert module._limit_story_budget(context, request) is None
        assert request.config.max_output_tokens == 3
        module._count_story_tokens(context, response)
        completion = module._limit_story_budget(context, LlmRequest())
        assert completion is not None
        assert completion.content is not None
        assert "budget reached" in completion.content.parts[0].text
        module.reset_story_budget()
        assert module._limit_story_budget(context, LlmRequest()) is None

    def test_story_budget_is_finite_without_provider_usage(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary), {"deep_planning": {"token_budget_per_event": 10}})
        context = MagicMock(spec=CallbackContext)
        module._count_story_tokens(context, LlmResponse(partial=True))
        assert module._limit_story_budget(context, LlmRequest()) is None
        module._count_story_tokens(context, LlmResponse())
        assert module._limit_story_budget(context, LlmRequest()) is not None

    def test_multiple_events_refill_budget_without_exceeding_cap(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary), {"deep_planning": {"token_budget_per_event": 10}})
        module.story_session = MagicMock(spec=StorySession)
        module.plan_story_updates({"story_updates": [{"action": "Look"}, {"action": "Listen"}]}, module.notepad.snapshot())
        assert module._story_tokens_remaining == 10
        module._count_story_tokens(MagicMock(spec=CallbackContext), LlmResponse(
            usage_metadata=types.GenerateContentResponseUsageMetadata(candidates_token_count=7),
        ))
        assert module._story_tokens_remaining == 3
        module.plan_story_updates({"story_updates": [{"action": "Wait"}, {"action": "Listen"}]}, module.notepad.snapshot())
        assert module._story_tokens_remaining == 10
        module.reset_story_budget()
        module.reset_story_budget()
        assert module._story_tokens_remaining == 10

    def test_zero_budget_skips_both_planner_entry_points(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary), {"deep_planning": {"token_budget_per_event": 0}})
        module.story_session = MagicMock(spec=StorySession)
        assert module.plan_story_updates({"story_updates": [{"action": "Look"}]}, module.notepad.snapshot()) == {}
        module.story_session.run.assert_not_called()
        with patch.object(module, "_run_deep_planner_agent") as plan:
            module.queue_deep_planning(1, "Look", {}).join(1)
        plan.assert_not_called()
        assert module.wait_for_deep_planning(timeout=0, target_turn_id=1)
        assert module.wait_for_pending_events(timeout=0)

    def test_negative_planning_budget_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "token_budget_per_event cannot be negative"):
            self._make_module(MagicMock(spec=LoreLibrary), {"deep_planning": {"token_budget_per_event": -1}})

    def test_vertex_gemini_initializes_and_reuses_client_cache(self) -> None:
        model = VertexGemini(
            model="gemini-test",
            project_id="test-project",
            location="global",
        )
        client = MagicMock()
        with patch(
            "components.story.story_models.genai.Client",
            return_value=client,
        ) as create_client:
            self.assertIs(model.api_client, client)
            self.assertIs(model.api_client, client)

        create_client.assert_called_once_with(
            project="test-project",
            location="global",
            vertexai=True,
        )

    def test_vertex_gemini_passes_http_options(self) -> None:
        from google.genai import types

        http_options = types.HttpOptions(
            headers={"X-Vertex-AI-LLM-Shared-Request-Type": "priority"}
        )
        model = VertexGemini(
            model="gemini-test",
            project_id="test-project",
            location="global",
            http_options=http_options,
        )
        client = MagicMock()
        with patch(
            "components.story.story_models.genai.Client",
            return_value=client,
        ) as create_client:
            self.assertIs(model.api_client, client)

        create_client.assert_called_once_with(
            project="test-project",
            location="global",
            http_options=http_options,
            vertexai=True,
        )

    def test_vertex_gemini_can_explicitly_use_enterprise(self) -> None:
        model = VertexGemini(
            model="gemini-test",
            project_id="test-project",
            location="global",
            enterprise=True,
        )
        with patch("components.story.story_models.genai.Client") as create_client:
            model.api_client

        create_client.assert_called_once_with(
            project="test-project",
            location="global",
            enterprise=True,
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
            patch("components.story.story_planning_module.App"),
            patch("components.story.story_planning_module.Runner"),
        ):
            canvas = MagicMock(spec=CanvasStateManager)
            canvas.story = StoryState()
            canvas.notepad = Notepad(theater, canvas_manager=canvas)
            return StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=MagicMock(spec=ReferenceManager),
                session_service=MagicMock(spec=InMemorySessionService),
                session_id="planning-boundary-session",
            )

    def test_uses_injected_provider_and_lore_library(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        reference_manager = MagicMock(spec=ReferenceManager)
        theater = MagicMock(spec=Theater)
        theater.theater_id = "planning-boundary"
        theater.config.return_value = {
            "notepad": {"max_stickies": 7, "style": "Ignored normal-note style"},
            "story_planning": {"style": "Author-defined pacing"},
        }
        theater.read_planning_schema.return_value = None
        canvas = MagicMock(spec=CanvasStateManager)
        canvas.notepad = Notepad(theater, canvas_manager=canvas)
        session_service = MagicMock(spec=InMemorySessionService)

        with (
            patch.object(StoryPlanningModule, "_create_deep_planner_agent") as create_agent,
            patch("components.story.story_planning_module.App"),
            patch("components.story.story_planning_module.Runner"),
        ):
            module = StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=reference_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
            )

        self.assertIs(module.lore_library, lore_library)
        self.assertIs(module.reference_manager, reference_manager)
        self.assertIs(module.theater, theater)
        self.assertIs(module.canvas_manager, canvas)
        self.assertIs(module.session_service, session_service)
        self.assertEqual(module.session_id, "planning-boundary-session")
        self.assertEqual(module.notepad.max_sticky_notes, 7)
        self.assertEqual(module.style, "Author-defined pacing")
        self.assertEqual(theater.config.call_count, 2)
        create_agent.assert_called_once_with()

        mock_result = CharacterLookupResult(
            characters=[Character(name="Lyra", gender="female", description="Mystic scholar")]
        )
        reference_manager.lookup_character.return_value = mock_result
        self.assertEqual(module.lookup_character("Lyra"), mock_result)
        reference_manager.lookup_character.assert_called_once_with("Lyra")

    def test_delegates_player_and_character_management(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        reference_manager = MagicMock(spec=ReferenceManager)
        reference_manager.get_player_character.return_value = PlayerCharacter(
            name="Valen", image_description="Armored knight", reference="valen_img"
        )
        reference_manager.update_player_character.return_value = PlayerCharacter(
            name="Valen", image_description="Armored knight", reference="valen_img"
        )
        reference_manager.create_or_update_character.return_value = Character(
            name="Soran", alias="soran", gender="male", personality="Brave"
        )
        module = self._make_module(lore_library)
        module.reference_manager = reference_manager

        player = module.get_player_character()
        self.assertEqual(player.name, "Valen")

        info = module.get_player_character_info()
        self.assertIn("Valen", info)

        update_msg = module.update_player_character(name="Valen", image_description="Armored knight")
        self.assertIn("Valen", update_msg)
        reference_manager.update_player_character.assert_called_once()

        char_msg = module.create_or_update_character(name="Soran", personality="Brave")
        self.assertIn("Soran", char_msg)
        reference_manager.create_or_update_character.assert_called_once()

        reference_manager.create_or_update_scene.return_value = {
            "name": "Tower",
            "path": "scenes/tower/1.png",
            "alias": "tower",
        }
        scene_msg = module.create_or_update_scene(name="Tower", description="High tower")
        self.assertIn("Tower", scene_msg)
        self.assertNotIn("scenes/tower/1.png", scene_msg)
        reference_manager.create_or_update_scene.assert_called_once_with(
            name="Tower",
            description="High tower",
        )

    def test_initializes_with_reflect_and_retry_plugin_by_default(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        theater = MagicMock(spec=Theater)
        theater.theater_id = "planning-boundary"
        theater.config.return_value = {}
        theater.read_planning_schema.return_value = None
        canvas = MagicMock(spec=CanvasStateManager)
        session_service = MagicMock(spec=InMemorySessionService)

        canvas.notepad = Notepad(theater, canvas_manager=canvas)
        with (
            patch.object(StoryPlanningModule, "_create_deep_planner_agent"),
            patch("components.story.story_planning_module.App") as mock_app_class,
            patch("components.story.story_planning_module.Runner"),
        ):
            module = StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=MagicMock(spec=ReferenceManager),
                session_service=session_service,
                session_id="default-plugin-session",
            )

        self.assertEqual(len(module.plugins), 1)
        self.assertIs(type(module.plugins[0]), ReflectAndRetryToolPlugin)
        self.assertEqual(module.plugins[0].max_retries, 3)
        self.assertTrue(module.plugins[0].throw_exception_if_retry_exceeded)
        # Verify plugins were passed to App
        mock_app_class.assert_called_once()
        _, kwargs = mock_app_class.call_args
        self.assertIn("plugins", kwargs)
        self.assertEqual(kwargs["plugins"], module.plugins)

    def test_restart_deep_planner_agent_retains_plugins(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        theater = MagicMock(spec=Theater)
        theater.theater_id = "restart-test"
        theater.config.return_value = {}
        theater.read_planning_schema.return_value = None
        canvas = MagicMock(spec=CanvasStateManager)
        canvas.notepad = Notepad(theater, canvas_manager=canvas)
        session_service = MagicMock(spec=InMemorySessionService)

        with (
            patch.object(StoryPlanningModule, "_create_deep_planner_agent"),
            patch("components.story.story_planning_module.App") as mock_app_class,
            patch("components.story.story_planning_module.Runner"),
        ):
            module = StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=MagicMock(spec=ReferenceManager),
                session_service=session_service,
                session_id="restart-session",
            )
            mock_app_class.reset_mock()
            module.restart_deep_planner_agent()

        mock_app_class.assert_called_once()
        _, kwargs = mock_app_class.call_args
        self.assertEqual(kwargs["plugins"], module.plugins)

    def test_wraps_shared_lore_operations_with_its_own_run_budgets(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        lore_library.read_lore.return_value = "read result"
        lore_library.search_lore.return_value = "search result"
        module = self._make_module(lore_library)

        self.assertEqual([module.deep_read_lore("lore.txt") for _ in range(8)], ["read result"] * 8)
        self.assertIn("Maximum deep_read_lore limit (8) reached", module.deep_read_lore("lore.txt"))
        self.assertEqual([module.deep_search_lore("clue") for _ in range(8)], ["search result"] * 8)
        self.assertIn("Maximum deep_search_lore limit (8) reached", module.deep_search_lore("clue"))
        self.assertEqual(lore_library.read_lore.call_count, 8)
        self.assertEqual(lore_library.search_lore.call_count, 8)
        lore_library.read_lore.assert_called_with("lore.txt", role="planner")

        module.reset_deep_lore_call_counts()
        self.assertEqual(module.deep_read_lore("lore.txt"), "read result")
        self.assertEqual(module.deep_search_lore("clue"), "search result")

    def test_worker_starts_without_heartbeat_delay_and_drains_later_response(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary), {"adventure_mode": True})
        module.deep_heartbeat_seconds = 60.0
        started = Event()
        finish_action = Event()
        batches: list[list[tuple[int, str, dict[str, JsonValue]]]] = []

        def plan(batch: list[tuple[int, str, dict[str, JsonValue]]]) -> dict[str, JsonValue]:
            batches.append(batch)
            if len(batches) == 1:
                started.set()
                if not finish_action.wait(timeout=3.0):
                    return {"error": "Test did not release worker"}
            return {"tool_updates": True}

        with patch.object(module, "_run_deep_planner_agent", side_effect=plan):
            try:
                worker = module.queue_deep_planning(0, "Open the door", {"source": "user_action"})
                self.assertTrue(started.wait(timeout=1.0))
                self.assertFalse(module.wait_for_deep_planning(timeout=0, target_turn_id=1))
                for turn_id in range(1, 7):
                    same_worker = module.queue_deep_planning(turn_id, "Open the door", {"narration": "The door opens."})
                    self.assertIs(same_worker, worker)
            finally:
                finish_action.set()
                self.assertTrue(module.wait_for_pending_events(timeout=3.0))
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0][0][2], {"source": "user_action"})
        self.assertEqual(batches[1][0][2], {"narration": "The door opens."})
        self.assertEqual(len(batches[1]), 6)
        self.assertTrue(module.wait_for_deep_planning(timeout=0, target_turn_id=6))

    def test_exposes_image_name_lookup_to_deep_planner(self) -> None:
        library = MagicMock(spec=ReferenceManager)
        library.find_image_names.return_value = [{"name": "tower", "alias": "tower"}]
        module = self._make_module(MagicMock(spec=LoreLibrary))
        module.reference_manager = library

        self.assertEqual(module.find_image_names("tower"), [{"name": "tower", "alias": "tower"}])
        library.find_image_names.assert_called_once_with("tower")

    def test_instruction_maximizes_prefix_and_excludes_state_variables(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        lore_library.get_lore_context.return_value = "Lore docs: history.md"
        module = self._make_module(lore_library)
        module.style = "Grimdark fantasy"

        instruction = module._build_deep_planner_instruction()
        self.assertIn("# Role & Mission", instruction)
        self.assertIn("# Non-Negotiable Planning Rules", instruction)
        self.assertIn("# Character, Lore, and Image Tools", instruction)
        self.assertIn("# Completion", instruction)
        self.assertIn("# Story-Planning Style\nGrimdark fantasy", instruction)
        self.assertIn("Available theater lore (top-level documents and directories):\nLore docs: history.md", instruction)
        # Ensure dynamic state variables are NOT in instructions to maximize prefix caching
        self.assertNotIn("Recently generated images", instruction)
        self.assertNotIn("Recent committed story log", instruction)
        self.assertNotIn("Current Sticky Notes", instruction)

    def test_compute_state_diffs_tracks_changes_accurately(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary))
        prev_snapshot = PlannerStateSnapshot(
            stickies={"HUD": "HP: 100", "Old": "Old note"},
            player_character=PlayerCharacterState(name="Aiden", image_description="Scarred"),
            characters={"Lyra": CharacterState(name="Lyra", personality="Curious", motivation="Truth", quirk="Hums")},
            known_image_names={"img1"},
            notepad_revision=1,
        )
        current_stickies = {"HUD": "HP: 80", "New": "New note"}
        current_pc = PlayerCharacterState(name="Aiden", image_description="Armored and Scarred")
        current_chars = {
            "Lyra": CharacterState(name="Lyra", personality="Curious", motivation="Revenge", quirk="Hums"),
            "Garrick": CharacterState(name="Garrick", personality="Gruff", motivation="Gold", quirk="Spits"),
        }
        recent_images: list[dict[str, object]] = [
            {"name": "img1", "alias": "img1"},
            {"name": "img2", "alias": "img2", "title": "Castle"},
        ]

        sticky_diffs, pc_diff, char_diffs, new_images, dp_diff = module._compute_state_diffs(
            prev_snapshot,
            current_stickies,
            current_pc,
            current_chars,
            recent_images,
            2,
            5,
        )
        self.assertIn("+ Added [New]: New note", sticky_diffs)
        self.assertIn('~ Updated [HUD]: was "HP: 100" -> now "HP: 80"', sticky_diffs)
        self.assertIn("- Removed [Old]", sticky_diffs)
        self.assertIn("Armored and Scarred", pc_diff)
        self.assertTrue(any("Updated Lyra" in d for d in char_diffs))
        self.assertTrue(any("Added Garrick" in d for d in char_diffs))
        self.assertEqual(len(new_images), 1)
        self.assertEqual(new_images[0]["name"], "img2")
        self.assertIn("revision advanced from 1 to 2", dp_diff)

    def test_deep_planner_agent_includes_image_name_lookup_tool(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary))

        with patch("components.story.story_planning_module.Agent") as agent_type:
            module._create_deep_planner_agent()

        self.assertIn(module.find_image_names, agent_type.call_args.kwargs["tools"])

    def test_run_deep_planner_agent_records_snapshot_at_end_of_turn(self) -> None:
        module = self._make_module(MagicMock(spec=LoreLibrary))
        self.assertIsNone(module._last_state_snapshot)
        module.reference_manager.list_references.return_value = [{
            "name": "Harbor", "alias": "<Harbor>", "description": "A quiet harbor",
        }]

        async def fake_run_async(
            *, user_id: str, session_id: str, new_message: types.Content, run_config: RunConfig,
        ) -> AsyncIterator[types.Content]:
            assert new_message.parts is not None
            prompt = new_message.parts[0].text
            assert prompt is not None
            self.assertIn("## Available References", prompt)
            self.assertIn("Name: `Harbor`", prompt)
            self.assertNotIn("Generated Images", prompt)
            if False:
                yield new_message

        mock_runner = MagicMock()
        mock_runner.run_async = fake_run_async
        with patch.object(module, "_get_or_create_deep_planner_runner", return_value=mock_runner):
            result = module._run_deep_planner_agent([])

        self.assertEqual(result, {"tool_updates": True})
        self.assertIsNotNone(module._last_state_snapshot)
        assert module._last_state_snapshot is not None
        self.assertEqual(module._last_state_snapshot.notepad_revision, 0)
        self.assertEqual(module._last_state_snapshot.known_image_names, {"Harbor"})

    def test_rejects_missing_injected_dependencies(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        reference_manager = MagicMock(spec=ReferenceManager)
        theater = MagicMock(spec=Theater)
        theater.theater_id = "planning-boundary"
        theater.config.return_value = {}
        theater.read_planning_schema.return_value = None
        canvas = MagicMock(spec=CanvasStateManager)
        notepad = Notepad(theater, canvas_manager=canvas)
        canvas.notepad = notepad
        session_service = MagicMock(spec=InMemorySessionService)

        with self.assertRaisesRegex(ValueError, "theater is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=None,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=reference_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
            )
        with self.assertRaisesRegex(ValueError, "canvas_manager is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=None,
                lore_library=lore_library,
                reference_manager=reference_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
            )
        with self.assertRaisesRegex(ValueError, "lore_library is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=None,
                reference_manager=reference_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
            )
        with self.assertRaisesRegex(ValueError, "reference_manager is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=None,
                session_service=session_service,
                session_id="planning-boundary-session",
            )
        with self.assertRaisesRegex(ValueError, "session_service is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=reference_manager,
                session_service=None,
                session_id="planning-boundary-session",
            )
        with self.assertRaisesRegex(ValueError, "session_id is required"):
            StoryPlanningModule(  # type: ignore[arg-type]
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=reference_manager,
                session_service=session_service,
                session_id="",
            )
        with self.assertRaisesRegex(ValueError, "canvas_manager.notepad is required"):
            canvas_no_notepad = MagicMock(spec=CanvasStateManager)
            canvas_no_notepad.notepad = None
            StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas_no_notepad,
                lore_library=lore_library,
                reference_manager=reference_manager,
                session_service=session_service,
                session_id="planning-boundary-session",
            )


class TestStoryPlanningModuleState(unittest.TestCase):
    def setUp(self) -> None:
        self.lore_library = MagicMock(spec=LoreLibrary)
        self.reference_manager = MagicMock(spec=ReferenceManager)
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "planning_theater"
        self.theater.read_planning_schema.return_value = None
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.canvas.story = StoryState()
        self.session_service = InMemorySessionService()
        self.config = {"story_planning": {"adventure_mode": True}}
        self.theater.config.return_value = self.config
        self.theater.read_planning_schema.return_value = {
            "HUD": {"owner": "recorder", "initial": "HP: 100 | MP: 50"},
            "Location": {"owner": "recorder", "initial": "Whispering Woods"},
            "Quest": {"owner": "planner", "initial": "Find the ancient relic"},
        }
        self.module = self._make_module(session_id="planning-state-session")

    def _make_module(
        self,
        *,
        theater: Theater | None = None,
        session_id: str,
    ) -> StoryPlanningModule:
        self.canvas.notepad = Notepad(theater or self.theater, canvas_manager=self.canvas)
        return StoryPlanningModule(
            theater=theater or self.theater,
            canvas_manager=self.canvas,
            lore_library=self.lore_library,
            reference_manager=self.reference_manager,
            session_service=self.session_service,
            session_id=session_id,
        )

    def test_initial_sticky_notes_and_required(self) -> None:
        notes = self.module.notepad.get_present_sticky_notes()

        self.assertEqual(len(notes), 3)
        self.assertEqual(
            self.module.notepad.get_required_sticky_notes(),
            ["HUD", "Location", "Quest"],
        )
        self.assertEqual(
            {note["topic"] for note in notes},
            {"HUD", "Location", "Quest"},
        )

    def test_planner_game_context_is_read_only_and_cannot_trigger_peers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            game = make_game(Path(tmp_dir), exchange_definition())
            self.module.game_state = game
            self.assertIn('"instructions": "Test exchange', self.module._game_state_json())
            for action in ("player_place", "narratron_place"):
                self.assertIn("Only the recorder", self.module.take_game_action(action, '{"item":"green"}', 0))
            self.assertEqual(game.revision, 0)
        handler = MagicMock()
        self.module.on_trigger_story_response = handler
        self.assertIn("Only StoryTool", self.module.trigger_story_response("Player moved"))
        handler.assert_not_called()
        rendered = _DEEP_PLANNING_INSTRUCTION_TEMPLATE.render(has_game=True)
        self.assertNotIn("trigger_story_response", rendered)
        self.assertNotIn("take_game_action", rendered)
        self.assertIn("read only", rendered)

    def test_game_action_heartbeat_identifies_committed_panel_action(self) -> None:
        prompt = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
            game_state_json='{"revision": 1}',
            turn_events=[{
                "source": "game", "turn_id": 0,
                "game_action_json": '{"name": "player_place", "arguments": {"item": "Pack mule"}, "revision": 0}',
                "committed_game_state_json": '{"revision": 1, "state": {"player_offer": ["Pack mule"]}}',
            }],
            is_initial=True,
        )
        self.assertIn("### Player Game Action", prompt)
        self.assertIn('"name": "player_place"', prompt)
        self.assertIn('"player_offer": ["Pack mule"]', prompt)
        self.assertNotIn("### Turn 0", prompt)

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

    def test_enforced_contract_rejects_unconfigured_notes(self) -> None:
        before = self.module.notepad.export_state()
        self.assertIn("not configured", self.module.notepad.update_sticky_note("Clue", "Found a footprint"))
        self.assertEqual(self.module.notepad.export_state(), before)

    def test_loads_structured_sticky_definitions_from_planning_contract(self) -> None:
        self.theater.read_planning_schema.return_value = {
            "Stats": {
                "owner": "recorder",
                "required": True,
                "fields": {
                    "hp": "Hit points",
                    "level": "Player level",
                },
                "initial": {"hp": "100", "level": "1"},
                "render": "HP: {hp} | LVL: {level}",
            },
            "Inventory": {
                "owner": "recorder",
                "required": False,
                "description": "Simple string list of items",
                "initial": "Sword, Shield",
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
                    "owner": "recorder",
                "required": True,
                "fields": {
                    "time": "Current time",
                    "deadline": "Summit deadline",
                },
                "initial": {"time": "9:00 AM", "deadline": "3:00 PM"},
                "render": "Time: {time} | Deadline: {deadline}",
            },
            "Contraband": {
                    "owner": "recorder",
                "required": False,
                "initial": "Quill, Turnip",
            },
            "Secret Plot": {
                    "owner": "planner",
                "required": False,
                "initial": "Mole investigating",
            },
        }
        theater.config.return_value = {"story_planning": {"adventure_mode": True}}
        theater.read_planning_schema.return_value["Secret Plot"]["hidden"] = True
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

        module.notepad.update_sticky_note("Clock", '{"time":"10:00 AM","deadline":"3:00 PM"}')
        module.notepad.update_sticky_note("Contraband", "Quill, Poison Phial")
        module.notepad.update_sticky_note("Secret Plot", "Mole discovered in archives")
        committed = module._complete_planning_heartbeat(1, {"tool_updates": True})
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

    def test_tool_driven_commit_keeps_unmodified_stickies(self) -> None:
        self.module.notepad.update_sticky_note("Quest", "The relic is now guarded")

        self.assertTrue(self.module._complete_planning_heartbeat(1, {"tool_updates": True}))
        notes = {
            note["topic"]: note["info"]
            for note in self.module.notepad.get_present_sticky_notes()
        }
        self.assertEqual(notes["Quest"], "The relic is now guarded")
        self.assertEqual(notes["HUD"], "HP: 100 | MP: 50")
        self.assertTrue(self.module.wait_for_deep_planning(timeout=0, target_turn_id=1))
        self.assertEqual(self.module.notepad.revision, 1)

    def test_unchanged_heartbeat_advances_progress_without_advancing_notepad_revision(self) -> None:
        self.assertTrue(self.module._complete_planning_heartbeat(4, {"tool_updates": True}))
        self.assertEqual(self.module.notepad.revision, 0)
        self.assertTrue(self.module.wait_for_deep_planning(timeout=0, target_turn_id=4))
        self.assertFalse(self.module.wait_for_deep_planning(timeout=0, target_turn_id=5))
        state = self.module.export_planning_state()
        self.assertEqual(state["planned_through_turn_id"], 4)
        self.assertEqual(state["notepad_revision"], 0)
        self.assertNotIn("deep_plan", state)
        self.assertNotIn("deep_plan_revision", state)

    def test_timeout_keeps_accepted_notes_versioned_without_completing_turn(self) -> None:
        async def timed_out_run(
            *, user_id: str, session_id: str, new_message: types.Content, run_config: RunConfig | None,
        ) -> AsyncIterator[types.Content]:
            assert new_message.parts is not None
            prompt = new_message.parts[0].text or ""
            self.assertIn("# Current Sticky Notes", prompt)
            self.assertNotIn("# Current Deep Plan", prompt)
            self.module.notepad.update_sticky_note("Quest", "The relic is now guarded")
            yield new_message
            raise TimeoutError("Planner exceeded its deadline")

        runner = MagicMock()
        runner.run_async = timed_out_run
        with (
            patch.object(self.module, "_get_or_create_deep_planner_runner", return_value=runner),
            patch.object(self.module, "restart_deep_planner_agent"),
        ):
            result = self.module._run_deep_planner_agent([(1, "I approach the relic", {})])
        self.assertIn("accepted tool updates remain applied", result["error"])
        self.assertFalse(self.module._complete_planning_heartbeat(1, result))
        self.assertEqual(self.module.notepad.revision, 1)
        self.assertFalse(self.module.wait_for_deep_planning(timeout=0, target_turn_id=1))
        self.assertEqual(self.module.export_planning_state()["notepad_revision"], 1)

    def test_planning_state_restores_progress_and_notepad_without_cached_plan(self) -> None:
        self.module.import_planning_state({
            "deep_plan": {"revision": 5, "through_turn_id": 7},
            "sticky_notes": [{"topic": "Quest", "info": "Gate opened"}],
        })
        self.assertEqual(self.module.notepad.revision, 5)
        self.assertTrue(self.module.wait_for_deep_planning(timeout=0, target_turn_id=7))
        state = self.module.export_planning_state()
        self.assertNotIn("deep_plan", state)
        self.module.import_planning_state(state)
        self.assertEqual(self.module.notepad.revision, 5)
        self.assertTrue(self.module.wait_for_deep_planning(timeout=0, target_turn_id=7))
        self.module.notepad.update_sticky_note("Quest", "Enter gate")
        self.assertEqual(self.module.notepad.revision, 6)


class TestDeepPlanningPromptTemplate(unittest.TestCase):
    def test_player_intent_is_distinct_from_responder_resolution(self) -> None:
        rendered = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
            turn_events=[
                {"source": "user_action", "turn_id": 0, "user_action": "I drink a potion", "nudge": "Describe its taste"},
                {"source": "story", "turn_id": 1, "user_action": "I drink a potion", "narration": "The bottle was empty."},
            ],
            is_initial=True,
            current_notes=[],
        )
        self.assertIn("Player Action (Awaiting Responder Resolution)", rendered)
        self.assertIn("Responder Response — Turn 1", rendered)
        self.assertIn("The bottle was empty.", rendered)
        instruction = _DEEP_PLANNING_INSTRUCTION_TEMPLATE.render()
        self.assertIn("never record attempted actions as successful", instruction)
        self.assertIn("never apply its costs or consequences twice", instruction)

    def test_instruction_template_renders_rules_and_tools(self) -> None:
        rendered = _DEEP_PLANNING_INSTRUCTION_TEMPLATE.render(
            style="Epic fantasy",
            sticky_schema_json='{"HUD": {"fields": {"hp": "Hit points"}}}',
            lore_context="world_lore.md",
        )
        self.assertIn("Independent Analysis & Responder Communication", rendered)
        self.assertIn("DO NOT solely rely on responder signals", rendered)
        self.assertIn("update_sticky_note", rendered)
        self.assertIn("# Story-Planning Style\nEpic fantasy", rendered)
        self.assertIn("# Sticky State Schema Reference", rendered)
        self.assertIn("# Available Theater Lore", rendered)

    def test_instruction_reflects_tri_role_architecture(self) -> None:
        rendered = _DEEP_PLANNING_INSTRUCTION_TEMPLATE.render(
            style="Dark mystery",
            sticky_schema_json='{"Plot": {"owner": "planner", "fields": {"clue": "Next clue"}}}',
            lore_context="lore.md",
        )
        self.assertIn("You own only `planner`-owned sticky notes", rendered)
        self.assertIn("never attempt to write `recorder`-owned sticky notes", rendered)
        self.assertIn("RECORDER.md", rendered)
        self.assertIn("Factual recording of inventory, location, stats, and resources is maintained by the recorder", rendered)
        self.assertIn("deep_search_lore", rendered)
        self.assertIn("deep_read_lore", rendered)
        self.assertNotIn("You are the sole owner of Adventure Mode sticky notes", rendered)

    def test_build_deep_planner_instruction_filters_to_planner_owned_schemas(self) -> None:
        lore_library = MagicMock(spec=LoreLibrary)
        lore_library.get_lore_context.return_value = "lore.md"
        theater = MagicMock(spec=Theater)
        theater.theater_id = "tri-role-test"
        theater.config.return_value = {"story_planning": {"adventure_mode": True}}
        theater.read_planning_schema.return_value = {
            "Inventory": {
                "owner": "recorder",
                "description": "Items carried",
                "initial": "Key",
            },
            "Faction Scheme": {
                "owner": "planner",
                "description": "Antagonist plans",
                "initial": "Gathering forces",
            },
        }
        canvas = MagicMock(spec=CanvasStateManager)
        notepad = Notepad(theater, canvas_manager=canvas)
        canvas.notepad = notepad
        session_service = MagicMock(spec=InMemorySessionService)

        with (
            patch.object(StoryPlanningModule, "_create_deep_planner_agent"),
            patch("components.story.story_planning_module.App"),
            patch("components.story.story_planning_module.Runner"),
        ):
            module = StoryPlanningModule(
                theater=theater,
                canvas_manager=canvas,
                lore_library=lore_library,
                reference_manager=MagicMock(spec=ReferenceManager),
                session_service=session_service,
                session_id="tri-role-session",
            )

        instruction = module._build_deep_planner_instruction()
        self.assertIn("Faction Scheme", instruction)
        self.assertNotIn("Inventory", instruction)

    def test_turn_prompt_renders_responder_signals_and_turn_events(self) -> None:
        rendered = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
            turn_events=[
                {
                    "turn_id": 1,
                    "user_action": "I drink the health potion.",
                    "narration": "A soothing warmth mends your injuries.",
                    "dialogue": [],
                    "planning_signals": [
                        "[STICKY UPDATE: Combat Stats & Synergy] HP restored to 100/100",
                        "[STICKY UPDATE: Equipment & Trait Deck] Consumed Health Potion",
                    ],
                    "characters": [],
                    "die_rolls": [],
                }
            ],
            is_initial=True,
            current_notes=[],
        )
        self.assertIn("Turn responder direct communication & planning signals:", rendered)
        self.assertIn("[STICKY UPDATE: Combat Stats & Synergy] HP restored to 100/100", rendered)

    def test_turn_prompt_renders_state_diffs_on_subsequent_turns(self) -> None:
        rendered = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
            turn_events=[],
            is_initial=False,
            sticky_diffs=[
                "+ Added [Inventory]: Sword, Shield",
                "~ Updated [HUD]: was \"HP: 100\" -> now \"HP: 80\"",
                "- Removed [OldClue]",
            ],
            player_character_diff="Name: Arthur | Visual: Knight in battered armor",
            character_diffs=[
                "+ Added Merlin: Personality: Wise | Motivation: Magic | Quirk: Mutters",
            ],
            new_images=[
                {
                    "alias": "dragon_lair",
                    "name": "dragon_lair_001",
                    "title": "Dragon's Den",
                    "description": "Smoldering cave",
                }
            ],
            notepad_diff="Notepad revision advanced from 1 to 2.",
        )
        self.assertIn("# State Diffs (Changes Since Previous Heartbeat)", rendered)
        self.assertIn("+ Added [Inventory]: Sword, Shield", rendered)
        self.assertIn("~ Updated [HUD]: was \"HP: 100\" -> now \"HP: 80\"", rendered)
        self.assertIn("- Removed [OldClue]", rendered)
        self.assertIn("## Player Character Update", rendered)
        self.assertIn("Name: Arthur | Visual: Knight in battered armor", rendered)
        self.assertIn("## Character Updates", rendered)
        self.assertIn("+ Added Merlin: Personality: Wise", rendered)
        self.assertIn("## Newly Available References", rendered)
        self.assertIn("Name: `dragon_lair_001`", rendered)
        self.assertIn("## Notepad Update", rendered)

    def test_turn_prompt_renders_no_changes_when_diffs_empty(self) -> None:
        rendered = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
            turn_events=[],
            is_initial=False,
            sticky_diffs=[],
            player_character_diff="",
            character_diffs=[],
            new_images=[],
            notepad_diff="",
        )
        self.assertIn("(No sticky notes changed since last heartbeat)", rendered)

    def test_renders_new_reference_images_in_prompt(self) -> None:
        rendered = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
            is_initial=False,
            new_images=[
                {
                    "name": "dungeon_entrance",
                    "alias": "dungeon_entrance",
                    "title": "The Iron Gate",
                    "description": "Ancient runes glowing blue",
                }
            ],
        )
        self.assertIn("## Newly Available References", rendered)
        self.assertIn("Name: `dungeon_entrance`", rendered)
        self.assertIn('Title: "The Iron Gate"', rendered)
        self.assertIn("Description: Ancient runes glowing blue", rendered)

    def test_renders_player_character_and_characters_in_prompt(self) -> None:
        player = PlayerCharacter(
            name="Aiden",
            image_description="A grim wanderer with a scarred cheek",
            reference="aiden_portrait",
        )
        rendered = _DEEP_PLANNING_TURN_PROMPT_TEMPLATE.render(
            current_notes=[],
            structured_sticky_json="",
            sticky_schema_json="",
            turn_events=[],
            max_sticky_notes=5,
            recent_images=[],
            player_character=player,
            characters=[
                Character(name="Lyra", personality="Curious", motivation="Truth", quirk="Hums", gender="female", image_reference="lyra_portrait")
            ],
        )
        self.assertIn("# Canonical Player Character", rendered)
        self.assertIn("Aiden", rendered)
        self.assertNotIn("aiden_portrait", rendered)
        self.assertNotIn("lyra_portrait", rendered)
        self.assertIn("# Canonical Active Characters", rendered)
        self.assertIn("Lyra", rendered)


if __name__ == "__main__":
    unittest.main()
