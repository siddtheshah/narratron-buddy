import asyncio
import base64
import json
import logging
import mimetypes
import threading
import time
import uuid
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
    format_canvas_state,
    get_bound_tool_instance,
)
from services.preloaded_in_memory_artifact_service import PreloadedInMemoryArtifactService
from services.priority_live_request_queue import PriorityLiveRequestQueue
from utils.config_loader import get_app_config, get_theater_config
from components.theater_manager import TheaterManager
from utils.auth_cache import auth_session_cache

logger = logging.getLogger(__name__)

TOOL_INJECTION_INTERVAL_SECONDS = 30.0
DEFAULT_OBSERVABILITY_STARTUP_DELAY_SECONDS = 0.0
DEFAULT_OBSERVABILITY_INTERVAL_SECONDS = 45.0
DEFAULT_COLLABORATION_OBSERVABILITY_COOLDOWN_SECONDS = 5.0




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
        runner: Runner,
        tool_bundle: Any,
        database_manager: Optional[Any] = None,
        config: Optional[dict] = None,
        canvas_state_manager: Optional[Any] = None,
    ):
        import uuid
        self.theater_id = theater_id
        self.adk_session_id = f"adk_{theater_id}_{uuid.uuid4().hex[:8]}"
        self.adk_user_id = f"orator_{theater_id}"
        self.runner = runner
        self.agent = runner.agent
        self.session_service = runner.session_service
        self.tool_bundle = tool_bundle
        self.database_manager = database_manager
        self.config = config or {}
        self.canvas_state_manager = canvas_state_manager
        self.owner_user_id: Optional[int] = None
        agent_internal = self.config.get("agent_internal", {})
        self.enable_tool_injection = bool(agent_internal.get("enable_tool_injection", False))
        self.observability_startup_delay = self._get_nonnegative_config_seconds(
            agent_internal.get(
                "observability_startup_delay",
                DEFAULT_OBSERVABILITY_STARTUP_DELAY_SECONDS,
            ),
            "observability_startup_delay",
        )
        self.observability_interval = self._get_nonnegative_config_seconds(
            agent_internal.get(
                "observability_interval",
                DEFAULT_OBSERVABILITY_INTERVAL_SECONDS,
            ),
            "observability_interval",
        )
        self.collaboration_observability_cooldown = self._get_nonnegative_config_seconds(
            agent_internal.get(
                "collaboration_observability_cooldown",
                DEFAULT_COLLABORATION_OBSERVABILITY_COOLDOWN_SECONDS,
            ),
            "collaboration_observability_cooldown",
        )




        self.images_created_count: int = 0
        self.music_created_count: int = 0
        self.audio_bytes_received: int = 0
        self.unbilled_images: int = 0
        self.unbilled_music: int = 0
        self.unbilled_audio_bytes: int = 0
        # Batches remain here until their durable idempotency key has been
        # acknowledged, so a timeout can retry the exact same debit safely.
        self.story_plans_count: int = 0
        self.unbilled_story_plans: int = 0
        self._pending_usage_batches: list[tuple[str, int, int, int, int]] = []
        self.created_at = time.time()
        self.last_active_at = time.time()
        self.status = "ready"  # "ready", "active", "stopped"


        self.live_request_queue = PriorityLiveRequestQueue()
        self.websockets: Set[WebSocket] = set()
        self.websocket_user_ids: Dict[WebSocket, Optional[int]] = {}
        self.active_controller_user_id: Optional[int] = None
        self.ws_lock = asyncio.Lock()

        # Retrieve bound tool instances safely
        self.image_tools = get_bound_tool_instance(self.agent, "create_image")
        self.animation_tools = get_bound_tool_instance(self.agent, "create_triframe")
        self.chat_tools = get_bound_tool_instance(self.agent, "send_chat_message")
        self.story_planning_tools = (
            get_bound_tool_instance(self.agent, "process_user_action")
            or get_bound_tool_instance(self.agent, "update_or_insert_named_element")
        )
        self.music_tools = get_bound_tool_instance(self.agent, "play_music")
        self.observability_tools = get_bound_tool_instance(
            self.agent,
            "request_canvas_observability",
        )

        self.run_config = build_run_config(
            agent=self.agent,
            config=self.config,
        )

        self._setup_tool_callbacks()

        self.downstream_task: Optional[asyncio.Task] = None
        self.refresh_task: Optional[asyncio.Task] = None
        self.tool_injection_task: Optional[asyncio.Task] = None
        self.observability_available_at = time.monotonic() + self.observability_startup_delay
        self.last_canvas_state_sent: Optional[float] = None
        self.last_collaboration_observability_sent: Optional[float] = None
        self.state_lock = threading.Lock()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._doodle_snapshot_task: Optional[asyncio.Task] = None

    @staticmethod
    def _get_nonnegative_config_seconds(value: Any, setting_name: str) -> float:
        """Return a non-negative duration, falling back safely for malformed settings."""
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            logger.warning("Invalid %s value %r; using 0 seconds.", setting_name, value)
            return 0.0

    @property
    def websocket_connected(self) -> bool:
        return len(self.websockets) > 0

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
            self.send_canvas_state()

        def handle_scene_reaction(result: Dict[str, Any]) -> None:
            """Place asynchronous planner output onto the live queue safely."""
            message = "[Story Planner Result] " + json.dumps(result, ensure_ascii=False)
            content = types.Content(parts=[types.Part(text=message)])

            def enqueue() -> None:
                self.send_content(content)

            if self._event_loop and self._event_loop.is_running():
                self._event_loop.call_soon_threadsafe(enqueue)
            else:
                enqueue()

        for tool_suite in (
            self.image_tools,
            self.animation_tools,
            self.chat_tools,
            self.story_planning_tools,
            self.music_tools,
            self.observability_tools,
        ):
            if tool_suite and hasattr(tool_suite, "on_cooldown_expired"):
                tool_suite.on_cooldown_expired = handle_cooldown_expired

        if self.story_planning_tools:
            self.story_planning_tools.on_scene_reaction = handle_scene_reaction
            self.story_planning_tools.on_story_plan_completed = self.record_story_plan_completed

        if self.image_tools:
            self.image_tools.on_after_tool_call = handle_after_image_tool
            self.image_tools.on_image_created = self.record_image_created

        if self.music_tools:
            self.music_tools.on_music_created = self.record_music_created

        if self.observability_tools:
            self.observability_tools.on_observability_requested = (
                self.send_agent_requested_observability
            )

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

    def send_canvas_state(self) -> bool:
        """Inject current canvas image/music state into LiveRequestQueue."""
        if not self.websocket_connected:
            logger.debug(f"[AgentSession] User disconnected; suppressing canvas state update for session {self.theater_id}.")
            return False
        now = time.monotonic()
        with self.state_lock:
            if now < self.observability_available_at:
                return False
            if (
                self.last_canvas_state_sent is not None
                and now - self.last_canvas_state_sent < self.observability_interval
            ):
                return False
            msg = format_canvas_state(self.canvas_state_manager, self.story_planning_tools)
            try:
                self.send_content(types.Content(parts=[types.Part(text=msg)]))
            except Exception as e:
                logger.error(f"[AgentSession] Failed to send canvas observability update: {e}", exc_info=True)
                return False
            self.last_canvas_state_sent = now
        self._schedule_doodle_snapshot()
        logger.info("[AgentSession] Canvas state update: %s", msg.replace("\n", " | "))
        return True

    def send_collaboration_toggle_observability(self) -> bool:
        """Send a canvas update for a collaboration toggle, subject to a cooldown.

        A successful update also refreshes ``last_canvas_state_sent``, naturally
        deferring the next periodic canvas observability update.
        """
        if not self.websocket_connected:
            logger.debug(
                "[AgentSession] User disconnected; suppressing collaboration toggle update for session %s.",
                self.theater_id,
            )
            return False

        now = time.monotonic()
        with self.state_lock:
            if (
                self.last_collaboration_observability_sent is not None
                and now - self.last_collaboration_observability_sent
                < self.collaboration_observability_cooldown
            ):
                logger.debug(
                    "[AgentSession] Collaboration toggle update is cooling down for session %s.",
                    self.theater_id,
                )
                return False

            msg = format_canvas_state(self.canvas_state_manager, self.story_planning_tools)
            try:
                self.send_content(types.Content(parts=[types.Part(text=msg)]))
            except Exception as e:
                logger.error(
                    "[AgentSession] Failed to send collaboration toggle observability update: %s",
                    e,
                    exc_info=True,
                )
                return False
            self.last_canvas_state_sent = now
            self.last_collaboration_observability_sent = now

        self._schedule_doodle_snapshot()
        logger.info("[AgentSession] Collaboration toggle canvas state update: %s", msg.replace("\n", " | "))
        return True

    def send_agent_requested_observability(self) -> bool:
        """Send an explicit agent-requested canvas update and defer regular pulses.

        Unlike the regular text pulse, this request includes a visual snapshot
        of the current canvas when it is available so the agent can inspect it.
        When viewer doodles are active, the annotated composite is attached in
        the *same* content item as the state text.  Sending the base image and
        then an asynchronous doodle image made it easy for a live turn to act
        on the unannotated image before the annotation arrived.
        """
        if not self.websocket_connected:
            logger.debug(
                "[AgentSession] User disconnected; suppressing agent-requested canvas update for session %s.",
                self.theater_id,
            )
            return False

        now = time.monotonic()
        with self.state_lock:
            msg = format_canvas_state(self.canvas_state_manager, self.story_planning_tools)
            parts = [types.Part(text=msg)]
            image_part = self._get_current_canvas_image_part()
            if image_part:
                if (
                    getattr(self.canvas_state_manager, "viewer_collab_enabled", False)
                    and hasattr(self.canvas_state_manager, "get_doodle_snapshot_data")
                    and self.canvas_state_manager.get_doodle_snapshot_data()
                ):
                    parts.append(types.Part(
                        text="[Viewer Doodles]: The attached image is the current canvas with the audience annotations applied."
                    ))
                parts.append(image_part)
            try:
                self.send_content(types.Content(parts=parts))
            except Exception as e:
                logger.error(
                    "[AgentSession] Failed to send agent-requested canvas observability update: %s",
                    e,
                    exc_info=True,
                )
                return False
            # Share the periodic timestamp with every observation source.
            self.last_canvas_state_sent = now

        # The visual attached above already includes viewer doodles when
        # collaboration is enabled, so do not enqueue a second, late image.
        logger.info("[AgentSession] Agent-requested canvas state update: %s", msg.replace("\n", " | "))
        return True

    def _get_current_canvas_image_part(self) -> Optional[types.Part]:
        """Return the current visible canvas image as an inline part.

        Prefer the server-rendered doodle composite during collaboration.  It
        is PNG regardless of the source-image format and is therefore also a
        reliable decode path for the Live API.  Fall back to the source image
        when there is no visible annotation or the composite cannot be made.
        """
        canvas = self.canvas_state_manager
        if (
            canvas
            and getattr(canvas, "viewer_collab_enabled", False)
            and hasattr(canvas, "get_doodle_snapshot_data")
            and canvas.get_doodle_snapshot_data()
            and hasattr(canvas, "get_doodle_snapshot_png")
        ):
            try:
                snapshot = canvas.get_doodle_snapshot_png()
                if snapshot:
                    return types.Part(
                        inline_data=types.Blob(mime_type="image/png", data=snapshot)
                    )
            except Exception:
                logger.exception(
                    "Could not render doodle composite for observability in theater %s",
                    self.theater_id,
                )

        image_path = getattr(canvas, "shown_image_path", None)
        if not image_path:
            return None
        try:
            path = Path(image_path)
            if not path.is_file():
                return None
            mime_type, _ = mimetypes.guess_type(path.name)
            if not mime_type or not mime_type.startswith("image/"):
                return None
            image_data = path.read_bytes()
            if not image_data:
                return None
            return types.Part(inline_data=types.Blob(mime_type=mime_type, data=image_data))
        except OSError as error:
            logger.warning(
                "[AgentSession] Could not load current canvas image for observability in theater %s: %s",
                self.theater_id,
                error,
            )
            return None

    def _schedule_doodle_snapshot(self) -> None:
        """Schedule one composite doodle render without blocking the event loop."""
        if not (
            self.websocket_connected
            and self.canvas_state_manager
            and getattr(self.canvas_state_manager, "viewer_collab_enabled", False)
            and self.canvas_state_manager.get_doodle_snapshot_data()
        ):
            return

        def start_render() -> None:
            if self._doodle_snapshot_task and not self._doodle_snapshot_task.done():
                return
            self._doodle_snapshot_task = asyncio.create_task(self._send_doodle_snapshot())

        loop = self._event_loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(start_render)
            return
        try:
            asyncio.get_running_loop().call_soon(start_render)
        except RuntimeError:
            logger.debug("No running event loop available for doodle snapshot in theater %s", self.theater_id)

    async def _send_doodle_snapshot(self) -> None:
        """Render the composite PNG in a worker, then enqueue it for the agent."""
        try:
            snapshot = await asyncio.to_thread(self.canvas_state_manager.get_doodle_snapshot_png)
            if snapshot and self.websocket_connected:
                content = types.Content(parts=[
                    types.Part(text="[Viewer Doodles]: A composite canvas image with audience doodles is attached."),
                    types.Part(inline_data=types.Blob(mime_type="image/png", data=snapshot)),
                ])
                self.send_content(content)
        except Exception:
            logger.exception("Failed to render viewer doodle snapshot for theater %s", self.theater_id)

    def start_background_tasks(self):
        """Start long-running downstream_task (runner.run_live), canvas refresh loop, and tool injection loop."""
        self._event_loop = asyncio.get_running_loop()
        if self.downstream_task is None or self.downstream_task.done():
            self.downstream_task = asyncio.create_task(self._run_downstream())

        if self.refresh_task is None or self.refresh_task.done():
            self.refresh_task = asyncio.create_task(self._run_canvas_refresh())

        if self.enable_tool_injection and (self.tool_injection_task is None or self.tool_injection_task.done()):
            self.tool_injection_task = asyncio.create_task(self._run_tool_injection_loop())

        # Canvas observability begins after the configured startup delay.
        self.send_canvas_state()


    async def _run_downstream(self):
        """Task that runs runner.run_live() continuously and broadcasts model events to attached WebSockets."""
        logger.info(f"[AgentSession] Starting downstream_task (runner.run_live) for theater_id={self.theater_id}")
        try:
            if await self.session_service.get_session(
                app_name=self.runner.app_name,
                user_id=self.adk_user_id,
                session_id=self.adk_session_id,
            ) is None or self.status == 'stopped':
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
            if self.canvas_state_manager:
                try:
                    self.canvas_state_manager.set_agent_thought("wandering")
                except Exception:
                    logger.exception("[AgentSession] Could not update failed agent thought for theater_id=%s", self.theater_id)
            try:
                await self.broadcast_text(json.dumps({
                    "type": "agent_failed",
                    "detail": "Narratron lost its train of thought and stopped.",
                }))
            finally:
                self.close()

    async def _run_canvas_refresh(self):
        try:
            while True:
                db_inst = self._get_database()
                owner_id = self._get_owner_id(db_inst)
                if db_inst and owner_id:
                    try:
                        owner = db_inst.get_user_by_id(owner_id)
                        if owner and owner.get("credits", 0.0) <= 0.0:
                            logger.warning(f"[AgentSession] Owner user {owner_id} credit balance <= 0. Auto-stopping agent session for {self.theater_id}.")
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
                await asyncio.sleep(60.0)
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


    async def add_websocket(self, websocket: WebSocket, user_id: Optional[int] = None):
        async with self.ws_lock:
            was_disconnected = len(self.websockets) == 0 or self.status == "stopped"
            self.websockets.add(websocket)
            self.websocket_user_ids[websocket] = user_id
            self.status = "active"
            self.last_active_at = time.time()
            logger.info(f"[AgentSession] WebSocket attached to session {self.theater_id} (total={len(self.websockets)})")

        if was_disconnected:
            logger.info(f"[AgentSession] User reconnected for session {self.theater_id}; re-enabling state information.")
            self.send_canvas_state()

    async def revoke_websockets_except(self, active_user_id: int) -> None:
        """Close agent-control sockets belonging to a previous baton holder."""
        async with self.ws_lock:
            sockets_to_close = [
                websocket
                for websocket in self.websockets
                if self.websocket_user_ids.get(websocket) != active_user_id
            ]
        for websocket in sockets_to_close:
            try:
                await websocket.close(code=1008)
            except (RuntimeError, ConnectionResetError):
                pass

    def set_active_controller(self, user_id: Optional[int]) -> None:
        """Hand off live input without disconnecting the agent session.

        The Live API needs the previous audio activity to end before another
        controller begins one.  Emit that boundary here rather than relying on
        a browser frame that may arrive after its baton access was revoked.
        """
        if (
            self.active_controller_user_id is not None
            and self.active_controller_user_id != user_id
        ):
            self.send_activity_end()
        self.active_controller_user_id = user_id

    def can_accept_controller_input(self, user_id: Optional[int]) -> bool:
        """Return whether this socket's user currently holds the baton."""
        return (
            self.active_controller_user_id is None
            or user_id == self.active_controller_user_id
        )

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

    def record_music_created(self, music_path: str = ""):
        """Record music track created for active theater session and flush usage."""
        self.music_created_count += 1
        self.unbilled_music += 1
        logger.info(f"[AgentSession] Music created recorded for theater {self.theater_id} (total={self.music_created_count})")
        self.flush_usage_to_db()

    def record_story_plan_completed(self):
        """Record a successfully resolved story-planning turn and flush it for billing."""
        self.story_plans_count += 1
        self.unbilled_story_plans += 1
        logger.info(
            "[AgentSession] Story plan completed for theater %s (total=%s)",
            self.theater_id,
            self.story_plans_count,
        )
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
        """Deduct credits and record cumulative voice minutes / images created / music created in database."""
        if (
            self.unbilled_audio_bytes > 0
            or self.unbilled_images > 0
            or self.unbilled_music > 0
            or self.unbilled_story_plans > 0
        ):
            self._pending_usage_batches.append((
                f"live-usage:{self.theater_id}:{uuid.uuid4()}",
                self.unbilled_audio_bytes,
                self.unbilled_images,
                self.unbilled_music,
                self.unbilled_story_plans,
            ))
            self.unbilled_audio_bytes = 0
            self.unbilled_images = 0
            self.unbilled_music = 0
            self.unbilled_story_plans = 0

        if not self._pending_usage_batches:
            return

        db_inst = self._get_database()
        owner_id = self._get_owner_id(db_inst)

        if db_inst and owner_id:
            while self._pending_usage_batches:
                event_key, unbilled_audio_bytes, unbilled_img, unbilled_mus, unbilled_story_plans = self._pending_usage_batches[0]
                unbilled_vm = unbilled_audio_bytes / 1920000.0
                try:
                    updated_user = db_inst.record_user_usage(
                        user_id=owner_id,
                        voice_minutes=unbilled_vm,
                        images_created=unbilled_img,
                        music_created=unbilled_mus,
                        story_plans=unbilled_story_plans,
                        idempotency_key=event_key,
                    )
                    self._pending_usage_batches.pop(0)
                    auth_session_cache.invalidate_user(owner_id)
                    logger.info(
                        f"[AgentSession] Flushed usage to DB for user {owner_id} (theater {self.theater_id}): voice_minutes={unbilled_vm:.4f}, images={unbilled_img}, music={unbilled_mus}, story_plans={unbilled_story_plans}"
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
                    # Retain the exact event key and payload.  Retrying it is
                    # safe whether the timed-out commit did or did not land.
                    logger.error(f"[AgentSession] Error flushing usage to DB: {e}")
                    return

    def get_usage(self) -> Dict[str, Any]:
        """Return usage summary dictionary for the active session."""
        return {
            "theater_id": self.theater_id,
            "owner_user_id": self.owner_user_id,
            "voice_minutes": self.voice_minutes,
            "images_created": self.images_created_count,
            "music_created": self.music_created_count,
            "story_plans": self.story_plans_count,
            "total_audio_bytes": self.audio_bytes_received,
        }

    def _get_database(self) -> Optional[Any]:
        """Return the database manager supplied by the session manager."""
        return self.database_manager

    def _get_owner_id(self, db_inst: Optional[Any]) -> Optional[int]:
        if self.owner_user_id is None and db_inst:
            try:
                deployment = db_inst.get_deployment(self.theater_id)
                if deployment:
                    self.owner_user_id = deployment.get("user_id")
            except Exception as e:
                logger.debug(f"[AgentSession] Could not fetch deployment owner: {e}")
        return self.owner_user_id

    def save_named_elements_to_session_state(self):
        """Save named elements snapshot to canvas state / session state when agent connection drops."""
        tools = self.story_planning_tools
        if tools:
            if hasattr(tools, "save_to_session_state"):
                tools.save_to_session_state()
            elif hasattr(tools, "get_present_elements") and self.canvas_state_manager:
                if hasattr(self.canvas_state_manager, "set_named_elements"):
                    self.canvas_state_manager.set_named_elements(
                        tools.get_present_elements()
                    )

    async def remove_websocket(self, websocket: WebSocket):
        async with self.ws_lock:
            self.websockets.discard(websocket)
            self.websocket_user_ids.pop(websocket, None)
            self.last_active_at = time.time()
            is_now_disconnected = len(self.websockets) == 0
            if is_now_disconnected:
                self.status = "ready"
            logger.info(f"[AgentSession] WebSocket detached from session {self.theater_id} (remaining={len(self.websockets)})")
            if is_now_disconnected:
                logger.info(f"[AgentSession] User disconnected for session {self.theater_id}; inputs are now suppressed.")
                self.save_named_elements_to_session_state()
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
                    self.websocket_user_ids.pop(ws, None)

    def close(self):
        """Close LiveRequestQueue and cancel background tasks."""
        self.status = "stopped"
        self.save_named_elements_to_session_state()
        self.flush_usage_to_db()
        if self.downstream_task and not self.downstream_task.done():
            self.downstream_task.cancel()
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
        if self.tool_injection_task and not self.tool_injection_task.done():
            self.tool_injection_task.cancel()
        if self._doodle_snapshot_task and not self._doodle_snapshot_task.done():
            self._doodle_snapshot_task.cancel()
        try:
            self.live_request_queue.close()
        except Exception as e:
            logger.debug(f"[AgentSession] Error closing live_request_queue: {e}")

from services.agent import create_agent, create_tool_bundle_for_session


class AgentSessionManager:
    def __init__(
        self,
        theater_manager: TheaterManager,
        database_manager: Any,
        app_name: str = "narratron-combined",
        config: Optional[dict] = None,
    ):
        self.app_name = app_name
        self.config = config or {}
        self.theater_manager = theater_manager
        self.database_manager = database_manager
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

        theater_config = get_theater_config(theater_id, base_dir=self.theater_manager.base_dir)
        story_planning_config = theater_config.get("story_planning", {})
        if (
            canvas_mgr
            and isinstance(story_planning_config, dict)
            and bool(story_planning_config.get("adventure_mode", False))
            and bool(story_planning_config.get("character_voicing", False))
            and hasattr(canvas_mgr, "enable_scene_speech")
        ):
            canvas_mgr.enable_scene_speech()
        tool_bundle = create_tool_bundle_for_session(
            theater_id=theater_id,
            config=theater_config,
            canvas_state_service=canvas_state_service,
            theater_manager=self.theater_manager,
            database_manager=self.database_manager,
        )

        session_agent = create_agent(
            theater_id=theater_id,
            config=theater_config,
            canvas_state_service=canvas_state_service,
            tool_bundle=tool_bundle,
            theater_manager=self.theater_manager,
            database_manager=self.database_manager,
        )

        disk_service_path = self.theater_manager.theater(theater_id).artifacts_dir()
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
            runner=runner,
            tool_bundle=tool_bundle,
            database_manager=self.database_manager,
            config=theater_config,
            canvas_state_manager=canvas_mgr,
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

    async def revoke_agent_access_except(self, theater_id: str, active_user_id: int) -> None:
        """Disconnect any live controller that no longer holds this theater's baton."""
        session = self.get_session(theater_id)
        if session:
            await session.revoke_websockets_except(active_user_id)

    def set_active_controller(self, theater_id: str, user_id: Optional[int]) -> None:
        """Keep a live theater session connected when the baton changes hands."""
        session = self.get_session(theater_id)
        if session:
            session.set_active_controller(user_id)
