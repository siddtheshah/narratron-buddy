import time
import unittest
from unittest.mock import MagicMock, patch

from services.agent_manager import AgentSessionManager, AgentSession


class TestAgentSessionManager(unittest.TestCase):
    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_sessions_root")
    def test_get_or_create_session(self, mock_ensure_root, mock_create_agent, mock_tasks):
        mock_ensure_root.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager()
        session1 = manager.get_or_create_session(narratron_session_id="s1")

        self.assertIsNotNone(session1)
        self.assertEqual(session1.narratron_session_id, "s1")
        self.assertTrue(session1.adk_session_id.startswith("adk_s1_"))

        # Retrieving existing session returns the same instance
        session2 = manager.get_or_create_session(narratron_session_id="s1")
        self.assertIs(session1, session2)

    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_sessions_root")
    def test_stop_session(self, mock_ensure_root, mock_create_agent, mock_tasks):
        mock_ensure_root.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager()
        manager.get_or_create_session(narratron_session_id="s2")
        self.assertIsNotNone(manager.get_session("s2"))

        stopped = manager.stop_session("s2")
        self.assertTrue(stopped)
        self.assertIsNone(manager.get_session("s2"))

        # Stopping non-existent session returns False
        self.assertFalse(manager.stop_session("s2"))

    @patch("services.agent_manager.AgentSession.start_background_tasks")
    @patch("services.agent_manager.create_agent")
    @patch("services.agent_manager.ensure_sessions_root")
    def test_cleanup_idle_sessions(self, mock_ensure_root, mock_create_agent, mock_tasks):
        mock_ensure_root.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = AgentSessionManager()
        session = manager.get_or_create_session(narratron_session_id="s3")
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
            narratron_session_id="test_sess",
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


if __name__ == "__main__":
    unittest.main()

