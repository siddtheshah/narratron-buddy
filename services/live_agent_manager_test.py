import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from components.theater_manager import TheaterManager
from components.reference_manager import ReferenceManager
from google.adk.events import Event
from google.genai import types
from providers import LiveAgentProvider
from providers.gemini_live_agent_provider import GeminiLiveAgentProvider
from services.live_agent_manager import (
    AUTO_BEGIN_ADVENTURE_ACTION,
    LiveAgentSessionManager,
    LiveAgentSession,
)
from tools.observability_tool import ObservabilityTools
from tools.image import ImageTools
from services.priority_live_request_queue import PriorityLiveRequestQueue


def canvas_observability_fixture(image_path=None, collaboration_enabled=False, doodles=None):
    canvas = MagicMock()
    canvas.theater = None
    canvas.visual.shown_image_path = image_path
    canvas.visual.shown_image_prompt = None
    canvas.audio.current_playlist = None
    canvas.ui.viewer_collab_enabled = collaboration_enabled
    canvas.chat.consume_top_suggestion.return_value = None
    canvas.doodles.snapshot_batches.return_value = doodles or []
    canvas.doodles.has_visible_annotations.return_value = bool(doodles)
    canvas.doodles.snapshot_png.return_value = None
    return canvas


class TestLiveAgentSessionManager(unittest.TestCase):
    def test_downstream_bills_all_calls_even_when_tool_fails(self) -> None:
        async def events() -> AsyncGenerator[Event, None]:
            yield Event(author="agent", content=types.Content(parts=[
                types.Part(function_call=types.FunctionCall(id="lookup", name="browse_images", args={})),
                types.Part(function_call=types.FunctionCall(id="failed", name="create_image", args={})),
            ]))
            yield Event(author="agent", content=types.Content(parts=[
                types.Part(function_response=types.FunctionResponse(
                    id="failed", name="create_image", response={"error": "provider unavailable"},
                )),
            ]))

        runner = MagicMock()
        runner.agent.tools = []
        runner.session_service.get_session = AsyncMock(return_value=MagicMock())
        database = MagicMock()
        database.get_deployment.return_value = {"user_id": 7}
        database.record_user_usage.return_value = {"credits": 1.0}
        provider = MagicMock(spec=LiveAgentProvider)
        provider.id = "alternate"
        provider.background_content_is_partial = False
        provider.requires_tool_reminders = False
        provider.tool_results_bypass_input_window = False
        provider.run_live.return_value = events()
        session = LiveAgentSession(
            theater_id="all_calls", runner=runner, tool_bundle=MagicMock(),
            provider=provider, database_manager=database,
        )
        session.broadcast_text = AsyncMock()
        asyncio.run(session._run_downstream())
        database.record_user_usage.assert_called_once()
        self.assertEqual(database.record_user_usage.call_args.kwargs["live_agent_tool_calls"], 2)
        self.assertEqual(session.live_agent_tool_calls_count, 2)

    def test_tool_call_billing_retries_same_batch_without_double_counting(self) -> None:
        runner = MagicMock()
        runner.agent.tools = []
        database = MagicMock()
        database.get_deployment.return_value = {"user_id": 7}
        database.record_user_usage.side_effect = [TimeoutError("unknown commit outcome"), {"credits": 1.0}]
        session = LiveAgentSession(
            theater_id="retry_calls", runner=runner, tool_bundle=MagicMock(),
            database_manager=database,
        )
        session.record_live_agent_tool_calls(2)
        session.flush_usage_to_db()
        self.assertEqual(database.record_user_usage.call_args_list[0], database.record_user_usage.call_args_list[1])
        self.assertEqual(session.get_usage()["live_agent_tool_calls"], 2)
        self.assertEqual(session._pending_usage_batches, [])

    def test_viewer_suggestion_loop_uses_configured_interval_and_recovers(self) -> None:
        runner = MagicMock()
        runner.agent.tools = []
        session = LiveAgentSession(
            theater_id="suggestions", runner=runner, tool_bundle=MagicMock(),
            config={"live_agent": {"viewer_suggestion_interval": 7}},
        )
        session.send_viewer_suggestion = MagicMock(side_effect=[RuntimeError("temporary failure"), True])
        with patch("services.live_agent_manager.asyncio.sleep", new_callable=AsyncMock) as sleep:
            sleep.side_effect = [None, None, asyncio.CancelledError()]
            asyncio.run(session._run_viewer_suggestion_loop())
        self.assertEqual(session.send_viewer_suggestion.call_count, 2)
        self.assertEqual([call.args for call in sleep.call_args_list], [(7.0,), (7.0,), (7.0,)])

    def test_viewer_suggestions_use_notifications_and_their_own_interval(self) -> None:
        runner = MagicMock()
        runner.agent.tools = []
        canvas = canvas_observability_fixture(collaboration_enabled=True)
        canvas.chat.get_suggestions.return_value = [
            {"author": "Ada", "text": "Open the hidden door", "upvote_count": 2},
        ]
        session = LiveAgentSession(
            theater_id="suggestions", runner=runner, tool_bundle=MagicMock(),
            canvas_state_manager=canvas,
            config={"live_agent": {
                "enable_regular_observability": False, "viewer_suggestion_interval": 12,
            }},
        )
        session.websockets.add(MagicMock())
        session.send_notification = MagicMock(return_value=True)
        with patch("services.live_agent_manager.time.monotonic", return_value=100):
            self.assertTrue(session.send_viewer_suggestion())
        content = session.send_notification.call_args.args[0]
        self.assertEqual(content.role, "user")
        self.assertEqual(content.parts[0].text,
                         "[Viewer Suggestion]: Open the hidden door (by Ada, 2 upvotes)")
        canvas.chat.consume_top_suggestion.assert_called_once_with()
        canvas.persist.assert_called_once_with()
        canvas.notify_changed.assert_called_once_with("chat", "suggestions")
        with patch("services.live_agent_manager.time.monotonic", return_value=111):
            self.assertFalse(session.send_viewer_suggestion())
        with patch("services.live_agent_manager.time.monotonic", return_value=112):
            self.assertTrue(session.send_viewer_suggestion())
        self.assertIsNone(session.last_canvas_state_sent)

    def test_viewer_suggestions_are_retained_until_notification_can_be_sent(self) -> None:
        runner = MagicMock()
        runner.agent.tools = []
        canvas = canvas_observability_fixture(collaboration_enabled=True)
        canvas.chat.get_suggestions.return_value = [
            {"author": "Ada", "text": "Open the door", "upvote_count": 1},
        ]
        session = LiveAgentSession(
            theater_id="suggestions", runner=runner, tool_bundle=MagicMock(),
            canvas_state_manager=canvas,
        )
        session.send_notification = MagicMock(return_value=False)
        self.assertFalse(session.send_viewer_suggestion())
        session.websockets.add(MagicMock())
        canvas.ui.viewer_collab_enabled = False
        self.assertFalse(session.send_viewer_suggestion())
        canvas.ui.viewer_collab_enabled = True
        self.assertFalse(session.send_viewer_suggestion())
        canvas.chat.consume_top_suggestion.assert_not_called()
        self.assertIsNone(session.last_viewer_suggestion_sent)
        session.send_notification.return_value = True
        canvas.chat.get_suggestions.return_value = []
        self.assertFalse(session.send_viewer_suggestion())
        session.status = "stopped"
        self.assertFalse(session.send_viewer_suggestion())

    def test_viewer_suggestions_are_marked_as_user_role(self) -> None:
        runner = MagicMock()
        runner.agent.tools = []
        canvas = canvas_observability_fixture(collaboration_enabled=True)
        canvas.chat.get_suggestions.return_value = [
            {"author": "Ada", "text": "Open the hidden door", "upvote_count": 2},
        ]
        session = LiveAgentSession(
            theater_id="suggestions", runner=runner, tool_bundle=MagicMock(),
            canvas_state_manager=canvas,
        )
        session.websockets.add(MagicMock())
        session.live_request_queue = PriorityLiveRequestQueue(background_content_is_partial=False)
        self.assertTrue(session.send_viewer_suggestion())
        req = asyncio.run(session.live_request_queue.get())
        self.assertEqual(req.content.role, "user")
        self.assertIn("[Viewer Suggestion]: Open the hidden door", req.content.parts[0].text)

    def test_injected_provider_stream_is_closed_when_event_delivery_fails(self) -> None:
        closed: list[bool] = []

        async def events() -> AsyncGenerator[Event, None]:
            try:
                yield Event(author="alternate", turn_complete=True)
            finally:
                closed.append(True)

        runner = MagicMock()
        runner.agent.tools = []
        runner.session_service.get_session = AsyncMock(return_value=MagicMock())
        provider = MagicMock(spec=LiveAgentProvider)
        provider.id = "alternate"
        provider.background_content_is_partial = False
        provider.requires_tool_reminders = True
        provider.tool_results_bypass_input_window = False
        provider.run_live.return_value = events()
        session = LiveAgentSession(
            theater_id="alternate", runner=runner, tool_bundle=MagicMock(), provider=provider,
        )
        session.broadcast_text = AsyncMock(side_effect=[RuntimeError("browser disconnected"), None])
        asyncio.run(session._run_downstream())
        self.assertEqual(closed, [True])
        provider.build_run_config.assert_called_once()
        provider.run_live.assert_called_once()
        request = provider.run_live.call_args.args[0]
        self.assertIs(request.input_queue, session.live_request_queue)
        self.assertIs(request.run_config, provider.build_run_config.return_value)
        self.assertIs(request.runner, runner)
        self.assertEqual(request.session_id, session.adk_session_id)
        runner.run_live.assert_not_called()
        self.assertEqual(session.status, "stopped")

    def test_summon_starts_planner_and_greeting_once(self):
        class PlannerTools:
            def record_user_input(self):
                pass

            def process_user_action(self, user_action):
                return {"status": "processing"}

            def process_system_action(self, user_action, message_type):
                return {"status": "processing"}

        planner_tools = PlannerTools()
        mock_agent = MagicMock()
        mock_agent.tools = [
            SimpleNamespace(
                name="process_user_action", func=planner_tools.process_user_action
            )
        ]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="auto_begin",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            config={"story_planning": {"adventure_mode": True, "auto_begin": True}},
        )
        session.story_planning_tools.record_user_input = MagicMock()
        session.story_planning_tools.process_system_action = MagicMock(
            return_value={"status": "processing"}
        )
        session.send_user_content = MagicMock(return_value=True)

        session.summon()
        session.summon()

        session.story_planning_tools.record_user_input.assert_called_once()
        session.story_planning_tools.process_system_action.assert_called_once_with(
            AUTO_BEGIN_ADVENTURE_ACTION, "Starting/Resuming Adventure"
        )
        session.send_user_content.assert_called_once()
        greeting = session.send_user_content.call_args.args[0].parts[0].text
        self.assertEqual(greeting, "You have just been summoned. Say hello by sending a chat message.")

    def test_summon_sends_its_greeting_without_audio_activity_boundaries(self):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(theater_id="summon_turn", runner=mock_runner, tool_bundle=MagicMock())

        self.assertTrue(session.summon())

        async def read_summon_instruction():
            return await session.live_request_queue.get()

        greeting = asyncio.run(read_summon_instruction())
        self.assertEqual(greeting.content.parts[0].text, "You have just been summoned. Say hello by sending a chat message.")
        self.assertIsNone(greeting.activity_start)
        self.assertIsNone(greeting.activity_end)

    def test_auto_begin_is_disabled_unless_both_adventure_flags_are_true(self):
        planner_tools = MagicMock()
        session = LiveAgentSession.__new__(LiveAgentSession)
        session.theater_id = "not_auto_begin"
        session.config = {
            "story_planning": {"adventure_mode": False, "auto_begin": True}
        }
        session.story_planning_tools = planner_tools
        session._auto_begin_started = False

        session._auto_begin_adventure()

        planner_tools.process_user_action.assert_not_called()

    def test_auto_begin_is_skipped_after_a_recent_auto_begin(self):
        planner_tools = MagicMock()
        session = LiveAgentSession.__new__(LiveAgentSession)
        session.theater_id = "recent_auto_begin"
        session.config = {
            "story_planning": {"adventure_mode": True, "auto_begin": True}
        }
        session.story_planning_tools = planner_tools
        session._auto_begin_started = False

        recent_auto_begin = datetime.now(timezone.utc).isoformat()
        session._auto_begin_adventure(recent_auto_begin)

        planner_tools.process_system_action.assert_not_called()

    def test_auto_begin_is_skipped_when_a_new_session_is_resummoned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            theater_manager = TheaterManager(base_theaters_dir=temp_dir)
            theater_manager.create_theater(name="Auto Begin", theater_id="resummon")

            def make_session():
                mock_agent = MagicMock()
                mock_agent.tools = []
                mock_runner = MagicMock()
                mock_runner.agent = mock_agent
                mock_runner.session_service = MagicMock()
                session = LiveAgentSession(
                    theater_id="resummon",
                    runner=mock_runner,
                    tool_bundle=MagicMock(),
                    theater_manager=theater_manager,
                    config={
                        "story_planning": {
                            "adventure_mode": True,
                            "auto_begin": True,
                        }
                    },
                )
                session.story_planning_tools = MagicMock()
                session.story_planning_tools.process_system_action.return_value = {
                    "status": "processing"
                }
                session.send_user_content = MagicMock(return_value=True)
                return session

            first_session = make_session()
            first_session.summon()
            second_session = make_session()
            second_session.summon()

            first_session.story_planning_tools.process_system_action.assert_called_once()
            second_session.story_planning_tools.process_system_action.assert_not_called()
            self.assertIsNotNone(
                theater_manager.get_theater("resummon").last_auto_begin_at
            )

    def test_failed_auto_begin_is_not_persisted(self):
        planner_tools = MagicMock()
        planner_tools.process_system_action.return_value = {
            "error": "Story action is already in flight."
        }
        session = LiveAgentSession.__new__(LiveAgentSession)
        session.theater_id = "failed_auto_begin"
        session.config = {
            "story_planning": {"adventure_mode": True, "auto_begin": True}
        }
        session.story_planning_tools = planner_tools
        session.theater_manager = MagicMock()
        session._auto_begin_started = False

        session._auto_begin_adventure()

        session.theater_manager.record_auto_begin.assert_not_called()

    def test_baton_handoff_ends_outgoing_audio_without_closing_session(self):
        session = LiveAgentSession.__new__(LiveAgentSession)
        session.active_controller_user_id = 1
        session.send_activity_end = MagicMock()

        session.set_active_controller(2)

        session.send_activity_end.assert_called_once()
        self.assertEqual(session.active_controller_user_id, 2)
        self.assertFalse(session.can_accept_controller_input(1))
        self.assertTrue(session.can_accept_controller_input(2))

    @patch("services.live_agent_manager.create_tool_bundle_for_session")
    @patch("services.live_agent_manager.LiveAgentSession.start_background_tasks")
    @patch("services.live_agent_manager.create_agent")
    def test_get_or_create_session(self, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        mock_database_manager = MagicMock()
        manager = LiveAgentSessionManager(
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

    @patch("services.live_agent_manager.create_tool_bundle_for_session")
    @patch("services.live_agent_manager.LiveAgentSession.start_background_tasks")
    @patch("services.live_agent_manager.create_agent")
    def test_get_or_create_session_passes_canvas_manager_to_tool_bundle(
        self,
        mock_create_agent: MagicMock,
        mock_tasks: MagicMock,
        mock_create_bundle: MagicMock,
    ) -> None:
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        canvas_mgr = MagicMock()
        canvas_service = MagicMock()
        canvas_service.get.return_value = canvas_mgr

        manager = LiveAgentSessionManager(
            theater_manager=TheaterManager(),
            database_manager=MagicMock(),
            canvas_state_service=canvas_service,
        )
        manager.get_or_create_session(theater_id="test_canvas_prop")

        mock_create_bundle.assert_called_once()
        self.assertIs(mock_create_bundle.call_args.kwargs["canvas_manager"], canvas_mgr)

    @patch("services.live_agent_manager.create_tool_bundle_for_session")
    @patch("services.live_agent_manager.LiveAgentSession.start_background_tasks")
    @patch("services.live_agent_manager.create_agent")
    def test_character_voicing_uses_configured_speech_provider(
        self,
        mock_create_agent,
        mock_tasks,
        mock_create_bundle,
    ):
        mock_agent = MagicMock(tools=[])
        mock_create_agent.return_value = mock_agent
        canvas_manager = MagicMock()
        canvas_state_service = MagicMock()
        canvas_state_service.get.return_value = canvas_manager
        configured_provider = MagicMock()
        config = {
            "story_planning": {
                "adventure_mode": True,
                "character_voicing": True,
            },
            "speech": {
                "provider": "gemini-flash-tts",
                "model": "gemini-3.1-flash-tts-preview",
            },
        }

        with (
            patch("services.live_agent_manager.get_theater_config", return_value=config),
            patch(
                "services.live_agent_manager.get_speech_provider",
                return_value=configured_provider,
            ) as get_provider,
        ):
            manager = LiveAgentSessionManager(
                theater_manager=TheaterManager(),
                database_manager=MagicMock(),
            )
            manager.get_or_create_session(
                theater_id="gemini-voicing",
                canvas_state_service=canvas_state_service,
            )

        get_provider.assert_called_once_with("gemini-flash-tts", config["speech"])
        canvas_manager.story.enable_scene_speech.assert_called_once_with(
            configured_provider
        )

    @patch("services.live_agent_manager.create_tool_bundle_for_session")
    @patch("services.live_agent_manager.LiveAgentSession.start_background_tasks")
    @patch("services.live_agent_manager.create_agent")
    def test_stop_session(self, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = LiveAgentSessionManager(
            theater_manager=TheaterManager(), database_manager=MagicMock()
        )
        manager.get_or_create_session(theater_id="s2")
        self.assertIsNotNone(manager.get_session("s2"))

        stopped = manager.stop_session("s2")
        self.assertTrue(stopped)
        self.assertIsNone(manager.get_session("s2"))

        # Stopping non-existent session returns False
        self.assertFalse(manager.stop_session("s2"))

    @patch("services.live_agent_manager.create_tool_bundle_for_session")
    @patch("services.live_agent_manager.LiveAgentSession.start_background_tasks")
    @patch("services.live_agent_manager.create_agent")
    def test_cleanup_idle_sessions(self, mock_create_agent, mock_tasks, mock_create_bundle):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_create_agent.return_value = mock_agent

        manager = LiveAgentSessionManager(
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

        session = LiveAgentSession(
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

        session = LiveAgentSession(
            theater_id="test_failure",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            canvas_state_manager=canvas_state_manager,
        )
        session.broadcast_text = AsyncMock()

        asyncio.run(session._run_downstream())

        canvas_state_manager.tool_response.set_agent_thought.assert_called_once_with("wandering")
        session.broadcast_text.assert_awaited_once_with(json.dumps({
            "type": "agent_failed",
            "detail": "Narratron lost its train of thought and stopped.",
        }))
        self.assertEqual(session.status, "stopped")

    def test_session_accepts_text_without_a_microphone_connection(self):
        import asyncio
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()

        session = LiveAgentSession(
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

        # Text and system input remain available after Summon, without a mic.
        self.assertTrue(session.send_content(dummy_content))
        session.live_request_queue.send_content.assert_called_once_with(dummy_content)
        session.live_request_queue.send_content.reset_mock()

        # Realtime audio still requires the microphone WebSocket.
        self.assertFalse(session.send_realtime(dummy_blob))
        session.live_request_queue.send_realtime.assert_not_called()

        self.assertFalse(session.send_canvas_state())

    def test_scene_reaction_callback_enqueues_planner_result(self) -> None:
        class PlannerTools:
            def process_user_action(self, user_action: str) -> dict[str, str]:
                return {"status": "processing"}

        planner_tools = PlannerTools()
        mock_image_tools = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = [SimpleNamespace(name="process_user_action", func=planner_tools.process_user_action)]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(theater_id="planner_queue", runner=mock_runner, tool_bundle=MagicMock())
        session.live_request_queue = MagicMock()
        session.image_tools = mock_image_tools
        session.interactive_canvas_tools = MagicMock()
        session.websockets.add(MagicMock())

        result = {"narration": "A door opens.", "dialogue": [{"speaker": "Guard", "text": "Go inside."}]}
        planner_tools.on_scene_reaction(result)

        session.live_request_queue.send_notification.assert_called_once()
        session.live_request_queue.send_user_input.assert_not_called()
        args, _ = session.live_request_queue.send_notification.call_args
        message = args[0].parts[0].text
        self.assertTrue(message.startswith("[System Notification] [Story Planner Result]\n"))
        self.assertIn("System-generated story planner output, not user input.", message)
        self.assertIn("do not submit it to process_user_action", message)
        self.assertEqual(json.loads(message.split("\n", 2)[2]), result)
        mock_image_tools.record_story_plan_completed.assert_called_once()
        session.interactive_canvas_tools.record_story_plan_completed.assert_called_once()

    def test_asset_only_show_image_receives_adventure_completion_callback(self):
        class PlannerTools:
            def process_user_action(self, user_action):
                return {"status": "processing"}

        class AssetOnlyImageTools:
            def __init__(self):
                self.reference_manager = MagicMock(spec=ReferenceManager)
                self.reference_manager.available_character_images.return_value = {}
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

        session = LiveAgentSession(
            theater_id="asset_only_adventure",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())

        self.assertIs(session.image_tools, image_tools)
        self.assertIs(session.reference_manager, image_tools.reference_manager)
        planner_tools.on_scene_reaction({"narration": "The path opens."})

        image_tools.record_story_plan_completed.assert_called_once()
        self.assertTrue(callable(image_tools.on_after_tool_call))

    def test_user_input_forwarded_to_story_planning_tools(self):
        mock_story_planning = MagicMock()
        mock_agent = MagicMock()
        mock_agent.tools = [SimpleNamespace(name="process_user_action", func=mock_story_planning.process_user_action)]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(theater_id="voice_fwd", runner=mock_runner, tool_bundle=MagicMock())
        session.story_planning_tools = mock_story_planning
        session.websockets.add(MagicMock())

        session.record_audio_input(1000)
        mock_story_planning.record_user_input.assert_called_once()

        mock_story_planning.reset_mock()
        session.send_activity_start()
        mock_story_planning.record_user_input.assert_called_once()

        mock_story_planning.reset_mock()
        session.record_voice_activity("mic_detect")
        mock_story_planning.record_user_input.assert_called_once()

        mock_story_planning.reset_mock()
        session.live_request_queue = MagicMock()
        self.assertTrue(session.send_user_content(MagicMock()))
        mock_story_planning.record_user_input.assert_called_once()
        session.live_request_queue.send_user_input.assert_called_once()

    def test_cooldown_expired_skips_process_user_action(self):
        class MockPlannerTools:
            def __init__(self):
                self.on_cooldown_expired = None

            def process_user_action(self, user_action):
                return {}

        class MockImageTools:
            def __init__(self):
                self.reference_manager: ReferenceManager | None = None
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
        session = LiveAgentSession(theater_id="cooldown_filter", runner=mock_runner, tool_bundle=MagicMock())
        session.send_content = MagicMock()

        # Trigger cooldown expired for process_user_action -> Should NOT send content
        planner_tools.on_cooldown_expired("process_user_action")
        session.send_content.assert_not_called()

        image_tools.on_cooldown_expired("image_cycle")
        session.send_content.assert_not_called()

        # Trigger cooldown expired for create_image -> Should send content
        image_tools.on_cooldown_expired("create_image")
        session.send_content.assert_called_once()
        args, _ = session.send_content.call_args
        self.assertIn("create_image", args[0].parts[0].text)
        self.assertEqual(args[0].role, "system")

    def test_observability_cooldown_is_a_non_triggering_system_message(self) -> None:
        async def run_test(background_content_is_partial: bool) -> None:
            theater = MagicMock()
            theater.config.return_value = {"observability_tool": {"cooldown_duration": 30}}
            observability_tools = ObservabilityTools(theater, MagicMock())
            runner = MagicMock()
            runner.agent.tools = [SimpleNamespace(
                name="request_canvas_observability",
                func=observability_tools.request_canvas_observability,
            )]
            session = LiveAgentSession(
                theater_id="observability_cooldown", runner=runner, tool_bundle=MagicMock(),
            )
            session.live_request_queue = PriorityLiveRequestQueue(
                background_content_is_partial=background_content_is_partial,
            )
            session.send_notification = MagicMock()
            session.record_user_input = MagicMock()

            self.assertIsNotNone(observability_tools.on_cooldown_expired)
            observability_tools.on_cooldown_expired("request_canvas_observability")
            request = await asyncio.wait_for(session.live_request_queue.get(), timeout=1)

            self.assertEqual(request.content.role, "system")
            self.assertTrue(request.partial)
            self.assertIn("request_canvas_observability", request.content.parts[0].text)
            session.send_notification.assert_not_called()
            session.record_user_input.assert_not_called()

        for background_content_is_partial in (False, True):
            with self.subTest(background_content_is_partial=background_content_is_partial):
                asyncio.run(run_test(background_content_is_partial))

    def test_reenable_state_on_reconnect(self):
        import asyncio
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()

        session = LiveAgentSession(
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

    def make_observability_session(self) -> LiveAgentSession:
        runner = MagicMock()
        runner.agent.tools = []
        session = LiveAgentSession(
            theater_id="observability", runner=runner, tool_bundle=MagicMock(),
            provider=GeminiLiveAgentProvider(),
            config={"live_agent": {"observability_interval": 0, "collaboration_observability_cooldown": 0}},
        )
        session.websockets.add(MagicMock())
        session.canvas_state_manager = canvas_observability_fixture()
        session.observability_available_at = 0.0
        return session

    def test_observability_is_partial_for_complete_turn_provider(self) -> None:
        session = self.make_observability_session()
        self.assertFalse(session.provider.background_content_is_partial)

        async def run_test() -> None:
            self.assertTrue(session.send_canvas_state())
            request = await session.live_request_queue.get()
            self.assertTrue(request.partial)
            self.assertIn("[Canvas Image]", request.content.parts[0].text)

        asyncio.run(run_test())

    def test_observability_deduplicates_across_sources_and_sends_changed_state(self) -> None:
        session = self.make_observability_session()
        session.live_request_queue = MagicMock()
        self.assertTrue(session.send_canvas_state())
        self.assertFalse(session.send_canvas_state())
        self.assertFalse(session.send_collaboration_toggle_observability())
        self.assertFalse(session.send_agent_requested_observability())
        self.assertEqual(session.live_request_queue.send_content.call_count, 1)

        session.canvas_state_manager.visual.shown_image_prompt = "A changed scene"
        self.assertTrue(session.send_collaboration_toggle_observability())
        self.assertFalse(session.send_canvas_state())
        session.canvas_state_manager.visual.shown_image_prompt = None
        self.assertTrue(session.send_agent_requested_observability())
        self.assertEqual(session.live_request_queue.send_content.call_count, 3)
        for call in session.live_request_queue.send_content.call_args_list:
            self.assertEqual(call.kwargs, {"partial": True})

    def test_observability_deduplicates_images_and_detects_changed_annotations(self) -> None:
        session = self.make_observability_session()
        session.live_request_queue = MagicMock()
        canvas = canvas_observability_fixture(collaboration_enabled=True, doodles=[{"type": "draw"}])
        canvas.doodles.snapshot_png.return_value = b"first-annotation"
        session.canvas_state_manager = canvas
        self.assertTrue(session.send_agent_requested_observability())
        self.assertFalse(session.send_agent_requested_observability())
        self.assertFalse(session.send_canvas_state())
        asyncio.run(session._send_doodle_snapshot())
        self.assertEqual(session.live_request_queue.send_content.call_count, 1)

        canvas.doodles.snapshot_png.return_value = b"changed-annotation"
        asyncio.run(session._send_doodle_snapshot())
        self.assertEqual(session.live_request_queue.send_content.call_count, 2)
        self.assertFalse(session.send_agent_requested_observability())
        for call in session.live_request_queue.send_content.call_args_list:
            self.assertEqual(call.kwargs, {"partial": True})

    def test_observability_retries_failed_enqueue_without_caching_state(self) -> None:
        session = self.make_observability_session()
        session.live_request_queue = MagicMock()
        session.live_request_queue.send_content.side_effect = [RuntimeError("enqueue failed"), None]
        self.assertFalse(session.send_canvas_state())
        self.assertIsNone(session.last_canvas_state_sent)
        self.assertTrue(session.send_canvas_state())
        self.assertFalse(session.send_canvas_state())
        self.assertEqual(session.live_request_queue.send_content.call_count, 2)

    def test_canvas_capture_is_reusable_and_preserves_previous_annotations(self) -> None:
        from PIL import Image
        from components.canvas.doodle_state import DoodleState
        from components.canvas.visual_state import VisualState
        from components.reference_manager import ReferenceManager

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "scene.png"
            Image.new("RGB", (80, 80), "black").save(image_path)
            canvas = canvas_observability_fixture(str(image_path), True)
            canvas.theater = TheaterManager(directory).theater("capture")
            canvas.doodles = DoodleState(MagicMock())
            canvas.doodles.doodles = [{
                "type": "draw", "x0": 0.1, "y0": 0.5, "x1": 0.9, "y1": 0.5,
                "color": "#ff0000", "size": 5,
            }]
            session = self.make_observability_session()
            session.canvas_state_manager = canvas
            session.live_request_queue = MagicMock()

            self.assertTrue(session.send_agent_requested_observability())
            content = session.live_request_queue.send_content.call_args.args[0]
            self.assertEqual(content.role, "user")
            capture_path = content.parts[0].text.split("[Canvas Capture]: ", 1)[1].splitlines()[0]
            self.assertTrue(Path(capture_path).is_absolute())
            first_capture = Path(capture_path).read_bytes()
            self.assertEqual(first_capture, content.parts[-1].inline_data.data)
            self.assertNotEqual(first_capture, image_path.read_bytes())
            ref_mgr = ReferenceManager(canvas.theater)
            references, error = ref_mgr.resolve_provider_references(
                [capture_path], visual=VisualState(canvas.theater),
            )
            self.assertIsNone(error)
            self.assertEqual(references[0].data, first_capture)
            self.assertEqual(references[0].mime_type, "image/png")
            self.assertFalse(session.send_agent_requested_observability())

            canvas.doodles.doodles[0]["color"] = "#00ff00"
            asyncio.run(session._send_doodle_snapshot())
            content = session.live_request_queue.send_content.call_args.args[0]
            self.assertEqual(content.role, "user")
            next_path = content.parts[0].text.split("[Canvas Capture]: ", 1)[1].splitlines()[0]
            self.assertNotEqual(next_path, capture_path)
            self.assertEqual(Path(next_path).read_bytes(), content.parts[-1].inline_data.data)
            self.assertEqual(Path(capture_path).read_bytes(), first_capture)
            self.assertFalse(session.send_agent_requested_observability())
            self.assertEqual(session.live_request_queue.send_content.call_count, 2)

    def test_story_snapshot_omits_capture_reference_and_preserves_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            canvas = canvas_observability_fixture(collaboration_enabled=True)
            canvas.theater = TheaterManager(directory).theater("story_snapshot")
            canvas.doodles.has_visible_annotations.return_value = True
            canvas.doodles.snapshot_png.return_value = b"annotated-png"
            session = self.make_observability_session()
            session.canvas_state_manager = canvas
            session.live_request_queue = MagicMock()

            self.assertTrue(session.send_agent_requested_observability(
                force=True, include_capture_reference=False,
            ))

            content = session.live_request_queue.send_content.call_args.args[0]
            self.assertEqual(content.role, "user")
            text = "\n".join(part.text for part in content.parts if part.text is not None)
            self.assertNotIn("[Canvas Capture]", text)
            self.assertNotIn("reference_images", text)
            self.assertIn("audience annotations", text)
            self.assertEqual(content.parts[-1].inline_data.data, b"annotated-png")
            self.assertFalse(canvas.theater.canvas_captures_dir().exists())

    def test_canvas_capture_includes_stamp_imagery(self) -> None:
        import io
        from PIL import Image
        from components.canvas.doodle_state import DoodleState

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "scene.png"
            Image.new("RGB", (100, 100), (0, 0, 0)).save(image_path)

            theater = TheaterManager(directory).theater("capture_stamp")
            stamps_dir = theater.stamps_dir()
            stamps_dir.mkdir(parents=True, exist_ok=True)
            stamp_path = stamps_dir / "token.png"
            Image.new("RGBA", (20, 20), (0, 255, 0, 255)).save(stamp_path)

            canvas = canvas_observability_fixture(str(image_path), True)
            canvas.theater = theater
            canvas.doodles = DoodleState(MagicMock(), theater=theater)
            canvas.doodles.save_stamp({
                "type": "stamp",
                "id": "stamp-1",
                "stamp_id": "theater:capture_stamp:token.png",
                "url": "/theaters/capture_stamp/stamps/token.png",
                "name": "Token",
                "x": 0.5,
                "y": 0.5,
                "size": 80.0,
            })

            session = self.make_observability_session()
            session.canvas_state_manager = canvas
            session.live_request_queue = MagicMock()

            self.assertTrue(session.send_agent_requested_observability())
            content = session.live_request_queue.send_content.call_args.args[0]
            self.assertEqual(content.role, "user")
            self.assertIn("[Canvas Capture]: ", content.parts[0].text)
            capture_path = content.parts[0].text.split("[Canvas Capture]: ", 1)[1].splitlines()[0]
            self.assertTrue(Path(capture_path).is_file())

            capture_bytes = Path(capture_path).read_bytes()
            self.assertEqual(capture_bytes, content.parts[-1].inline_data.data)

            with Image.open(io.BytesIO(capture_bytes)) as rendered:
                center_pixel = rendered.convert("RGB").getpixel((50, 50))
                # The green stamp imagery must appear in the saved canvas capture
                self.assertEqual(center_pixel, (0, 255, 0))

    def test_canvas_capture_without_collaboration_and_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "scene.jpg"
            image_path.write_bytes(b"scene-image")
            canvas = canvas_observability_fixture(str(image_path))
            canvas.theater = TheaterManager(directory).theater("capture")
            session = self.make_observability_session()
            session.canvas_state_manager = canvas
            session.live_request_queue = MagicMock()
            self.assertTrue(session.send_agent_requested_observability())
            content = session.live_request_queue.send_content.call_args.args[0]
            capture_path = content.parts[0].text.split("[Canvas Capture]: ", 1)[1].splitlines()[0]
            self.assertEqual(Path(capture_path).read_bytes(), b"scene-image")
            self.assertIn("reference_images", content.parts[0].text)
            self.assertEqual(content.parts[-1].inline_data.mime_type, "image/jpeg")

            image_path.write_bytes(b"changed-scene")
            with patch("services.live_agent_manager.Path.mkdir", side_effect=OSError("disk full")):
                self.assertTrue(session.send_agent_requested_observability())
            content = session.live_request_queue.send_content.call_args.args[0]
            self.assertNotIn("[Canvas Capture]", content.parts[0].text)
            self.assertEqual(content.parts[-1].inline_data.data, b"changed-scene")

    def test_stopped_session_does_not_cache_observability(self) -> None:
        session = self.make_observability_session()
        session.live_request_queue = MagicMock()
        session.status = "stopped"
        self.assertFalse(session.send_agent_requested_observability())
        self.assertIsNone(session.last_canvas_state_sent)
        session.live_request_queue.send_content.assert_not_called()
        session.status = "active"
        self.assertTrue(session.send_agent_requested_observability())

    def test_canvas_observability_respects_startup_delay_and_interval(self) -> None:
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="test_observability_timing",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            config={
                "live_agent": {
                    "observability_startup_delay": 10,
                    "observability_interval": 30,
                }
            },
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        session.canvas_state_manager = canvas_observability_fixture()

        session.observability_available_at = 110.0
        with patch("services.live_agent_manager.time.monotonic", return_value=100.0):
            self.assertFalse(session.send_canvas_state())

        with patch("services.live_agent_manager.time.monotonic", return_value=110.0):
            self.assertTrue(session.send_canvas_state())
        self.assertEqual(session.live_request_queue.send_content.call_count, 1)

        with patch("services.live_agent_manager.time.monotonic", return_value=139.0):
            self.assertFalse(session.send_canvas_state())

        session.canvas_state_manager.visual.shown_image_prompt = "A new scene"
        with patch("services.live_agent_manager.time.monotonic", return_value=140.0):
            self.assertTrue(session.send_canvas_state())
        self.assertEqual(session.live_request_queue.send_content.call_count, 2)

    def test_collaboration_toggle_observability_is_cooled_down_and_defers_periodic_update(self) -> None:
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="test_collaboration_observability",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            config={
                "live_agent": {
                    "observability_interval": 30,
                    "collaboration_observability_cooldown": 5,
                }
            },
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        session.canvas_state_manager = canvas_observability_fixture()
        session.observability_available_at = 0.0

        with patch("services.live_agent_manager.time.monotonic", return_value=100.0):
            self.assertTrue(session.send_collaboration_toggle_observability())
        with patch("services.live_agent_manager.time.monotonic", return_value=102.0):
            self.assertFalse(session.send_collaboration_toggle_observability())
        with patch("services.live_agent_manager.time.monotonic", return_value=129.0):
            self.assertFalse(session.send_canvas_state())
        session.canvas_state_manager.visual.shown_image_prompt = "A new scene"
        with patch("services.live_agent_manager.time.monotonic", return_value=130.0):
            self.assertTrue(session.send_canvas_state())

        self.assertEqual(session.live_request_queue.send_content.call_count, 2)

    def test_agent_requested_observability_defers_the_next_regular_pulse(self) -> None:
        mock_theater = MagicMock(theater_id="test-theater")
        mock_theater.config = MagicMock(return_value={"cooldown_duration": 0})
        observability_tools = ObservabilityTools(mock_theater, MagicMock())
        mock_agent = MagicMock()
        mock_agent.tools = [SimpleNamespace(
            name="request_canvas_observability",
            func=observability_tools.request_canvas_observability,
        )]
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="test_agent_requested_observability",
            runner=mock_runner,
            tool_bundle=MagicMock(),
            config={"live_agent": {"observability_interval": 30}},
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image_file:
            image_file.write(b"canvas-image")
            image_path = image_file.name
        try:
            session.canvas_state_manager = canvas_observability_fixture(image_path=image_path)
            session.observability_available_at = 0.0

            with patch("services.live_agent_manager.time.monotonic", return_value=100.0):
                self.assertIn("Current canvas state sent", observability_tools.request_canvas_observability())
            with patch("services.live_agent_manager.time.monotonic", return_value=129.0):
                self.assertFalse(session.send_canvas_state())
            session.canvas_state_manager.visual.shown_image_prompt = "A new scene"
            with patch("services.live_agent_manager.time.monotonic", return_value=130.0):
                self.assertTrue(session.send_canvas_state())

            first_content = session.live_request_queue.send_content.call_args_list[0].args[0]
            self.assertEqual(first_content.parts[1].inline_data.mime_type, "image/png")
            self.assertEqual(first_content.parts[1].inline_data.data, b"canvas-image")
            self.assertEqual(session.live_request_queue.send_content.call_count, 2)
        finally:
            os.remove(image_path)

    def test_agent_requested_observability_forced_sends_even_when_unchanged(self) -> None:
        mock_runner = MagicMock()
        mock_runner.agent = MagicMock(tools=[])
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="test_forced_observability",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )
        session.live_request_queue = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image_file:
            image_file.write(b"forced-canvas-image")
            image_path = image_file.name
        try:
            session.canvas_state_manager = canvas_observability_fixture(image_path=image_path)
            session.websockets.add(MagicMock())
            # First send (normal)
            self.assertTrue(session.send_agent_requested_observability())
            self.assertEqual(session.live_request_queue.send_content.call_count, 1)

            # Second send (normal, unchanged -> returns False)
            self.assertFalse(session.send_agent_requested_observability())
            self.assertEqual(session.live_request_queue.send_content.call_count, 1)

            # Third send (forced -> sends even though unchanged)
            self.assertTrue(session.send_agent_requested_observability(force=True))
            self.assertEqual(session.live_request_queue.send_content.call_count, 2)
        finally:
            os.remove(image_path)

    def test_agent_requested_observability_attaches_doodles_with_state(self) -> None:
        """The live model must receive the annotation before it can respond."""
        mock_runner = MagicMock()
        mock_runner.agent = MagicMock(tools=[])
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="test_observability_doodles",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        canvas = canvas_observability_fixture(
            collaboration_enabled=True, doodles=[{"type": "draw"}],
        )
        canvas.doodles.snapshot_png.return_value = b"annotated-png"
        session.canvas_state_manager = canvas

        self.assertTrue(session.send_agent_requested_observability())

        content = session.live_request_queue.send_content.call_args.args[0]
        self.assertEqual(len(content.parts), 3)
        self.assertIn("audience annotations", content.parts[1].text)
        self.assertEqual(content.parts[2].inline_data.mime_type, "image/png")
        self.assertEqual(content.parts[2].inline_data.data, b"annotated-png")
        canvas.doodles.snapshot_png.assert_called_once_with(None)

    def test_agent_requested_observability_attaches_text_only_annotation(self):
        """Text without any stroke must still select the annotated composite."""
        mock_runner = MagicMock()
        mock_runner.agent = MagicMock(tools=[])
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="test_observability_text_annotation",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )
        session.live_request_queue = MagicMock()
        session.websockets.add(MagicMock())
        canvas = canvas_observability_fixture(
            collaboration_enabled=True,
            doodles=[{"type": "text", "text": "Secret door", "x": 0.2, "y": 0.3}],
        )
        canvas.doodles.snapshot_batches.return_value = []
        canvas.doodles.snapshot_png.return_value = b"text-annotated-png"
        session.canvas_state_manager = canvas

        self.assertTrue(session.send_agent_requested_observability())

        content = session.live_request_queue.send_content.call_args.args[0]
        self.assertEqual(len(content.parts), 3)
        self.assertIn("audience annotations", content.parts[1].text)
        self.assertEqual(content.parts[2].inline_data.data, b"text-annotated-png")
        canvas.doodles.has_visible_annotations.assert_called()
        canvas.doodles.snapshot_png.assert_called_once_with(None)

    def test_doodle_snapshot_is_rendered_off_the_event_loop(self):
        async def run_test():
            mock_agent = MagicMock()
            mock_agent.tools = []
            mock_runner = MagicMock()
            mock_runner.agent = mock_agent
            mock_runner.session_service = MagicMock()
            session = LiveAgentSession(
                theater_id="test_async_doodle_snapshot",
                runner=mock_runner,
                tool_bundle=MagicMock(),
                config={},
            )
            session.live_request_queue = MagicMock()
            session.websockets.add(MagicMock())
            canvas = canvas_observability_fixture(
                collaboration_enabled=True, doodles=[{"type": "draw"}],
            )
            canvas.doodles.snapshot_png.return_value = b"fake-png"
            session.canvas_state_manager = canvas
            session._event_loop = asyncio.get_running_loop()

            self.assertTrue(session.send_canvas_state())
            # The regular observability text is sent immediately; the image is
            # produced in a worker and delivered separately.
            self.assertEqual(session.live_request_queue.send_content.call_count, 1)
            await asyncio.sleep(0.05)
            self.assertEqual(canvas.doodles.snapshot_png.call_count, 1)
            self.assertEqual(session.live_request_queue.send_content.call_count, 2)

        asyncio.run(run_test())

    def test_usage_tracking_and_db_flushing(self) -> None:
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_db = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        mock_db.get_deployment.return_value = {"user_id": 123}
        mock_db.record_user_usage.return_value = {"credits": 1.0}

        session = LiveAgentSession(
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

        # Input audio remains diagnostic usage and never triggers a debit.
        session.record_audio_input(96000)
        self.assertAlmostEqual(session.voice_minutes, 96000 / 1920000.0)
        session.flush_usage_to_db()
        mock_db.record_user_usage.assert_not_called()

        session.record_live_agent_tool_calls(3)
        self.assertEqual(session.get_usage()["live_agent_tool_calls"], 3)
        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 123)
        self.assertEqual(kwargs["voice_minutes"], 0.0)
        self.assertEqual(kwargs["live_agent_tool_calls"], 3)
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

    def test_completed_image_flushes_usage_after_agent_failure(self):
        class ImageTools:
            def __init__(self):
                self.reference_manager: ReferenceManager | None = None
                self.on_image_created = None

            def create_image(self, prompt):
                return prompt

        class FailingRunner:
            app_name = "test-app"

            def __init__(self, image_tools):
                self.agent = MagicMock(
                    tools=[SimpleNamespace(name="create_image", func=image_tools.create_image)]
                )
                self.session_service = MagicMock()
                self.session_service.get_session = AsyncMock(return_value=object())

            async def run_live(self, **_kwargs):
                if False:
                    yield None
                raise RuntimeError("agent died")

        async def run_test():
            image_tools = ImageTools()
            mock_db = MagicMock()
            mock_db.get_deployment.return_value = {"user_id": 123}
            mock_db.record_user_usage.return_value = {"credits": 1.0}
            session = LiveAgentSession(
                theater_id="late_image_usage",
                runner=FailingRunner(image_tools),
                tool_bundle=MagicMock(),
                database_manager=mock_db,
            )

            await session._run_downstream()
            self.assertEqual(session.status, "stopped")

            # Image generation runs in a separate thread. It may finish after
            # the live agent has failed and closed its request queue.
            image_tools.on_image_created("path/to/late-image.jpg")

            self.assertEqual(session.images_created_count, 1)
            kwargs = mock_db.record_user_usage.call_args.kwargs
            self.assertEqual(kwargs["user_id"], 123)
            self.assertEqual(kwargs["images_created"], 1)
            self.assertTrue(
                kwargs["idempotency_key"].startswith("live-usage:late_image_usage:")
            )

        asyncio.run(run_test())

    def test_character_voicing_adds_usage_for_each_completed_planner_turn(self):
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_runner = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = MagicMock()
        mock_db = MagicMock()
        mock_db.get_deployment.return_value = {"user_id": 123}
        mock_db.record_user_usage.return_value = {"credits": 5.0}
        session = LiveAgentSession(
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
        session = LiveAgentSession(
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

        session = LiveAgentSession(
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

    def test_live_tool_reminder_only_sends_during_post_vad_tool_window(self):
        mock_runner = MagicMock()
        mock_runner.agent = MagicMock()
        mock_runner.session_service = MagicMock()
        session = LiveAgentSession(
            theater_id="test_tool_reminder",
            runner=mock_runner,
            tool_bundle=None,
        )
        session.live_request_queue = MagicMock()
        session.live_request_queue.live_tool_window_active = True
        mock_ws = MagicMock()

        asyncio.run(session.add_websocket(mock_ws))
        session.live_request_queue.send_content.reset_mock()

        self.assertTrue(session.send_live_tool_reminder())
        content = session.live_request_queue.send_content.call_args.args[0]
        self.assertIn("tool-call budget", content.parts[0].text)

        session.live_request_queue.live_tool_window_active = False
        session.live_request_queue.send_content.reset_mock()
        self.assertFalse(session.send_live_tool_reminder())
        session.live_request_queue.send_content.assert_not_called()

    def test_enable_tool_injection_flag_default(self):
        async def run_test():
            default_runner = MagicMock()
            default_runner.agent = MagicMock()
            default_runner.session_service = MagicMock()
            session_default = LiveAgentSession(
                theater_id="test_flag_default",
                runner=default_runner,
                tool_bundle=MagicMock(),
            )
            self.assertFalse(session_default.enable_tool_injection)
            session_default.start_background_tasks()
            self.assertIsNone(session_default.tool_injection_task)
            self.assertIsNotNone(session_default.live_tool_reminder_task)
            if session_default.downstream_task:
                session_default.downstream_task.cancel()
            if session_default.refresh_task:
                session_default.refresh_task.cancel()
            if session_default.live_tool_reminder_task:
                session_default.live_tool_reminder_task.cancel()

            enabled_runner = MagicMock()
            enabled_runner.agent = MagicMock()
            enabled_runner.session_service = MagicMock()
            session_enabled = LiveAgentSession(
                theater_id="test_flag_enabled",
                runner=enabled_runner,
                tool_bundle=MagicMock(),
                config={"live_agent": {"enable_tool_injection": True}},
            )

            self.assertTrue(session_enabled.enable_tool_injection)
            session_enabled.start_background_tasks()
            self.assertIsNotNone(session_enabled.tool_injection_task)
            self.assertIsNotNone(session_enabled.live_tool_reminder_task)
            if session_enabled.downstream_task:
                session_enabled.downstream_task.cancel()
            if session_enabled.refresh_task:
                session_enabled.refresh_task.cancel()
            if session_enabled.tool_injection_task:
                session_enabled.tool_injection_task.cancel()
            if session_enabled.live_tool_reminder_task:
                session_enabled.live_tool_reminder_task.cancel()



    def test_remove_websocket_saves_named_elements_to_session_state(self):
        async def run_test():
            session = LiveAgentSession.__new__(LiveAgentSession)
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
            session = LiveAgentSession.__new__(LiveAgentSession)
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
        session = LiveAgentSession.__new__(LiveAgentSession)
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

            with patch("services.live_agent_manager.create_tool_bundle_for_session"), \
                 patch("services.live_agent_manager.create_agent", return_value=mock_agent), \
                 patch("services.live_agent_manager.Runner", return_value=mock_runner):

                from services.live_agent_manager import LiveAgentSessionManager, TheaterManager
                manager = LiveAgentSessionManager(
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

        with patch("services.live_agent_manager.get_bound_tool_instance") as mock_get_tool:
            def side_effect(agent, tool_name):
                if tool_name == "create_animation":
                    return mock_animation_tools
                return None
            mock_get_tool.side_effect = side_effect

            session = LiveAgentSession(
                theater_id="test_anim_notif",
                runner=MagicMock(agent=mock_agent, session_service=MagicMock()),
                tool_bundle=MagicMock(),
            )
            session.send_notification = MagicMock()

            # Verify on_animation_ready callback was registered
            self.assertIsNotNone(mock_animation_tools.on_animation_ready)
            self.assertIsNotNone(mock_animation_tools.on_animation_created)

            # Trigger callback
            mock_animation_tools.on_animation_ready("anim_123", "layered")

            # Check that notification was sent
            session.send_notification.assert_called_once()
            content_arg = session.send_notification.call_args.args[0]
            self.assertIn("anim_123", content_arg.parts[0].text)
            self.assertIn("layered", content_arg.parts[0].text)
            self.assertIn("ready to play", content_arg.parts[0].text)

    def test_session_binds_notepad_and_reference_tools_and_passes_to_format_canvas_state(self) -> None:
        mock_agent = MagicMock()
        mock_agent.tools = []
        mock_notepad_tools = MagicMock()
        mock_reference_tools = MagicMock()
        mock_image_tools = MagicMock(spec=ImageTools)
        mock_manager = MagicMock(spec=ReferenceManager)
        mock_image_tools.reference_manager = mock_manager

        with patch("services.live_agent_manager.get_bound_tool_instance") as mock_get_tool:
            def side_effect(agent: MagicMock, tool_name: str) -> MagicMock | None:
                if tool_name == "create_image":
                    return mock_image_tools
                if tool_name == "update_sticky_note":
                    return mock_notepad_tools
                if tool_name in ("create_or_update_character", "create_character", "update_character"):
                    return mock_reference_tools
                return None

            mock_get_tool.side_effect = side_effect

            session = LiveAgentSession(
                theater_id="test_notepad_char_tools",
                runner=MagicMock(agent=mock_agent, session_service=MagicMock()),
                tool_bundle=MagicMock(),
                config={"live_agent": {"observability_startup_delay": 0}},
            )
            self.assertIs(session.notepad_tools, mock_notepad_tools)
            self.assertIs(session.reference_tools, mock_reference_tools)
            self.assertIs(session.character_tools, mock_reference_tools)
            self.assertIs(session.reference_manager, mock_manager)

            session.live_request_queue = MagicMock()
            session.websockets.add(MagicMock())
            session.canvas_state_manager = canvas_observability_fixture()
            session.observability_available_at = 0.0

            with patch("services.live_agent_manager.format_canvas_state") as mock_format_canvas_state:
                mock_format_canvas_state.return_value = "Formatted Canvas State"
                with patch("services.live_agent_manager.time.monotonic", return_value=10.0):
                    self.assertTrue(session.send_canvas_state())

                mock_format_canvas_state.assert_called_once_with(
                    session.canvas_state_manager,
                    mock_notepad_tools,
                    mock_reference_tools,
                    mock_manager,
                )


if __name__ == "__main__":
    unittest.main()
