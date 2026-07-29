import asyncio
import base64
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Any, Set

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from agent import create_agent
from services.disk_artifact_service import DiskArtifactService
from services.live_stream_service import (
    CANVAS_STATE_REFRESH_SECONDS,
    format_canvas_state,
    get_bound_tool_instance,
)
from services.preloaded_in_memory_artifact_service import PreloadedInMemoryArtifactService
from utils.session_paths import ensure_sessions_root

logger = logging.getLogger(__name__)


def build_run_config(
    agent: Any = None,
    config: Optional[dict] = None,
    proactivity: Optional[bool] = None,
    affective_dialog: Optional[bool] = None,
    model_name: Optional[str] = None,
) -> RunConfig:
    """Construct RunConfig for ADK streaming execution using parameters from config.yaml."""
    config = config or {}
    agent_config = config.get("agent", {})

    if proactivity is None:
        proactivity = agent_config.get("proactivity", False)
    if affective_dialog is None:
        affective_dialog = agent_config.get("affective_dialog", False)

    if model_name is None and agent is not None:
        model_name = getattr(agent, "model", "")
    model_name = model_name or agent_config.get("model_id") or agent_config.get("model", "gemini-3.1-flash-live-preview")

    is_native_audio = any(
        token in model_name.lower()
        for token in ["native-audio", "1.5-flash", "2.0-flash-exp", "3.1-flash", "live"]
    )

    compaction = agent_config.get("compaction", {})
    compaction_config = None
    if compaction:
        trigger = compaction.get("trigger_tokens")
        target = compaction.get("target_tokens")
        compaction_config = types.ContextWindowCompressionConfig(
            trigger_tokens=int(trigger) if trigger is not None else None,
            sliding_window=types.SlidingWindow(target_tokens=int(target)) if target is not None else None,
        )

    if is_native_audio:
        response_modalities = ["AUDIO"]
        return RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=response_modalities,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=None,
            context_window_compression=compaction_config,
            proactivity=(
                types.ProactivityConfig(proactive_audio=True) if proactivity else None
            ),
            enable_affective_dialog=affective_dialog if affective_dialog else None,
            realtime_input_config=types.RealtimeInputConfig(
                activity_handling=types.ActivityHandling.NO_INTERRUPTION
            ),
            tool_thread_pool_config=ToolThreadPoolConfig(
                max_workers=agent_config.get("max_tool_workers", 3)
            ),
            get_session_config=GetSessionConfig(num_recent_events=0),
        )
    else:
        response_modalities = ["TEXT"]
        return RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=response_modalities,
            input_audio_transcription=None,
            output_audio_transcription=None,
            context_window_compression=compaction_config,
            get_session_config=GetSessionConfig(num_recent_events=0),
        )


