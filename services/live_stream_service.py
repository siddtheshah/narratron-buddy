import asyncio
import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional, Any

import websockets.exceptions
from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from components.canvas_state import CanvasStateManager

logger = logging.getLogger(__name__)

def format_canvas_state(
    canvas_state_manager: Optional[CanvasStateManager],
    story_planning_tools: Optional[Any] = None,
) -> str:
    """Format canvas and current-scene state injected into the live agent context."""
    image_path = getattr(canvas_state_manager, "shown_image_path", None)
    image_name = Path(image_path).name if image_path else "none"
    image_prompt = getattr(canvas_state_manager, "shown_image_prompt", None) or "none"
    playlist = getattr(canvas_state_manager, "current_playlist", None) or "none"
    parts = [f"[Canvas Image]: {image_name}, {image_prompt}", f"[Canvas music]: {playlist}"]

    # Collaboration observability consumes the leading suggestion. When it is
    # disabled, a canvas pulse must be read-only so audience work is retained
    # until collaboration is enabled again.
    collaboration_enabled = bool(
        canvas_state_manager
        and getattr(canvas_state_manager, "viewer_collab_enabled", False)
    )
    if collaboration_enabled:
        suggestion = canvas_state_manager.consume_top_suggestion()
        if suggestion:
            parts.append(
                f"[Viewer Suggestion]: {suggestion['text']} "
                f"(by {suggestion['author']}, {suggestion['upvote_count']} upvotes)"
            )

    elements = (
        story_planning_tools.get_present_elements()
        if story_planning_tools and hasattr(story_planning_tools, "get_present_elements")
        else []
    )
    if elements:
        rendered_elements = "; ".join(
            f"{element['name']}: {element['content']}" for element in elements
        )
        parts.append(f"[Present Scene Elements]: {rendered_elements}")
    characters = (
        story_planning_tools.get_present_characters()
        if story_planning_tools and hasattr(story_planning_tools, "get_present_characters")
        else []
    )
    if characters:
        rendered_chars = "; ".join(
            f"{c['name']} (Personality: {c.get('personality', 'N/A')}, Motivation: {c.get('motivation', 'N/A')}, Quirk: {c.get('quirk', 'N/A')})"
            for c in characters
        )
        parts.append(f"[Active Characters]: {rendered_chars}")

    return "\n".join(parts)



def get_bound_tool_instance(agent: object, tool_name: str) -> object:
    """Return the object owning the named function tool registered on an agent."""
    if not hasattr(agent, "tools") or not agent.tools:
        return None
    for tool in agent.tools:
        if getattr(tool, "name", None) == tool_name and hasattr(tool, "func"):
            instance = getattr(tool.func, "__self__", None)
            if instance is not None:
                return instance
    return None



def build_run_config(*args, **kwargs):
    """Lazy proxy for build_run_config defined in services.agent_manager."""
    from services.agent_manager import build_run_config as _build_run_config
    return _build_run_config(*args, **kwargs)


