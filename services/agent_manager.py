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

from services.disk_artifact_service import DiskArtifactService
from services.live_stream_service import (
    CANVAS_STATE_REFRESH_SECONDS,
    format_canvas_state,
    get_bound_tool_instance,
)
from services.preloaded_in_memory_artifact_service import PreloadedInMemoryArtifactService
from services.priority_live_request_queue import PriorityLiveRequestQueue
from utils.config_loader import get_app_config, get_theater_config
from utils.theaters_paths import ensure_theaters_root

logger = logging.getLogger(__name__)

TOOL_INJECTION_INTERVAL_SECONDS = 30.0




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
    app_internal = get_app_config().get("agent_internal", {})

    if proactivity is None:
        proactivity = agent_config.get("proactivity", False)
    if affective_dialog is None:
        affective_dialog = agent_config.get("affective_dialog", False)

    if model_name is None and agent is not None:
        model_name = getattr(agent, "model", "")
    model_name = (
        model_name
        or app_internal.get("model_id")
        or app_internal.get("model", "gemini-3.1-flash-live-preview")
    )

    is_native_audio = any(
        token in model_name.lower()
        for token in ["native-audio", "1.5-flash", "2.0-flash-exp", "3.1-flash", "live"]
    )

    compaction = app_internal.get("compaction", {})
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
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=True
                ),
                activity_handling=types.ActivityHandling.NO_INTERRUPTION,
            ),
            tool_thread_pool_config=ToolThreadPoolConfig(
                max_workers=agent_config.get("max_tool_workers", 3)
            ),
            get_session_config=GetSessionConfig(num_recent_events=0),
            session_resumption=types.SessionResumptionConfig()
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
        theater_id: str,
        agent: Any,
        runner: Runner,
        session_service: InMemorySessionService,
        artifact_service: Any,
        run_config: Optional[RunConfig] = None,
        config: Optional[dict] = None,
        canvas_state_manager: Optional[Any] = None,
        adk_user_id: Optional[str] = None,
        adk_session_id: Optional[str] = None,
        db: Optional[Any] = None,
        owner_user_id: Optional[int] = None,
        tool_bundle: Optional[Any] = None,
    ):
        import uuid
        self.theater_id = theater_id
        self.adk_session_id = adk_session_id or f"adk_{theater_id}_{uuid.uuid4().hex[:8]}"
        self.adk_user_id = adk_user_id or f"orator_{theater_id}"
        self.agent = agent
        self.tool_bundle = tool_bundle or getattr(agent, "tool_bundle", None)
        self.runner = runner
        self.session_service = session_service
        self.artifact_service = artifact_service
        self.config = config or {}
        self.canvas_state_manager = canvas_state_manager
        self.db = db
        self.owner_user_id = owner_user_id
        agent_internal = self.config.get("agent_internal", {})
        self.enable_tool_injection = bool(agent_internal.get("enable_tool_injection", False))




        self.images_created_count: int = 0
        self.audio_bytes_received: int = 0
        self.unbilled_images: int = 0
        self.unbilled_audio_bytes: int = 0
        self.created_at = time.time()
        self.last_active_at = time.time()
        self.status = "ready"  # "ready", "active", "stopped"


        self.live_request_queue = PriorityLiveRequestQueue(retention_window=0.5)
        self.websockets: Set[WebSocket] = set()
        self.ws_lock = asyncio.Lock()

        # Retrieve bound tool instances safely
        self.image_tools = get_bound_tool_instance(agent, "create_image")
        self.chat_tools = get_bound_tool_instance(agent, "send_chat_message")
        self.notes_tools = get_bound_tool_instance(agent, "edit_notes")
        self.music_tools = get_bound_tool_instance(agent, "play_playlist")

        self.run_config = run_config or build_run_config(
            agent=agent,
            config=self.config,
        )

        self._setup_tool_callbacks()

        self.downstream_task: Optional[asyncio.Task] = None
        self.refresh_task: Optional[asyncio.Task] = None
        self.tool_injection_task: Optional[asyncio.Task] = None
        self.last_canvas_state_sent = time.monotonic()
        self.state_lock = threading.Lock()

    @property
    def websocket_connected(self) -> bool:
        return len(self.websockets) > 0

    def record_input_detected(self) -> None:
        """Mark that orator input has been detected to hold priority window."""
        if hasattr(self.live_request_queue, "record_input_detected"):
            self.live_request_queue.record_input_detected()

    def send_content(self, content: types.Content) -> bool:
        """Send content to live_request_queue if user is connected; suppress otherwise."""
        if not self.websocket_connected:
            logger.debug(f"[AgentSession] User disconnected; suppressing content input for session {self.theater_id}.")
            return False
        self.live_request_queue.send_content(content)
        return True

    def send_realtime(self, blob: types.Blob) -> bool:
        """Send realtime audio/image blob to live_request_queue if user is connected; suppress otherwise."""
        if not self.websocket_connected:
            logger.debug(f"[AgentSession] User disconnected; suppressing realtime input for session {self.theater_id}.")
            return False
        self.live_request_queue.send_realtime(blob)
        return True

    def send_activity_start(self) -> bool:
        """Send activity_start to live_request_queue if user is connected."""
        if not self.websocket_connected:
            return False
        if hasattr(self.live_request_queue, "send_activity_start"):
            self.live_request_queue.send_activity_start()
        return True

    def send_activity_end(self) -> bool:
        """Send activity_end to live_request_queue if user is connected."""
        if not self.websocket_connected:
            return False
        if hasattr(self.live_request_queue, "send_activity_end"):
            self.live_request_queue.send_activity_end()
        return True

    def _setup_tool_callbacks(self):
        def handle_cooldown_expired(tool_name: str):
            msg = f"[System Notification] The cooldown for '{tool_name}' has expired. You may now call {tool_name} again."
            logger.info(f"[AgentSession] Cooldown expired notification: {msg}")
            try:
                content = types.Content(parts=[types.Part(text=msg)])
                self.send_content(content)
            except Exception as e:
                logger.error(f"[AgentSession] Failed to send cooldown expired notification: {e}")

        def handle_after_image_tool(_tool_name: str, _canvas_info: dict):
            self.send_canvas_state(force=True)

        for tool_suite in (self.image_tools, self.chat_tools, self.notes_tools, self.music_tools):
            if tool_suite and hasattr(tool_suite, "on_cooldown_expired"):
                tool_suite.on_cooldown_expired = handle_cooldown_expired

        if self.image_tools:
            self.image_tools.on_after_tool_call = handle_after_image_tool
            self.image_tools.on_image_created = self.record_image_created

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
        if not self.websocket_connected:
            logger.debug(f"[AgentSession] User disconnected; suppressing canvas state update for session {self.theater_id}.")
            return False
        now = time.monotonic()
        with self.state_lock:
            if not force and now - self.last_canvas_state_sent < CANVAS_STATE_REFRESH_SECONDS:
                return False
            msg = format_canvas_state(self.canvas_state_manager)
            try:
                self.send_content(types.Content(parts=[types.Part(text=msg)]))
            except Exception as e:
                logger.error(f"[AgentSession] Failed to send canvas observability update: {e}", exc_info=True)
                return False
            self.last_canvas_state_sent = now
        logger.info("[AgentSession] Canvas state update: %s", msg.replace("\n", " | "))
        return True

    def start_background_tasks(self):
        """Start long-running downstream_task (runner.run_live), canvas refresh loop, and tool injection loop."""
        if self.downstream_task is None or self.downstream_task.done():
            self.downstream_task = asyncio.create_task(self._run_downstream())

        if self.refresh_task is None or self.refresh_task.done():
            self.refresh_task = asyncio.create_task(self._run_canvas_refresh())

        if self.enable_tool_injection and (self.tool_injection_task is None or self.tool_injection_task.done()):
            self.tool_injection_task = asyncio.create_task(self._run_tool_injection_loop())

        self.send_canvas_state(force=True)


    async def _run_downstream(self):
        """Task that runs runner.run_live() continuously and broadcasts model events to attached WebSockets."""
        logger.info(f"[AgentSession] Starting downstream_task (runner.run_live) for theater_id={self.theater_id}")
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
            logger.debug(f"[AgentSession] downstream_task cancelled for theater_id={self.theater_id}")
        except Exception as e:
            logger.error(f"[AgentSession] Exception in downstream_task for theater_id={self.theater_id}: {e}", exc_info=True)

    async def _run_canvas_refresh(self):
        try:
            while True:
                await asyncio.sleep(1.0)
                if self.db and self.owner_user_id:
                    try:
                        owner = self.db.get_user_by_id(self.owner_user_id)
                        if owner and owner.get("credits", 0.0) <= 0.0:
                            logger.warning(f"[AgentSession] Owner user {self.owner_user_id} credit balance <= 0. Auto-stopping agent session for {self.theater_id}.")
                            await self.broadcast_text(json.dumps({
                                "type": "insufficient_credits",
                                "detail": "Agent stopped because your credit balance reached 0 or less.",
                                "credits": owner.get("credits", 0.0),
                            }))
                            self.close()
                            return
                    except Exception as err:
                        logger.debug(f"[AgentSession] Periodic credit check error: {err}")

                if self.websocket_connected and self.canvas_state_manager:
                    self.canvas_state_manager.set_tool_activity("live", active=True, recent_seconds=10.0)
                self.send_canvas_state()
        except asyncio.CancelledError:
            return

    def inject_tool_definitions(self) -> bool:
        """Formats and populates updated tool definitions into the session's live request queue."""
        if not self.tool_bundle:
            return False
        text = self.tool_bundle.format_descriptions()
        content = types.Content(parts=[types.Part(text=text)])
        return self.send_content(content)

    async def _run_tool_injection_loop(self):
        """Task that populates the live request queue with tool definitions on a 30s interval when enabled."""
        if not self.enable_tool_injection:
            return
        try:
            while True:
                await asyncio.sleep(TOOL_INJECTION_INTERVAL_SECONDS)
                if self.websocket_connected and self.tool_bundle:
                    logger.info(f"[AgentSession] Populating live request queue with tool definitions for session {self.theater_id}")
                    self.inject_tool_definitions()
        except asyncio.CancelledError:
            return


    async def add_websocket(self, websocket: WebSocket):
        async with self.ws_lock:
            was_disconnected = len(self.websockets) == 0
            self.websockets.add(websocket)
            self.status = "active"
            self.last_active_at = time.time()
            logger.info(f"[AgentSession] WebSocket attached to session {self.theater_id} (total={len(self.websockets)})")

        if was_disconnected:
            logger.info(f"[AgentSession] User reconnected for session {self.theater_id}; re-enabling state information.")
            self.send_canvas_state(force=True)

    @property
    def voice_minutes(self) -> float:
        """Calculate voice minutes from total raw PCM 16kHz audio input bytes (1,920,000 bytes/min)."""
        return self.audio_bytes_received / 1920000.0

    def record_image_created(self, image_path: str = ""):
        """Record image created for active theater session and flush usage."""
        self.images_created_count += 1
        self.unbilled_images += 1
        logger.info(f"[AgentSession] Image created recorded for theater {self.theater_id} (total={self.images_created_count})")
        self.flush_usage_to_db()

    def record_audio_input(self, byte_count: int):
        """Record incoming PCM audio input stream bytes as time counter proxy and flush usage periodically."""
        if byte_count <= 0:
            return
        self.audio_bytes_received += byte_count
        self.unbilled_audio_bytes += byte_count
        # Flush unbilled usage whenever unbilled audio reaches >= 96,000 bytes (~3 seconds of audio)
        if self.unbilled_audio_bytes >= 96000:
            self.flush_usage_to_db()

    def flush_usage_to_db(self):
        """Deduct credits and record cumulative voice minutes / images created in database."""
        if self.unbilled_audio_bytes <= 0 and self.unbilled_images <= 0:
            return
        unbilled_vm = self.unbilled_audio_bytes / 1920000.0
        unbilled_img = self.unbilled_images

        # Reset unbilled counters before DB call
        self.unbilled_audio_bytes = 0
        self.unbilled_images = 0

        db_inst = self.db
        if db_inst is None:
            try:
                from web_viewer_app import db as global_db
                db_inst = global_db
            except ImportError:
                db_inst = None

        owner_id = self.owner_user_id
        if owner_id is None and db_inst:
            try:
                deployment = db_inst.get_deployment(self.theater_id)
                if deployment:
                    owner_id = deployment.get("user_id")
                    self.owner_user_id = owner_id
            except Exception as e:
                logger.debug(f"[AgentSession] Could not fetch deployment owner: {e}")

        if db_inst and owner_id:
            try:
                updated_user = db_inst.record_user_usage(
                    user_id=owner_id,
                    voice_minutes=unbilled_vm,
                    images_created=unbilled_img,
                )
                logger.info(
                    f"[AgentSession] Flushed usage to DB for user {owner_id} (theater {self.theater_id}): voice_minutes={unbilled_vm:.4f}, images={unbilled_img}"
                )
                credits_remaining = updated_user.get("credits", 0.0) if updated_user else 1.0
                if credits_remaining <= 0.0:
                    logger.warning(
                        f"[AgentSession] Owner user {owner_id} credit balance reached <= 0 ({credits_remaining:.2f}). Gracefully stopping agent session for theater {self.theater_id}."
                    )
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.broadcast_text(json.dumps({
                            "type": "insufficient_credits",
                            "detail": "Agent stopped because your credit balance reached 0 or less.",
                            "credits": credits_remaining,
                        })))
                    except RuntimeError:
                        pass
                    self.close()
            except Exception as e:
                logger.error(f"[AgentSession] Error flushing usage to DB: {e}")

    def get_usage(self) -> Dict[str, Any]:
        """Return usage summary dictionary for the active session."""
        return {
            "theater_id": self.theater_id,
            "owner_user_id": self.owner_user_id,
            "voice_minutes": self.voice_minutes,
            "images_created": self.images_created_count,
            "total_audio_bytes": self.audio_bytes_received,
        }

    async def remove_websocket(self, websocket: WebSocket):
        async with self.ws_lock:
            self.websockets.discard(websocket)
            self.last_active_at = time.time()
            is_now_disconnected = len(self.websockets) == 0
            if is_now_disconnected:
                self.status = "ready"
            logger.info(f"[AgentSession] WebSocket detached from session {self.theater_id} (remaining={len(self.websockets)})")
            if is_now_disconnected:
                logger.info(f"[AgentSession] User disconnected for session {self.theater_id}; inputs are now suppressed.")
        self.flush_usage_to_db()

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
        self.flush_usage_to_db()
        if self.downstream_task and not self.downstream_task.done():
            self.downstream_task.cancel()
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
        if self.tool_injection_task and not self.tool_injection_task.done():
            self.tool_injection_task.cancel()
        try:
            self.live_request_queue.close()
        except Exception as e:
            logger.debug(f"[AgentSession] Error closing live_request_queue: {e}")

