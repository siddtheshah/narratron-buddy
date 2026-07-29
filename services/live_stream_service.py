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

CANVAS_STATE_REFRESH_SECONDS = 45.0


def format_canvas_state(canvas_state_manager: Optional[CanvasStateManager]) -> str:
    """Format the compact canvas state injected into the live agent context."""
    image_path = getattr(canvas_state_manager, "shown_image_path", None)
    image_name = Path(image_path).name if image_path else "none"
    image_prompt = getattr(canvas_state_manager, "shown_image_prompt", None) or "none"
    playlist = getattr(canvas_state_manager, "current_playlist", None) or "none"
    return f"[Canvas Image]: {image_name}, {image_prompt}\n[Canvas music]: {playlist}"


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
    narratron_session_id: str,
    agent_manager: Any,
    send_setup_complete_immediately: bool = True,
) -> None:
    """Handles WebSocket attachment and upstream audio/text/image frames forwarding to AgentSession."""
    await websocket.accept()

    agent_session = agent_manager.get_or_create_session(
        narratron_session_id=narratron_session_id
    )

    await agent_session.add_websocket(websocket)

    if send_setup_complete_immediately:
        try:
            await websocket.send_text(json.dumps({"setupComplete": True}))
        except Exception:
            pass

    audio_chunk_count = 0
    total_audio_bytes = 0
    last_audio_log_time = time.monotonic()

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message:
                audio_data = message.get("bytes")
                if not audio_data or len(audio_data) < 64:
                    continue
                audio_chunk_count += 1
                total_audio_bytes += len(audio_data)
                now = time.monotonic()
                if now - last_audio_log_time >= 5.0 or audio_chunk_count == 1:
                    logger.info(
                        f"[LiveStreamService] Audio stream active: received {audio_chunk_count} chunks ({total_audio_bytes} bytes total) for narratron_session_id={narratron_session_id}"
                    )
                    last_audio_log_time = now

                audio_blob = types.Blob(
                    mime_type="audio/pcm;rate=16000", data=audio_data
                )
                agent_session.live_request_queue.send_realtime(audio_blob)

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
                if msg_type == "text":
                    user_text = json_message.get("text", "")
                    if user_text and user_text.strip():
                        content = types.Content(parts=[types.Part(text=user_text)])
                        agent_session.live_request_queue.send_content(content)

                elif msg_type == "mic_detect":
                    rms = json_message.get("rms")
                    ts = json_message.get("ts")
                    logger.info(f"[Mic Detection] narratron_session_id={narratron_session_id} rms={rms} ts={ts}")

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
                                agent_session.live_request_queue.send_realtime(image_blob)
                        except Exception as e:
                            logger.warning(f"Failed to decode image payload: {e}")
    except (WebSocketDisconnect, RuntimeError):
        logger.debug(f"[LiveStreamService] Client disconnected for narratron_session_id={narratron_session_id}")
    finally:
        await agent_session.remove_websocket(websocket)
