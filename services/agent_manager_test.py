import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from components.theater_manager import TheaterManager
from services.agent_manager import AgentSessionManager, AgentSession


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

        self.assertFalse(session.send_canvas_state(force=True))

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

            # Connect websocket -> should trigger send_canvas_state(force=True)
            asyncio.run(session.add_websocket(mock_ws))
            self.assertTrue(session.websocket_connected)
            mock_send_canvas.assert_called_once_with(force=True)

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
            self.assertFalse(session.send_canvas_state(force=True))

        with patch("services.agent_manager.time.monotonic", return_value=110.0):
            self.assertTrue(session.send_canvas_state())
        self.assertEqual(session.live_request_queue.send_content.call_count, 1)

        with patch("services.agent_manager.time.monotonic", return_value=139.0):
            self.assertFalse(session.send_canvas_state())

        with patch("services.agent_manager.time.monotonic", return_value=140.0):
            self.assertTrue(session.send_canvas_state())
        self.assertEqual(session.live_request_queue.send_content.call_count, 2)

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
        mock_db.record_user_usage.assert_called_once_with(
            user_id=123,
            voice_minutes=0.0,
            images_created=1,
        )
        mock_db.record_user_usage.reset_mock()

        # 2. Record PCM audio bytes (1,920,000 bytes = 1.0 minute)
        # Record 96,000 bytes (triggers automatic flush threshold)
        session.record_audio_input(96000)
        self.assertAlmostEqual(session.voice_minutes, 96000 / 1920000.0)
        mock_db.record_user_usage.assert_called_once_with(
            user_id=123,
            voice_minutes=96000 / 1920000.0,
            images_created=0,
        )
        mock_db.record_user_usage.reset_mock()

        # 3. Check get_usage dictionary
        usage = session.get_usage()
        self.assertEqual(usage["theater_id"], "test_usage")
        self.assertEqual(usage["owner_user_id"], 123)
        self.assertEqual(usage["images_created"], 1)
        self.assertEqual(usage["total_audio_bytes"], 96000)

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
            if session_enabled.tool_injection_task:
                session_enabled.tool_injection_task.cancel()

        asyncio.run(run_test())



if __name__ == "__main__":
    unittest.main()