async def handle_live_websocket_connection(
    websocket: WebSocket,
    theater_id: str,
    agent_manager: Any,
    user_id: Optional[int] = None,
    send_setup_complete_immediately: bool = True,
    canvas_state_service: Optional[Any] = None,
) -> None:
    """Handles WebSocket attachment and upstream audio/text/image frames forwarding to AgentSession."""
    await websocket.accept()

    agent_session = agent_manager.get_or_create_session(
        theater_id=theater_id,
        canvas_state_service=canvas_state_service,
    )

    await agent_session.add_websocket(websocket, user_id=user_id)
    # The endpoint admitted this socket only for the active baton holder. Baton
    # transitions update this value without tearing down the live connection.
    agent_session.set_active_controller(user_id)

    if send_setup_complete_immediately:
        try:
            await websocket.send_text(json.dumps({"setupComplete": True}))
        except Exception:
            pass

    audio_chunk_count = 0
    total_audio_bytes = 0
    last_audio_log_time = time.monotonic()

    TARGET_AUDIO_CHUNK_BYTES = 960  # 30ms at 16kHz 16-bit mono PCM (16000 * 2 * 0.03)
    TARGET_AUDIO_FLUSH_INTERVAL = 0.030
    audio_buffer = bytearray()
    last_audio_flush_time = time.monotonic()

    def _send_audio_blob(chunk_bytes: bytes):
        nonlocal audio_chunk_count, total_audio_bytes, last_audio_log_time
        # A baton may change hands after a browser frame has entered this
        # per-socket buffer. Check again immediately before the shared live
        # queue receives it so stale partial audio cannot cross the handoff.
        if not chunk_bytes or not agent_session.can_accept_controller_input(user_id):
            return
        audio_chunk_count += 1
        total_audio_bytes += len(chunk_bytes)
        now = time.monotonic()
        if now - last_audio_log_time >= 5.0 or audio_chunk_count == 1:
            logger.info(
                f"[LiveStreamService] Audio chunk #{audio_chunk_count} ({len(chunk_bytes)} bytes, total {total_audio_bytes} bytes) sent to model for theater_id={theater_id}"
            )
            last_audio_log_time = now

        audio_blob = types.Blob(
            mime_type="audio/pcm;rate=16000", data=chunk_bytes
        )
        agent_session.record_audio_input(len(chunk_bytes))
        agent_session.send_realtime(audio_blob)

    def flush_audio_buffer(force_all: bool = False):
        nonlocal last_audio_flush_time
        while len(audio_buffer) >= TARGET_AUDIO_CHUNK_BYTES:
            chunk = bytes(audio_buffer[:TARGET_AUDIO_CHUNK_BYTES])
            del audio_buffer[:TARGET_AUDIO_CHUNK_BYTES]
            _send_audio_blob(chunk)
            last_audio_flush_time = time.monotonic()

        if force_all and audio_buffer:
            chunk = bytes(audio_buffer)
            audio_buffer.clear()
            _send_audio_blob(chunk)
            last_audio_flush_time = time.monotonic()

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message:
                audio_data = message.get("bytes")
                if not agent_session.can_accept_controller_input(user_id):
                    continue
                if not audio_data or len(audio_data) < 64:
                    continue
                audio_buffer.extend(audio_data)
                now = time.monotonic()
                if now - last_audio_flush_time >= TARGET_AUDIO_FLUSH_INTERVAL and len(audio_buffer) > 0:
                    flush_audio_buffer(force_all=True)
                else:
                    flush_audio_buffer(force_all=False)

            elif "text" in message:
                text_data = message.get("text")
                if not text_data:
                    continue
                try:
                    json_message = json.loads(text_data)
                except Exception:
                    logger.warning(f"Invalid JSON text received on websocket: {text_data}")
                    continue

                msg_type = json_message.get("type")
                if msg_type != "ping" and not agent_session.can_accept_controller_input(user_id):
                    continue
                if msg_type == "text":
                    user_text = json_message.get("text", "")
                    if user_text and user_text.strip():
                        content = types.Content(parts=[types.Part(text=user_text)])
                        agent_session.send_content(content)

                elif msg_type == "mic_detect":
                    rms = json_message.get("rms")
                    ts = json_message.get("ts")
                    logger.info(f"[Mic Detection] theater_id={theater_id} rms={rms} ts={ts}")

                elif msg_type == "activity_start":
                    logger.info(
                        "[Activity Start] theater_id=%s reason=%s",
                        theater_id,
                        json_message.get("reason", "unspecified"),
                    )
                    if hasattr(agent_session, "send_activity_start"):
                        agent_session.send_activity_start()

                elif msg_type == "activity_end":
                    logger.info(
                        "[Activity End] theater_id=%s reason=%s",
                        theater_id,
                        json_message.get("reason", "unspecified"),
                    )
                    if hasattr(agent_session, "send_activity_end"):
                        agent_session.send_activity_end()

                elif msg_type == "ping":
                    if agent_session.canvas_state_manager:
                        agent_session.canvas_state_manager.set_tool_activity("live", active=True, recent_seconds=10.0)
                    try:
                        await websocket.send_text(json.dumps({"type": "pong", "ts": json_message.get("ts")}))
                    except Exception:
                        pass

                elif msg_type == "image":
                    raw_data = json_message.get("data")
                    if raw_data:
                        try:
                            image_data = base64.b64decode(raw_data)
                            if image_data:
                                mime_type = json_message.get("mimeType", "image/jpeg")
                                image_blob = types.Blob(mime_type=mime_type, data=image_data)
                                agent_session.send_realtime(image_blob)
                        except Exception as e:
                            logger.warning(f"Failed to decode image payload: {e}")
    except (WebSocketDisconnect, RuntimeError):
        logger.debug(f"[LiveStreamService] Client disconnected for theater_id={theater_id}")
    finally:
        if audio_buffer:
            flush_audio_buffer(force_all=True)
        await agent_session.remove_websocket(websocket)

