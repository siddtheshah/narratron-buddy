from types import SimpleNamespace
import unittest

from google.adk.tools import FunctionTool

from services.live_stream_service import get_bound_tool_instance


class _ImageTools:
    def create_image(self, prompt: str) -> str:
        return prompt


class TestGetBoundToolInstance(unittest.TestCase):
    def test_returns_instance_owning_registered_tool(self):
        image_tools = _ImageTools()
        agent = SimpleNamespace(tools=[FunctionTool(image_tools.create_image)])

        self.assertIs(get_bound_tool_instance(agent, "create_image"), image_tools)


class TestLiveStreamRunConfig(unittest.IsolatedAsyncioTestCase):
    async def test_handle_live_websocket_connection_configures_get_session_config(self):
        from unittest.mock import AsyncMock, MagicMock
        from fastapi import WebSocketDisconnect
        from google.adk.tools import FunctionTool
        from services.live_stream_service import handle_live_websocket_connection

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.receive = AsyncMock(side_effect=WebSocketDisconnect(1000, "normal"))


        class DummyToolSuite:
            def create_image(self):
                pass

            def send_chat_message(self):
                pass

            def edit_notes(self):
                pass

            def play_playlist(self):
                pass

        suite = DummyToolSuite()
        agent = SimpleNamespace(
            model="gemini-3.1-flash-live-preview",
            tools=[
                FunctionTool(suite.create_image),
                FunctionTool(suite.send_chat_message),
                FunctionTool(suite.edit_notes),
                FunctionTool(suite.play_playlist),
            ],
        )



        runner = MagicMock()

        async def fake_run_live(**kwargs):
            run_config = kwargs.get("run_config")
            self.assertIsNotNone(run_config.get_session_config)
            self.assertEqual(run_config.get_session_config.num_recent_events, 0)
            return
            yield

        runner.run_live = fake_run_live
        session_service = AsyncMock()
        session_service.get_session = AsyncMock(return_value=SimpleNamespace(events=[]))

        await handle_live_websocket_connection(
            websocket=mock_ws,
            user_id="user_1",
            session_id="session_1",
            agent=agent,
            runner=runner,
            session_service=session_service,
            config={},
            send_setup_complete_immediately=False,
            send_setup_after_delay=False,
        )


if __name__ == "__main__":
    unittest.main()



