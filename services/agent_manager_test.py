import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch


from services.agent import create_agent
from services.agent_manager import AgentSessionManager, AgentSession


class TestCreateAgent(unittest.TestCase):
    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
    def test_create_agent_calls_list_references_on_init(
        self, mock_agent_cls, mock_bundle_fn
    ):
        mock_image_tools = MagicMock()
        mock_image_tools.list_references.return_value = [
            {
                "name": "hero_character",
                "alias": "hero_character",
                "path": "/path/to/hero_character.png",
                "description": "Hero character reference image",
            }
        ]
        mock_tool = MagicMock()
        mock_tool.name = "list_references"
        mock_tool.func = mock_image_tools.list_references
        mock_bundle = MagicMock()
        mock_bundle.tools = [mock_tool]
        mock_bundle_fn.return_value = mock_bundle

        theater_id = "test_agent_theater"
        agent_inst = create_agent(theater_id=theater_id)

        # Verify list_references was called immediately during create_agent
        mock_image_tools.list_references.assert_called_once()

        # Verify Agent instruction includes the preloaded references context
        mock_agent_cls.assert_called_once()
        _, kwargs = mock_agent_cls.call_args
        self.assertIn("Preloaded References Context", kwargs["instruction"])
        self.assertIn("hero_character", kwargs["instruction"])
        self.assertIn("/path/to/hero_character.png", kwargs["instruction"])
        self.assertIs(agent_inst, mock_agent_cls.return_value)

    @patch("services.agent.ImageTools")
    @patch("services.agent.ChatTools")
    @patch("services.agent.NotesTools")
    @patch("services.agent.MusicTools")
    @patch("services.agent.Agent")
    def test_create_agent_passes_canvas_state_service_to_every_tool(
        self, mock_agent_cls, mock_music_cls, mock_notes_cls, mock_chat_cls, mock_image_cls
    ):
        mock_image_cls.return_value.list_references.return_value = []
        canvas_state_service = MagicMock()
        theater_id = "test_agent_theater"
        config = {"agent": {"model_id": "test-model"}}

        create_agent(
            theater_id=theater_id,
            config=config,
            canvas_state_service=canvas_state_service,
        )

        expected_kwargs = {
            "theater_id": theater_id,
            "canvas_state_service": canvas_state_service,
        }
        mock_image_cls.assert_called_once_with(config.get("image_generation", {}), **expected_kwargs)
        mock_chat_cls.assert_called_once_with(config.get("chat", {}), **expected_kwargs)
        mock_notes_cls.assert_called_once_with(config.get("notes", {}), **expected_kwargs)
        mock_music_cls.assert_called_once_with(config.get("music", {}), **expected_kwargs)


class TestAgentSessionManager(unittest.TestCase):
    @patch("services.agent_manager.create_tool_bundle_for_session")
    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_theaters_root")
    def test_get_or_create_session(self, mock_ensure_root, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_ensure_root.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager()
        session1 = manager.get_or_create_session(theater_id="s1")

        self.assertIsNotNone(session1)
        self.assertEqual(session1.theater_id, "s1")
        self.assertTrue(session1.adk_session_id.startswith("adk_s1_"))

        # Retrieving existing session returns the same instance
        session2 = manager.get_or_create_session(theater_id="s1")
        self.assertIs(session1, session2)

    @patch("services.agent_manager.create_tool_bundle_for_session")
    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_theaters_root")
    def test_stop_session(self, mock_ensure_root, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_ensure_root.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager()
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
    @patch("services.agent_manager.ensure_theaters_root")
    def test_cleanup_idle_sessions(self, mock_ensure_root, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_ensure_root.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager()
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

    @patch("services.agent_manager.AgentSession._get_database")
    def test_usage_tracking_and_db_flushing(self, mock_get_database):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_db = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        mock_get_database.return_value = mock_db
        mock_db.get_deployment.return_value = {"user_id": 123}
        mock_db.record_user_usage.return_value = {"credits": 1.0}

        session = AgentSession(
            theater_id="test_usage",
            runner=mock_runner,
            tool_bundle=MagicMock(),
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