class AgentSession:
    def __init__(
        self,
        narratron_session_id: str,
        agent: Any,
        runner: Runner,
        session_service: InMemorySessionService,
        artifact_service: Any,
        run_config: Optional[RunConfig] = None,
        config: Optional[dict] = None,
        canvas_state_manager: Optional[Any] = None,
        adk_user_id: Optional[str] = None,
        adk_session_id: Optional[str] = None,
    ):
        import uuid
        self.narratron_session_id = narratron_session_id
        self.adk_session_id = adk_session_id or f"adk_{narratron_session_id}_{uuid.uuid4().hex[:8]}"
        self.adk_user_id = adk_user_id or f"orator_{narratron_session_id}"
        self.agent = agent
        self.runner = runner
        self.session_service = session_service
        self.artifact_service = artifact_service
        self.config = config or {}
        self.canvas_state_manager = canvas_state_manager
        self.created_at = time.time()
        self.last_active_at = time.time()
        self.status = "ready"  # "ready", "active", "stopped"

        self.live_request_queue = LiveRequestQueue()
        self.websockets: Set[WebSocket] = set()
        self.ws_lock = asyncio.Lock()

        # Retrieve bound tool instances safely
        self.image_tools = get_bound_tool_instance(agent, "create_image")
        self.chat_tools = get_bound_tool_instance(agent, "send_chat_message")
        self.notes_tools = get_bound_tool_instance(agent, "edit_notes")
        self.music_tools = get_bound_tool_instance(agent, "play_playlist")

        if self.image_tools and hasattr(self.image_tools, "active_session_id"):
            self.image_tools.active_narratron_session_id = narratron_session_id
        if self.notes_tools and hasattr(self.notes_tools, "active_session_id"):
            self.notes_tools.active_narratron_session_id = narratron_session_id

        self.run_config = run_config or build_run_config(
            agent=agent,
            config=self.config,
        )

        self._setup_tool_callbacks()

        self.downstream_task: Optional[asyncio.Task] = None
        self.refresh_task: Optional[asyncio.Task] = None
        self.last_canvas_state_sent = time.monotonic()
        self.state_lock = threading.Lock()

    @property
    def websocket_connected(self) -> bool:
        return len(self.websockets) > 0

    def _setup_tool_callbacks(self):
        def handle_cooldown_expired(tool_name: str):
            msg = f"[System Notification] The cooldown for '{tool_name}' has expired. You may now call {tool_name} again."
            logger.info(f"[AgentSession] Cooldown expired notification: {msg}")
            try:
                content = types.Content(parts=[types.Part(text=msg)])
                self.live_request_queue.send_content(content)
            except Exception as e:
                logger.error(f"[AgentSession] Failed to send cooldown expired notification: {e}")

        def handle_after_image_tool(_tool_name: str, _canvas_info: dict):
            self.send_canvas_state(force=True)

        for tool_suite in (self.image_tools, self.chat_tools, self.notes_tools, self.music_tools):
            if tool_suite and hasattr(tool_suite, "on_cooldown_expired"):
                tool_suite.on_cooldown_expired = handle_cooldown_expired

        if self.image_tools:
            self.image_tools.on_after_tool_call = handle_after_image_tool

        def handle_session_chat_message(text: str):
            async def send_to_ws():
                custom_event = {
                    "author": "agent",
                    "content": {"parts": [{"text": text}]},
                }
                await self.broadcast_text(json.dumps(custom_event))

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(send_to_ws())
            except RuntimeError:
                pass

        if self.chat_tools:
            self.chat_tools.on_send_chat_message = handle_session_chat_message

    def send_canvas_state(self, *, force: bool = False) -> bool:
        """Inject current canvas image/music state into LiveRequestQueue."""
        now = time.monotonic()
        with self.state_lock:
            if not force and now - self.last_canvas_state_sent < CANVAS_STATE_REFRESH_SECONDS:
                return False
            msg = format_canvas_state(self.canvas_state_manager)
            try:
                self.live_request_queue.send_content(types.Content(parts=[types.Part(text=msg)]))
            except Exception as e:
                logger.error(f"[AgentSession] Failed to send canvas observability update: {e}", exc_info=True)
                return False
            self.last_canvas_state_sent = now
        logger.info("[AgentSession] Canvas state update: %s", msg.replace("\n", " | "))
        return True

    def start_background_tasks(self):
        """Start long-running downstream_task (runner.run_live) and canvas refresh loop."""
        if self.downstream_task is None or self.downstream_task.done():
            self.downstream_task = asyncio.create_task(self._run_downstream())

        if self.refresh_task is None or self.refresh_task.done():
            self.refresh_task = asyncio.create_task(self._run_canvas_refresh())

        self.send_canvas_state(force=True)

    async def _run_downstream(self):
        """Task that runs runner.run_live() continuously and broadcasts model events to attached WebSockets."""
        logger.info(f"[AgentSession] Starting downstream_task (runner.run_live) for narratron_session_id={self.narratron_session_id}")
        try:
            if await self.session_service.get_session(
                app_name=self.runner.app_name,
                user_id=self.adk_user_id,
                session_id=self.adk_session_id,
            ) is None:
                await self.session_service.create_session(
                    app_name=self.runner.app_name,
                    user_id=self.adk_user_id,
                    session_id=self.adk_session_id,
                )
                logger.info(f"[AgentSession] Created ADK session {self.adk_session_id} for user {self.adk_user_id}")

            async for event in self.runner.run_live(
                user_id=self.adk_user_id,
                session_id=self.adk_session_id,
                live_request_queue=self.live_request_queue,
                run_config=self.run_config,
            ):
                if hasattr(event, "get_function_calls") and event.get_function_calls():
                    for call in event.get_function_calls():
                        logger.info(f"[Agent Tool Call] Function: {call.name}, Args: {call.args}")

                event_dict = json.loads(event.model_dump_json(exclude_none=True, by_alias=True))
                if "content" in event_dict and "parts" in event_dict["content"]:
                    event_dict["content"]["parts"] = [
                        part for part in event_dict["content"]["parts"]
                        if "inlineData" not in part
                    ]

                event_json = json.dumps(event_dict)
                await self.broadcast_text(event_json)
        except asyncio.CancelledError:
            logger.debug(f"[AgentSession] downstream_task cancelled for narratron_session_id={self.narratron_session_id}")
        except Exception as e:
            logger.error(f"[AgentSession] Exception in downstream_task for narratron_session_id={self.narratron_session_id}: {e}", exc_info=True)

    async def _run_canvas_refresh(self):
        try:
            while True:
                await asyncio.sleep(1.0)
                if self.websocket_connected and self.canvas_state_manager:
                    self.canvas_state_manager.set_tool_activity("live", active=True, recent_seconds=10.0)
                self.send_canvas_state()
        except asyncio.CancelledError:
            return

    async def add_websocket(self, websocket: WebSocket):
        async with self.ws_lock:
            self.websockets.add(websocket)
            self.status = "active"
            self.last_active_at = time.time()
            logger.info(f"[AgentSession] WebSocket attached to session {self.narratron_session_id} (total={len(self.websockets)})")

    async def remove_websocket(self, websocket: WebSocket):
        async with self.ws_lock:
            self.websockets.discard(websocket)
            self.last_active_at = time.time()
            if len(self.websockets) == 0:
                self.status = "ready"
            logger.info(f"[AgentSession] WebSocket detached from session {self.narratron_session_id} (remaining={len(self.websockets)})")

    async def broadcast_text(self, text: str):
        async with self.ws_lock:
            for ws in list(self.websockets):
                try:
                    if hasattr(ws, "client_state") and ws.client_state.name != "CONNECTED":
                        continue
                    await ws.send_text(text)
                except (WebSocketDisconnect, RuntimeError, ConnectionResetError) as err:
                    logger.debug(f"[AgentSession] broadcast_text skipped (closed): {err}")
                    self.websockets.discard(ws)

    def close(self):
        """Close LiveRequestQueue and cancel background tasks."""
        self.status = "stopped"
        if self.downstream_task and not self.downstream_task.done():
            self.downstream_task.cancel()
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
        try:
            self.live_request_queue.close()
        except Exception as e:
            logger.debug(f"[AgentSession] Error closing live_request_queue: {e}")


