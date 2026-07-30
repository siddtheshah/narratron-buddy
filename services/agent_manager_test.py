import time
import unittest
from unittest.mock import MagicMock, patch

from services.agent_manager import AgentSessionManager, AgentSession


class TestAgentSessionManager(unittest.TestCase):
    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_theaters_root")
    def test_get_or_create_session(self, mock_ensure_root, mock_create_agent, mock_tasks):
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

    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_theaters_root")
    def test_stop_session(self, mock_ensure_root, mock_create_agent, mock_tasks):
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

    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_theaters_root")
    def test_cleanup_idle_sessions(self, mock_ensure_root, mock_create_agent, mock_tasks):
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

        session = AgentSession(
            theater_id="test_sess",
            agent=mock_agent,
            runner=mock_runner,
            session_service=mock_session_service,
            artifact_service=MagicMock(),
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

        session = AgentSession(
            theater_id="test_suppress",
            agent=mock_agent,
            runner=mock_runner,
            session_service=MagicMock(),
            artifact_service=MagicMock(),
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

        session = AgentSession(
            theater_id="test_reconnect",
            agent=mock_agent,
            runner=mock_runner,
            session_service=MagicMock(),
            artifact_service=MagicMock(),
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

    def test_usage_tracking_and_db_flushing(self):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_db = MagicMock()

        session = AgentSession(
            theater_id="test_usage",
            agent=mock_agent,
            runner=mock_runner,
            session_service=MagicMock(),
            artifact_service=MagicMock(),
            db=mock_db,
            owner_user_id=123,
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


if __name__ == "__main__":
    unittest.main()