from services.agent import create_agent, create_tool_bundle_for_session


class AgentSessionManager:
    def __init__(self, app_name: str = "narratron-combined", config: Optional[dict] = None):
        self.app_name = app_name
        self.config = config or {}
        self._sessions: Dict[str, AgentSession] = {}
        self.shared_session_service = InMemorySessionService()

        # Construct run_config internally from configuration
        self.run_config = build_run_config(config=self.config)

    def get_session(self, theater_id: str) -> Optional[AgentSession]:
        """Retrieve an active agent session by theater_id if present."""
        return self._sessions.get(theater_id)

    def get_or_create_session(
        self,
        theater_id: str,
        canvas_state_service: Optional[Any] = None,
        use_in_memory_artifacts: bool = False,
    ) -> AgentSession:
        """Fetch an existing active session or instantiate a new AgentSession."""
        existing = self.get_session(theater_id)
        if existing and existing.status != "stopped":
            existing.last_active_at = time.time()
            return existing

        logger.info(f"[AgentSessionManager] Creating new AgentSession for theater_id={theater_id}")

        canvas_mgr = canvas_state_service.get(theater_id) if canvas_state_service and hasattr(canvas_state_service, "get") else None

        theater_config = get_theater_config(theater_id)
        session_run_config = build_run_config(config=theater_config)

        tool_bundle = create_tool_bundle_for_session(
            theater_id=theater_id,
            config=theater_config,
            canvas_state_service=canvas_state_service,
        )

        session_agent = create_agent(
            theater_id=theater_id,
            config=theater_config,
            canvas_state_service=canvas_state_service,
            tool_bundle=tool_bundle,
        )

        disk_service_path = ensure_theaters_root() / theater_id / "output" / "artifacts"
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
            theater_id=theater_id,
            agent=session_agent,
            runner=runner,
            session_service=self.shared_session_service,
            artifact_service=artifact_service,
            run_config=session_run_config,
            config=theater_config,
            canvas_state_manager=canvas_mgr,
            tool_bundle=tool_bundle,
        )

        agent_session.start_background_tasks()
        self._sessions[theater_id] = agent_session
        return agent_session

    def stop_session(self, theater_id: str) -> bool:
        """Stop and remove an agent session from memory."""
        session = self._sessions.get(theater_id)
        if not session:
            return False

        logger.info(f"[AgentSessionManager] Stopping agent theater_id={theater_id}")
        session.close()
        del self._sessions[theater_id]
        return True

    def cleanup_idle_sessions(self, ttl_seconds: float = 300.0) -> list:
        """Purge sessions that are disconnected from WebSocket and idle for longer than ttl_seconds."""
        now = time.time()
        expired_ids = []
        for sid, session in list(self._sessions.items()):
            if not session.websocket_connected and (now - session.last_active_at) > ttl_seconds:
                expired_ids.append(sid)

        for sid in expired_ids:
            logger.info(f"[AgentSessionManager] Auto-cleaning idle theater_id={sid}")
            self.stop_session(sid)

        return expired_ids
