import asyncio
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from components.theater_manager import TheaterManager
from services.agent_manager import AgentSessionManager, AgentSession
from tools.observability_tool import ObservabilityTools


class TestAgentSessionManager(unittest.TestCase):
    def test_baton_handoff_ends_outgoing_audio_without_closing_session(self):
        session = AgentSession.__new__(AgentSession)
        session.active_controller_user_id = 1
        session.send_activity_end = MagicMock()

        session.set_active_controller(2)

        session.send_activity_end.assert_called_once()
        self.assertEqual(session.active_controller_user_id, 2)
        self.assertFalse(session.can_accept_controller_input(1))
        self.assertTrue(session.can_accept_controller_input(2))

    @patch("services.agent_manager.create_tool_bundle_for_session")
    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    def test_get_or_create_session(self, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        mock_database_manager = MagicMock()
        manager = AgentSessionManager(
            theater_manager=TheaterManager(), database_manager=mock_database_manager
        )
        session1 = manager.get_or_create_session(theater_id="s1")

        self.assertIsNotNone(session1)
        self.assertEqual(session1.theater_id, "s1")
        self.assertIs(session1.database_manager, mock_database_manager)
        self.assertTrue(session1.adk_session_id.startswith("adk_s1_"))

        # Retrieving existing session returns the same instance
        session2 = manager.get_or_create_session(theater_id="s1")
        self.assertIs(session1, session2)

    @patch("services.agent_manager.create_tool_bundle_for_session")
    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    def test_stop_session(self, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager(
            theater_manager=TheaterManager(), database_manager=MagicMock()
        )
        manager.get_or_create_session(theater_id="s2")
        self.assertIsNotNone(manager.get_session("s2"))

        stopped = manager.stop_session("s2")
        self.assertTrue(stopped)
        self.assertIsNone(manager.get_session("s2"))

        # Stopping non-existent session returns False
        self.assertFalse(manager.stop_session("s2"))

    @patch("services.agent_manager.create_tool_bundle_for_session")
    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    def test_cleanup_idle_sessions(self, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager(
            theater_manager=TheaterManager(), database_manager=MagicMock()
        )
        session = manager.get_or_create_session(theater_id="s3")
        # Set last active to 10 minutes ago (600s)
        session.last_active_at = time.time() - 600.0

        expired = manager.cleanup_idle_sessions(ttl_seconds=300.0)
        self.assertIn("s3", expired)
        self.assertIsNone(manager.get_session("s3"))

    def test_run_downstream_creates_adk_session(self):
        import asyncio
        from unittest.mock import AsyncMock

        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.app_name = "test_app"

        async def mock_run_live(*args, **kwargs):
            if False:
                yield None

        mock_runner.run_live = MagicMock(side_effect=mock_run_live)
        mock_session_service = MagicMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_session_service.create_session = AsyncMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = mock_session_service

        session = AgentSession(
            theater_id="test_sess",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )

        asyncio.run(session._run_downstream())

        mock_session_service.get_session.assert_awaited_once_with(
            app_name="test_app",
            user_id=session.adk_user_id,
            session_id=session.adk_session_id,
        )
        mock_session_service.create_session.assert_awaited_once_with(
            app_name="test_app",
            user_id=session.adk_user_id,
            session_id=session.adk_session_id,
        )
        mock_runner.run_live.assert_called_once()

    def test_run_downstream_failure_notifies_ui_and_marks_thought_wandering(self):
        from unittest.mock import AsyncMock

        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.app_name = "test_app"
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        mock_runner.session_service.get_session = AsyncMock(side_effect=RuntimeError("connection lost"))
        canvas_state_manager = MagicMock()

        session = AgentSession(
            theater_id="test_failure",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            canvas_state_manager=canvas_state_manager,
        )
        session.broadcast_text = AsyncMock()

        asyncio.run(session._run_downstream())

        canvas_state_manager.set_agent_thought.assert_called_once_with("wandering")
        session.broadcast_text.assert_awaited_once_with(json.dumps({
            "type": "agent_failed",
            "detail": "Narratron lost its train of thought and stopped.",
        }))
        self.assertEqual(session.status, "stopped")

    def test_suppress_inputs_when_disconnected(self):
        import asyncio
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()

        session = AgentSession(
            theater_id="test_suppress",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )

        session.live_request_queue = MagicMock()
        mock_ws = MagicMock()

        # Connect websocket (which auto-triggers state re-enabling canvas update)
        asyncio.run(session.add_websocket(mock_ws))
        self.assertTrue(session.websocket_connected)
        session.live_request_queue.reset_mock()

        # Send input while connected succeeds
        dummy_content = MagicMock()
        dummy_blob = MagicMock()
        self.assertTrue(session.send_content(dummy_content))
        session.live_request_queue.send_content.assert_called_once_with(dummy_content)
        session.live_request_queue.send_content.reset_mock()

        self.assertTrue(session.send_realtime(dummy_blob))
        session.live_request_queue.send_realtime.assert_called_once_with(dummy_blob)
        session.live_request_queue.send_realtime.reset_mock()

        # Disconnect websocket
        asyncio.run(session.remove_websocket(mock_ws))
        self.assertFalse(session.websocket_connected)

        # Now inputs should be suppressed and discarded
        self.assertFalse(session.send_content(dummy_content))
        session.live_request_queue.send_content.assert_not_called()

        self.assertFalse(session.send_realtime(dummy_blob))
        session.live_request_queue.send_realtime.assert_not_called()

        self.assertFalse(session.send_canvas_state())

    def test_scene_reaction_callback_enqueues_planner_result(self):
        class PlannerTools:
            def process_user_action(self, user_action):
                return {"status": "processing"}

        planner_tools = PlannerTools()
        mock_image_tools = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = [SimpleNamespace(name="process_user_action", func=planner_tools.process_user_action)]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = AgentSession(theater_id="planner_queue", runner=mock_runner, tool_bundle=MagicMock())
        session.live_request_queue = MagicMock()
        session.image_tools = mock_image_tools
        session.interactive_canvas_tools = MagicMock()
        session.websockets.add(MagicMock())

        planner_tools.on_scene_reaction({"narration": "A door opens."})

        args, _ = session.live_request_queue.send_content.call_args
        self.assertIn("[Story Planner Result]", args[0].parts[0].text)
        self.assertIn("A door opens.", args[0].parts[0].text)
        mock_image_tools.record_story_plan_completed.assert_called_once()
        session.interactive_canvas_tools.record_story_plan_completed.assert_called_once()

    def test_asset_only_show_image_receives_adventure_completion_callback(self):
        class PlannerTools:
            def process_user_action(self, user_action):
                return {"status": "processing"}

        class AssetOnlyImageTools:
            def __init__(self):
                self.on_after_tool_call = None
                self.on_image_created = None
                self.record_story_plan_completed = MagicMock()

            def show_image(self, file_path):
                return f"Displayed {file_path}"

        planner_tools = PlannerTools()
        image_tools = AssetOnlyImageTools()
        mock_agent = MagicMock()
        mock_agent.tools = [
            SimpleNamespace(name="process_user_action", func=planner_tools.process_user_action),
            SimpleNamespace(name="show_image", func=image_tools.show_image),
        ]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()

        session = AgentSession(
            theater_id="asset_only_adventure",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())

        self.assertIs(session.image_tools, image_tools)
        planner_tools.on_scene_reaction({"narration": "The path opens."})

        image_tools.record_story_plan_completed.assert_called_once()
        self.assertTrue(callable(image_tools.on_after_tool_call))

    def test_voice_input_forwarded_to_story_planning_tools(self):
        mock_story_planning = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = [SimpleNamespace(name="process_user_action", func=mock_story_planning.process_user_action)]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = AgentSession(theater_id="voice_fwd", runner=mock_runner, tool_bundle=MagicMock())
        session.story_planning_tools = mock_story_planning
        session.websockets.add(MagicMock())

        session.record_audio_input(1000)
        mock_story_planning.record_voice_input.assert_called_once()

        mock_story_planning.reset_mock()
        session.send_activity_start()
        mock_story_planning.record_voice_input.assert_called_once()

        mock_story_planning.reset_mock()
        session.record_voice_activity("mic_detect")
        mock_story_planning.record_voice_input.assert_called_once()

    def test_cooldown_expired_skips_process_user_action(self):
        class MockPlannerTools:
            def __init__(self):
                self.on_cooldown_expired = None

            def process_user_action(self, user_action):
                return {}

        class MockImageTools:
            def __init__(self):
                self.on_cooldown_expired = None

            def create_image(self, prompt):
                return {}

        planner_tools = MockPlannerTools()
        image_tools = MockImageTools()
        mock_agent = MagicMock()
        mock_agent.tools = [
            SimpleNamespace(name="process_user_action", func=planner_tools.process_user_action),
            SimpleNamespace(name="create_image", func=image_tools.create_image),
        ]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = AgentSession(theater_id="cooldown_filter", runner=mock_runner, tool_bundle=MagicMock())
        session.send_content = MagicMock()

        # Trigger cooldown expired for process_user_action -> Should NOT send content
        planner_tools.on_cooldown_expired("process_user_action")
        session.send_content.assert_not_called()

        # Trigger cooldown expired for create_image -> Should send content
        image_tools.on_cooldown_expired("create_image")
        session.send_content.assert_called_once()
        args, _ = session.send_content.call_args
        self.assertIn("create_image", args[0].parts[0].text)

    def test_reenable_state_on_reconnect(self):
        import asyncio
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()

        session = AgentSession(
            theater_id="test_reconnect",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )

        session.live_request_queue = MagicMock()
        mock_ws = MagicMock()

        with patch.object(session, "send_canvas_state") as mock_send_canvas:
            # Initially disconnected
            self.assertFalse(session.websocket_connected)

            # Connect websocket -> should trigger a regular canvas state update.
            asyncio.run(session.add_websocket(mock_ws))
            self.assertTrue(session.websocket_connected)
            mock_send_canvas.assert_called_once_with()

    def test_canvas_observability_respects_startup_delay_and_interval(self):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = AgentSession(
            theater_id="test_observability_timing",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            config={
                "agent_internal": {
                    "observability_startup_delay": 10,
                    "observability_interval": 30,
                }
            },
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        session.canvas_state_manager = SimpleNamespace(
            shown_image_path=None,
            shown_image_prompt=None,
            current_playlist=None,
        )

        session.observability_available_at = 110.0
        with patch("services.agent_manager.time.monotonic", return_value=100.0):
            self.assertFalse(session.send_canvas_state())

        with patch("services.agent_manager.time.monotonic", return_value=110.0):
            self.assertTrue(session.send_canvas_state())
        self.assertEqual(session.live_request_queue.send_content.call_count, 1)

        with patch("services.agent_manager.time.monotonic", return_value=139.0):
            self.assertFalse(session.send_canvas_state())

        with patch("services.agent_manager.time.monotonic", return_value=140.0):
            self.assertTrue(session.send_canvas_state())
        self.assertEqual(session.live_request_queue.send_content.call_count, 2)

    def test_collaboration_toggle_observability_is_cooled_down_and_defers_periodic_update(self):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = AgentSession(
            theater_id="test_collaboration_observability",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            config={
                "agent_internal": {
                    "observability_interval": 30,
                    "collaboration_observability_cooldown": 5,
                }
            },
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        session.canvas_state_manager = SimpleNamespace(
            shown_image_path=None,
            shown_image_prompt=None,
            current_playlist=None,
        )
        session.observability_available_at = 0.0

        with patch("services.agent_manager.time.monotonic", return_value=100.0):
            self.assertTrue(session.send_collaboration_toggle_observability())
        with patch("services.agent_manager.time.monotonic", return_value=102.0):
            self.assertFalse(session.send_collaboration_toggle_observability())
        with patch("services.agent_manager.time.monotonic", return_value=129.0):
            self.assertFalse(session.send_canvas_state())
        with patch("services.agent_manager.time.monotonic", return_value=130.0):
            self.assertTrue(session.send_canvas_state())

        self.assertEqual(session.live_request_queue.send_content.call_count, 2)

    def test_agent_requested_observability_defers_the_next_regular_pulse(self):
        observability_tools = ObservabilityTools({"cooldown_duration": 0})
        mock_agent = MagicMock()
        mock_agent.tools = [SimpleNamespace(
            name="request_canvas_observability",
            func=observability_tools.request_canvas_observability,
        )]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = AgentSession(
            theater_id="test_agent_requested_observability",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            config={"agent_internal": {"observability_interval": 30}},
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image_file:
            image_file.write(b"canvas-image")
            image_path = image_file.name
        try:
            session.canvas_state_manager = SimpleNamespace(
                shown_image_path=image_path,
                shown_image_prompt=None,
                current_playlist=None,
            )
            session.observability_available_at = 0.0

            with patch("services.agent_manager.time.monotonic", return_value=100.0):
                self.assertIn("Current canvas state sent", observability_tools.request_canvas_observability())
            with patch("services.agent_manager.time.monotonic", return_value=129.0):
                self.assertFalse(session.send_canvas_state())
            with patch("services.agent_manager.time.monotonic", return_value=130.0):
                self.assertTrue(session.send_canvas_state())

            first_content = session.live_request_queue.send_content.call_args_list[0].args[0]
            self.assertEqual(first_content.parts[1].inline_data.mime_type, "image/png")
            self.assertEqual(first_content.parts[1].inline_data.data, b"canvas-image")
            self.assertEqual(session.live_request_queue.send_content.call_count, 2)
        finally:
            os.remove(image_path)

    def test_agent_requested_observability_attaches_doodles_with_state(self):
        """The live model must receive the annotation before it can respond."""
        mock_runner = MagicMock()
        mock_runner.agent = MagicMock(tools=[])
        mock_runner.session_service = MagicMock()
        session = AgentSession(
            theater_id="test_observability_doodles",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        canvas = MagicMock()
        canvas.viewer_collab_enabled = True
        canvas.get_doodle_snapshot_data.return_value = [{"type": "draw"}]
        canvas.get_doodle_snapshot_png.return_value = b"annotated-png"
        canvas.shown_image_path = None
        canvas.shown_image_prompt = None
        canvas.current_playlist = None
        session.canvas_state_manager = canvas

        self.assertTrue(session.send_agent_requested_observability())

        content = session.live_request_queue.send_content.call_args.args[0]
        self.assertEqual(len(content.parts), 3)
        self.assertIn("audience annotations", content.parts[1].text)
        self.assertEqual(content.parts[2].inline_data.mime_type, "image/png")
        self.assertEqual(content.parts[2].inline_data.data, b"annotated-png")
        canvas.get_doodle_snapshot_png.assert_called_once_with()

    def test_doodle_snapshot_is_rendered_off_the_event_loop(self):
        async def run_test():
            mock_agent = MagicMock()
            mock_agent.tools = []
            mock_runner = MagicMock()
            mock_runner.agent = mock_agent
            mock_runner.session_service = MagicMock()
            session = AgentSession(
                theater_id="test_async_doodle_snapshot",
                runner=mock_runner,
                tool_bundle=MagicMock(),
                config={},
            )
            session.live_request_queue = MagicMock()
            session.websockets.add(MagicMock())
            canvas = MagicMock()
            canvas.viewer_collab_enabled = True
            canvas.shown_image_path = None
            canvas.shown_image_prompt = None
            canvas.current_playlist = None
            canvas.get_doodle_snapshot_data.return_value = [{"type": "draw"}]
            canvas.get_doodle_snapshot_png.return_value = b"fake-png"
            session.canvas_state_manager = canvas
            session._event_loop = asyncio.get_running_loop()

            self.assertTrue(session.send_canvas_state())
            # The regular observability text is sent immediately; the image is
            # produced in a worker and delivered separately.
            self.assertEqual(session.live_request_queue.send_content.call_count, 1)
            await asyncio.sleep(0.05)
            self.assertEqual(canvas.get_doodle_snapshot_png.call_count, 1)
            self.assertEqual(session.live_request_queue.send_content.call_count, 2)

        asyncio.run(run_test())

    def test_usage_tracking_and_db_flushing(self):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_db = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        mock_db.get_deployment.return_value = {"user_id": 123}
        mock_db.record_user_usage.return_value = {"credits": 1.0}

        session = AgentSession(
            theater_id="test_usage",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            database_manager=mock_db,
        )

        # 1. Record image created -> triggers immediate flush
        session.record_image_created("path/to/img.jpg")
        self.assertEqual(session.images_created_count, 1)
        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 123)
        self.assertEqual(kwargs["voice_minutes"], 0.0)
        self.assertEqual(kwargs["images_created"], 1)
        self.assertEqual(kwargs["music_created"], 0)
        self.assertEqual(kwargs["story_plans"], 0)
        self.assertTrue(kwargs["idempotency_key"].startswith("live-usage:test_usage:"))
        mock_db.record_user_usage.reset_mock()

        # 2. Record music created -> triggers immediate flush
        session.record_music_created("path/to/track.mp3")
        self.assertEqual(session.music_created_count, 1)
        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 123)
        self.assertEqual(kwargs["voice_minutes"], 0.0)
        self.assertEqual(kwargs["images_created"], 0)
        self.assertEqual(kwargs["music_created"], 1)
        self.assertEqual(kwargs["story_plans"], 0)
        self.assertTrue(kwargs["idempotency_key"].startswith("live-usage:test_usage:"))
        mock_db.record_user_usage.reset_mock()

        # 3. Record PCM audio bytes (1,920,000 bytes = 1.0 minute)
        # Record 96,000 bytes (triggers automatic flush threshold)
        session.record_audio_input(96000)
        self.assertAlmostEqual(session.voice_minutes, 96000 / 1920000.0)
        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 123)
        self.assertEqual(kwargs["voice_minutes"], 96000 / 1920000.0)
        self.assertEqual(kwargs["images_created"], 0)
        self.assertEqual(kwargs["music_created"], 0)
        self.assertEqual(kwargs["story_plans"], 0)
        self.assertEqual(kwargs["interactive_canvas_used"], 0)
        self.assertTrue(kwargs["idempotency_key"].startswith("live-usage:test_usage:"))
        mock_db.record_user_usage.reset_mock()

        # 4. Record interactive canvas used -> triggers immediate flush
        session.record_interactive_canvas_used()
        self.assertEqual(session.interactive_canvas_used_count, 1)
        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 123)
        self.assertEqual(kwargs["interactive_canvas_used"], 1)
        mock_db.record_user_usage.reset_mock()

        # 5. Check get_usage dictionary
        usage = session.get_usage()
        self.assertEqual(usage["theater_id"], "test_usage")
        self.assertEqual(usage["owner_user_id"], 123)
        self.assertEqual(usage["images_created"], 1)
        self.assertEqual(usage["music_created"], 1)
        self.assertEqual(usage["story_plans"], 0)
        self.assertEqual(usage["interactive_canvas_used"], 1)

        session.record_story_plan_completed()
        self.assertEqual(session.story_plans_count, 1)
        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["story_plans"], 1)
        self.assertEqual(usage["total_audio_bytes"], 96000)

    def test_character_voicing_adds_usage_for_each_completed_planner_turn(self):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        mock_db = MagicMock()
        mock_db.get_deployment.return_value = {"user_id": 123}
        mock_db.record_user_usage.return_value = {"credits": 5.0}
        session = AgentSession(
            theater_id="test_voiced_usage",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            database_manager=mock_db,
            config={"story_planning": {"adventure_mode": True, "character_voicing": True}},
        )

        session.record_story_plan_completed()

        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["story_plans"], 1)
        self.assertEqual(kwargs["character_voiced_turns"], 1)
        self.assertEqual(session.get_usage()["character_voiced_turns"], 1)

    def test_disabled_character_voicing_does_not_add_usage(self):
        mock_runner = MagicMock()
        mock_runner.agent = MagicMock(tools=[])
        mock_runner.session_service = MagicMock()
        mock_db = MagicMock()
        mock_db.get_deployment.return_value = {"user_id": 123}
        mock_db.record_user_usage.return_value = {"credits": 5.0}
        session = AgentSession(
            theater_id="test_unvoiced_usage",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            database_manager=mock_db,
            config={"story_planning": {"adventure_mode": True, "character_voicing": False}},
        )

        session.record_story_plan_completed()

        self.assertFalse(session.character_voicing_enabled)
        self.assertEqual(mock_db.record_user_usage.call_args.kwargs["character_voiced_turns"], 0)

    def test_inject_tool_definitions(self):
        import asyncio
        from tools.tool_bundle import ToolBundle

        def sample_tool(query: str) -> str:
            """Sample search tool."""
            return query

        tool_bundle = ToolBundle([sample_tool])
        mock_agent = MagicMock()
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()

        session = AgentSession(
            theater_id="test_inject",
            runner=mock_runner,
            tool_bundle=tool_bundle,
        )
        session.live_request_queue = MagicMock()
        mock_ws = MagicMock()
        asyncio.run(session.add_websocket(mock_ws))
        session.live_request_queue.send_content.reset_mock()

        res = session.inject_tool_definitions()
        self.assertTrue(res)
        session.live_request_queue.send_content.assert_called_once()
        args, _ = session.live_request_queue.send_content.call_args
        content = args[0]
        self.assertIn("sample_tool", content.parts[0].text)

    def test_enable_tool_injection_flag_default(self):
        async def run_test():
            default_runner = MagicMock()
            default_runner.agent = MagicMock()
            default_runner.session_service = MagicMock()
            session_default = AgentSession(
                theater_id="test_flag_default",
                runner=default_runner,
                tool_bundle=MagicMock(),
            )
            self.assertFalse(session_default.enable_tool_injection)
            session_default.start_background_tasks()
            self.assertIsNone(session_default.tool_injection_task)
            if session_default.downstream_task:
                session_default.downstream_task.cancel()
            if session_default.refresh_task:
                session_default.refresh_task.cancel()

            enabled_runner = MagicMock()
            enabled_runner.agent = MagicMock()
            enabled_runner.session_service = MagicMock()
            session_enabled = AgentSession(
                theater_id="test_flag_enabled",
                runner=enabled_runner,
                tool_bundle=MagicMock(),
                config={"agent_internal": {"enable_tool_injection": True}},
            )

            self.assertTrue(session_enabled.enable_tool_injection)
            session_enabled.start_background_tasks()
            self.assertIsNotNone(session_enabled.tool_injection_task)
            if session_enabled.downstream_task:
                session_enabled.downstream_task.cancel()
            if session_enabled.refresh_task:
                session_enabled.refresh_task.cancel()



    def test_remove_websocket_saves_named_elements_to_session_state(self):
        async def run_test():
            session = AgentSession.__new__(AgentSession)
            session.theater_id = "theater_drop"
            session.ws_lock = asyncio.Lock()
            session.websockets = set()
            session.websocket_user_ids = {}
            session.flush_usage_to_db = MagicMock()

            mock_ws = MagicMock()
            session.websockets.add(mock_ws)

            mock_story_planning_tools = MagicMock()
            session.story_planning_tools = mock_story_planning_tools

            await session.remove_websocket(mock_ws)
            mock_story_planning_tools.save_to_session_state.assert_called_once()

        asyncio.run(run_test())

    def test_remove_websocket_preserves_stopped_status(self):
        async def run_test():
            session = AgentSession.__new__(AgentSession)
            session.theater_id = "theater_stopped_test"
            session.ws_lock = asyncio.Lock()
            session.websockets = set()
            session.websocket_user_ids = {}
            session.flush_usage_to_db = MagicMock()
            session.story_planning_tools = None
            session.status = "stopped"

            mock_ws = MagicMock()
            session.websockets.add(mock_ws)

            await session.remove_websocket(mock_ws)

            # status should NOT be reset to 'ready' when session is stopped
            self.assertEqual(session.status, "stopped")

        asyncio.run(run_test())

    def test_is_alive_property(self):
        session = AgentSession.__new__(AgentSession)
        session.status = "ready"
        session.downstream_task = None
        # Before tasks are started, status='ready' is considered alive
        self.assertTrue(session.is_alive)

        # Mock a running task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        session.downstream_task = mock_task
        self.assertTrue(session.is_alive)

        # When task is done
        mock_task.done.return_value = True
        self.assertFalse(session.is_alive)

        # When status is stopped
        mock_task.done.return_value = False
        session.status = "stopped"
        self.assertFalse(session.is_alive)

    def test_reconnection_after_downstream_error_creates_fresh_session_on_attempt_1(self):
        async def run_test():
            from unittest.mock import AsyncMock, patch

            mock_agent = MagicMock()
            mock_agent.tools = []

            mock_runner = MagicMock()
            mock_runner.app_name = "test_app"
            mock_runner.agent = mock_agent

            call_count = 0
            async def mock_run_live(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Gemini Live Disconnected")
                else:
                    while True:
                        await asyncio.sleep(1)
                        yield MagicMock()

            mock_runner.run_live = MagicMock(side_effect=mock_run_live)
            mock_session_service = MagicMock()
            mock_session_service.get_session = AsyncMock(return_value=None)
            mock_session_service.create_session = AsyncMock()
            mock_runner.session_service = mock_session_service

            with patch("services.agent_manager.create_tool_bundle_for_session"), \
                 patch("services.agent_manager.create_agent", return_value=mock_agent), \
                 patch("services.agent_manager.Runner", return_value=mock_runner):

                from services.agent_manager import AgentSessionManager, TheaterManager
                manager = AgentSessionManager(
                    theater_manager=TheaterManager(),
                    database_manager=MagicMock()
                )

                # Initial session
                session1 = manager.get_or_create_session(theater_id="test_reconnect")
                mock_ws1 = MagicMock()
                mock_ws1.send_text = AsyncMock()
                await session1.add_websocket(mock_ws1)

                # Allow downstream task to hit the error and close
                await asyncio.sleep(0.05)

                self.assertEqual(session1.status, "stopped")
                self.assertFalse(session1.is_alive)

                # Client disconnects upon error
                await session1.remove_websocket(mock_ws1)
                self.assertEqual(session1.status, "stopped")

                # First reconnection attempt:
                session_reconnect_1 = manager.get_or_create_session(theater_id="test_reconnect")
                self.assertIsNot(session_reconnect_1, session1)
                self.assertTrue(session_reconnect_1.is_alive)

                # Allow second downstream task to run
                await asyncio.sleep(0.05)
                self.assertEqual(call_count, 2)

                # Clean up background tasks
                session_reconnect_1.close()

        asyncio.run(run_test())

    def test_animation_ready_callback_sends_system_notification(self):
        mock_agent = MagicMock()
        mock_animation_tools = MagicMock()
        mock_agent.tools = []

        with patch("services.agent_manager.get_bound_tool_instance") as mock_get_tool:
            def side_effect(agent, tool_name):
                if tool_name == "create_animation":
                    return mock_animation_tools
                return None
            mock_get_tool.side_effect = side_effect

            session = AgentSession(
                theater_id="test_anim_notif",
                runner=MagicMock(agent=mock_agent, session_service=MagicMock()),
                tool_bundle=MagicMock(),
            )
            session.send_content = MagicMock()

            # Verify on_animation_ready callback was registered
            self.assertIsNotNone(mock_animation_tools.on_animation_ready)

            # Trigger callback
            mock_animation_tools.on_animation_ready("anim_123", "layered")

            # Check that notification was sent
            session.send_content.assert_called_once()
            content_arg = session.send_content.call_args.args[0]
            self.assertIn("anim_123", content_arg.parts[0].text)
            self.assertIn("layered", content_arg.parts[0].text)
            self.assertIn("ready to play", content_arg.parts[0].text)


if __name__ == "__main__":
    unittest.main()
