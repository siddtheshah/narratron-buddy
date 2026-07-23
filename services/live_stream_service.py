import asyncio
import base64
import json
import logging
import os

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

from utils.image_utils import resolve_image_path

logger = logging.getLogger(__name__)

async def handle_live_websocket_connection(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
    agent: any,
    runner: any,
    session_service: any,
    config: dict,
    image_tools: any,
    chat_tools: any,
    proactivity: bool = False,
    affective_dialog: bool = False,
    on_global_chat_message: any = None,
    app_name: str = "narratron-bidi",
    send_setup_complete_immediately: bool = True,
    send_setup_after_delay: bool = False,
) -> None:
    """Handles bidirectional WebSocket streaming between a client and ADK Gemini Live runner."""
    logger.debug(
        f"WebSocket connection request: user_id={user_id}, session_id={session_id}, "
        f"proactivity={proactivity}, affective_dialog={affective_dialog}"
    )
    await websocket.accept()
    logger.debug("WebSocket connection accepted")

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
                        if call.name in ("show_image", "create_image"):
                            file_path = call.args.get("file_path")
                            resolved_path = resolve_image_path(file_path, [image_tools.output_dir]) if file_path else None
                            if resolved_path:
                                try:
                                    with open(resolved_path, "rb") as f:
                                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                                    mime_type = "image/png"
                                    if resolved_path.lower().endswith(".jpg") or resolved_path.lower().endswith(".jpeg"):
                                        mime_type = "image/jpeg"
                                    custom_event = {
                                        "custom_image": {
                                            "mimeType": mime_type,
                                            "data": img_b64,
                                        }
                                    }
                                    await safe_send_text(json.dumps(custom_event))
                                except Exception as e:
                                    logger.error(f"Error reading image for UI: {e}")

                event_dict = json.loads(event.model_dump_json(exclude_none=True, by_alias=True))
                # Strip raw audio data (inlineData) from the event before sending to client
                if "content" in event_dict and "parts" in event_dict["content"]:
                    event_dict["content"]["parts"] = [
                        part for part in event_dict["content"]["parts"]
                        if "inlineData" not in part
                    ]

                event_json = json.dumps(event_dict)
                await safe_send_text(event_json)

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
            chat_tools.on_send_chat_message = on_global_chat_message
            live_request_queue.close()
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN WEBSOCKET SETUP: {e}", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