class AgentSessionManager:
    def __init__(self, app_name: str = "narratron-combined", config: Optional[dict] = None):
        self.app_name = app_name
        self.config = config or {}
        self._sessions: Dict[str, AgentSession] = {}
        self.shared_session_service = InMemorySessionService()

        # Construct run_config internally from configuration
        self.run_config = build_run_config(config=self.config)

    def get_session(self, narratron_session_id: str) -> Optional[AgentSession]:
        """Retrieve an active agent session by narratron_session_id if present."""
        return self._sessions.get(narratron_session_id)

    def get_or_create_session(
        self,
        narratron_session_id: str,
        canvas_state_service: Optional[Any] = None,
        use_in_memory_artifacts: bool = False,
    ) -> AgentSession:
        """Fetch an existing active session or instantiate a new AgentSession."""
        existing = self.get_session(narratron_session_id)
        if existing and existing.status != "stopped":
            existing.last_active_at = time.time()
            return existing

        logger.info(f"[AgentSessionManager] Creating new AgentSession for narratron_session_id={narratron_session_id}")

        canvas_mgr = canvas_state_service.get(narratron_session_id) if canvas_state_service and hasattr(canvas_state_service, "get") else None

        session_agent = create_agent(
            narratron_session_id=narratron_session_id,
            config=self.config,
            canvas_state_service=canvas_state_service,
        )

        disk_service_path = ensure_sessions_root() / narratron_session_id / "output" / "artifacts"
        if use_in_memory_artifacts:
            artifact_service = PreloadedInMemoryArtifactService()
            test_data_dir = Path(__file__).parent.parent / "testing" / "testdata"
            if test_data_dir.exists():
                artifact_service.preload_directory(test_data_dir, app_name=self.app_name)
        else:
            artifact_service = DiskArtifactService(disk_service_path)

        runner = Runner(
            app_name=self.app_name,
            agent=session_agent,
            session_service=self.shared_session_service,
            artifact_service=artifact_service,
        )

        agent_session = AgentSession(
            narratron_session_id=narratron_session_id,
            agent=session_agent,
            runner=runner,
            session_service=self.shared_session_service,
            artifact_service=artifact_service,
            run_config=self.run_config,
            config=self.config,
            canvas_state_manager=canvas_mgr,
        )

        agent_session.start_background_tasks()
        self._sessions[narratron_session_id] = agent_session
        return agent_session

    def stop_session(self, narratron_session_id: str) -> bool:
        """Stop and remove an agent session from memory."""
        session = self._sessions.get(narratron_session_id)
        if not session:
            return False

        logger.info(f"[AgentSessionManager] Stopping agent narratron_session_id={narratron_session_id}")
        session.close()
        del self._sessions[narratron_session_id]
        return True

    def cleanup_idle_sessions(self, ttl_seconds: float = 300.0) -> list:
        """Purge sessions that are disconnected from WebSocket and idle for longer than ttl_seconds."""
        now = time.time()
        expired_ids = []
        for sid, session in list(self._sessions.items()):
            if not session.websocket_connected and (now - session.last_active_at) > ttl_seconds:
                expired_ids.append(sid)

        for sid in expired_ids:
            logger.info(f"[AgentSessionManager] Auto-cleaning idle narratron_session_id={sid}")
            self.stop_session(sid)

        return expired_ids
