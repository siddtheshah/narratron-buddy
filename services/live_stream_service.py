import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
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
    for tool in agent.tools:
        if tool.name == tool_name and hasattr(tool, "func"):
            instance = getattr(tool.func, "__self__", None)
            if instance is not None:
                return instance
    raise ValueError(f"Agent does not have a bound '{tool_name}' tool.")


async def handle_live_websocket_connection(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
    agent: any,
    runner: any,
    session_service: any,
    config: dict,
    proactivity: bool = False,
    affective_dialog: bool = False,
    on_global_chat_message: any = None,
    app_name: str = "narratron-bidi",
    send_setup_complete_immediately: bool = True,
    send_setup_after_delay: bool = False,
    canvas_state_manager: Optional[object] = None,
) -> None:
    """Handles bidirectional WebSocket streaming between a client and ADK Gemini Live runner."""
    image_tools = get_bound_tool_instance(agent, "create_image")
    chat_tools = get_bound_tool_instance(agent, "send_chat_message")
    notes_tools = get_bound_tool_instance(agent, "edit_notes")
    music_tools = get_bound_tool_instance(agent, "play_playlist")

    logger.debug(
        f"WebSocket connection request: user_id={user_id}, session_id={session_id}, "
        f"proactivity={proactivity}, affective_dialog={affective_dialog}"
    )
    await websocket.accept()
    logger.debug("WebSocket connection accepted")

    if hasattr(image_tools, "active_session_id"):
        image_tools.active_session_id = session_id

    if notes_tools and hasattr(notes_tools, "active_session_id"):
        notes_tools.active_session_id = session_id

    websocket_write_lock = asyncio.Lock()

    async def safe_send_text(text: str) -> None:
        async with websocket_write_lock:
            await websocket.send_text(text)

    if send_setup_complete_immediately:
        await safe_send_text(json.dumps({"setupComplete": True}))

    try:
        model_name = agent.model
        is_native_audio = any(
            token in model_name.lower()
            for token in ["native-audio", "1.5-flash", "2.0-flash-exp", "3.1-flash", "live"]
        )

        # Load compaction config if present
        compaction = config.get("agent", {}).get("compaction", {})
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
            run_config = RunConfig(
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
                    max_workers=3
                )
            )
        else:
            response_modalities = ["TEXT"]
            run_config = RunConfig(
                streaming_mode=StreamingMode.BIDI,
                response_modalities=response_modalities,
                input_audio_transcription=None,
                output_audio_transcription=None,
                context_window_compression=compaction_config,
            )

        # Get or create session
        session = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        if not session:
            await session_service.create_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )

        loop = asyncio.get_running_loop()

        def handle_session_chat_message(text: str):
            if on_global_chat_message:
                on_global_chat_message(text)

            async def send_to_ws():
                try:
                    custom_event = {
                        "author": "agent",
                        "content": {"parts": [{"text": text}]},
                    }
                    await safe_send_text(json.dumps(custom_event))
                except Exception as e:
                    logger.error(f"Error sending tool chat message to websocket: {e}")

            loop.create_task(send_to_ws())

        chat_tools.on_send_chat_message = handle_session_chat_message
        live_request_queue = LiveRequestQueue()

        def handle_cooldown_expired(tool_name: str):
            msg = f"[System Notification] The cooldown for '{tool_name}' has expired. You may now call {tool_name} again."
            logger.info(f"[LiveStreamService] Cooldown expired notification: {msg}")
            try:
                content = types.Content(parts=[types.Part(text=msg)])
                live_request_queue.send_content(content)
            except Exception as e:
                logger.error(f"[LiveStreamService] Failed to send cooldown expired notification: {e}")

        state_lock = threading.Lock()
        # Start the refresh window when the live session starts; image tool calls
        # can still force an immediate snapshot.
        last_canvas_state_sent = time.monotonic()

        def send_canvas_state(*, force: bool = False) -> bool:
            """Inject the current image and music state, subject to the refresh interval."""
            nonlocal last_canvas_state_sent
            now = time.monotonic()
            with state_lock:
                if not force and now - last_canvas_state_sent < CANVAS_STATE_REFRESH_SECONDS:
                    return False
                msg = format_canvas_state(canvas_state_manager)
                try:
                    live_request_queue.send_content(types.Content(parts=[types.Part(text=msg)]))
                except Exception as e:
                    logger.error(f"[LiveStreamService] Failed to send canvas observability update: {e}")
                    return False
                last_canvas_state_sent = now
            logger.info("[LiveStreamService] Canvas state update: %s", msg.replace("\n", " | "))
            return True

        def handle_after_image_tool(_tool_name: str, _canvas_info: dict):
            # Every image tool call gets a fresh state snapshot, including failed/cooldown calls.
            send_canvas_state(force=True)

        for tool_suite in (image_tools, chat_tools, notes_tools, music_tools):
            if tool_suite and hasattr(tool_suite, "on_cooldown_expired"):
                tool_suite.on_cooldown_expired = handle_cooldown_expired

        if image_tools:
            image_tools.on_after_tool_call = handle_after_image_tool

        async def canvas_state_refresh_task() -> None:
            """Refresh agent context whenever the compact canvas state is older than 45 seconds."""
            try:
                while True:
                    # Check frequently enough that a state which becomes stale just
                    # after the sleep is refreshed promptly, without busy-waiting.
                    await asyncio.sleep(1.0)
                    send_canvas_state()
            except asyncio.CancelledError:
                return

        async def upstream_task() -> None:
            """Receives messages from WebSocket and sends to LiveRequestQueue."""
            while True:
                message = await websocket.receive()

                if "bytes" in message:
                    audio_data = message["bytes"]
                    logger.debug(f"[Upstream Audio] Received chunk: {len(audio_data)} bytes")
                    audio_blob = types.Blob(
                        mime_type="audio/pcm;rate=16000", data=audio_data
                    )
                    live_request_queue.send_realtime(audio_blob)

                elif "text" in message:
                    text_data = message["text"]
                    json_message = json.loads(text_data)

                    if json_message.get("type") == "text":
                        content = types.Content(
                            parts=[types.Part(text=json_message["text"])]
                        )
                        live_request_queue.send_content(content)

                    elif json_message.get("type") == "mic_detect":
                        try:
                            rms = json_message.get("rms")
                            ts = json_message.get("ts")
                            logger.info(f"[Mic Detection] session={session_id} user={user_id} rms={rms} ts={ts}")
                        except Exception:
                            logger.exception("Failed to process mic_detect message")

                    elif json_message.get("type") == "image":
                        image_data = base64.b64decode(json_message["data"])
                        mime_type = json_message.get("mimeType", "image/jpeg")
                        image_blob = types.Blob(mime_type=mime_type, data=image_data)
                        live_request_queue.send_realtime(image_blob)

        async def downstream_task() -> None:
            """Receives Events from run_live() and sends to WebSocket."""
            if send_setup_after_delay:
                async def send_setup_signal():
                    await asyncio.sleep(1.0)
                    logger.info("Gemini Live session initialized. Sending setupComplete to client.")
                    await safe_send_text(json.dumps({"setupComplete": True}))

                loop.create_task(send_setup_signal())

            async for event in runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=live_request_queue,
                run_config=run_config,
            ):
                if hasattr(event, "get_function_calls") and event.get_function_calls():
                    for call in event.get_function_calls():
                        logger.info(f"[Agent Tool Call] Function: {call.name}, Args: {call.args}")

                event_dict = json.loads(event.model_dump_json(exclude_none=True, by_alias=True))
                # Strip raw audio data (inlineData) from the event before sending to client
                if "content" in event_dict and "parts" in event_dict["content"]:
                    event_dict["content"]["parts"] = [
                        part for part in event_dict["content"]["parts"]
                        if "inlineData" not in part
                    ]

                event_json = json.dumps(event_dict)
                await safe_send_text(event_json)

        refresh_task = asyncio.create_task(canvas_state_refresh_task())
        try:
            await asyncio.gather(upstream_task(), downstream_task())
        except WebSocketDisconnect:
            logger.debug("Client disconnected normally")
        except RuntimeError as re:
            if "disconnect message has been received" in str(re):
                logger.debug("Client disconnected normally (receive after disconnect)")
            else:
                logger.error(f"Unexpected error in streaming tasks: {re}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error in streaming tasks: {e}", exc_info=True)
        finally:
            refresh_task.cancel()
            await asyncio.gather(refresh_task, return_exceptions=True)
            chat_tools.on_send_chat_message = on_global_chat_message
            live_request_queue.close()
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN WEBSOCKET SETUP: {e}", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
