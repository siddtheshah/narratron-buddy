"""FastAPI application demonstrating ADK Gemini Live API Toolkit with WebSocket."""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import warnings

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import yaml

# Monkeypatch OpenTelemetry contextvars context to suppress ValueError on detach in different context
try:
    import opentelemetry.context.contextvars_context as otel_ctx_vars
    _original_detach = otel_ctx_vars.ContextVarsRuntimeContext.detach
    def _safe_detach(self, token):
        try:
            _original_detach(self, token)
        except ValueError:
            pass
    otel_ctx_vars.ContextVarsRuntimeContext.detach = _safe_detach
except Exception:
    pass

# Load environment variables
load_dotenv()

from agent import chat_tools, narratron_agent as agent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Suppress PIL debug clutter
logging.getLogger("PIL").setLevel(logging.INFO)

# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Application name constant
APP_NAME = "narratron-bidi-demo"

# ========================================
# Phase 1: Application Initialization (once at startup)
# ========================================

app = FastAPI()

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Define your session service
session_service = InMemorySessionService()

# Define your artifact service
from services.disk_artifact_service import DiskArtifactService
artifact_service = DiskArtifactService("output/artifacts")

# Define your runner
runner = Runner(
    app_name=APP_NAME,
    agent=agent,
    session_service=session_service,
    artifact_service=artifact_service,
)

# ========================================
# HTTP Endpoints
# ========================================


@app.get("/")
async def root():
    """Serve the index.html page."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# ========================================
# WebSocket Endpoint
# ========================================


@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
    proactivity: bool = False,
    affective_dialog: bool = False,
) -> None:
    """WebSocket endpoint for bidirectional streaming with ADK."""
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

    # ========================================
    # Phase 2: Session Initialization (once per streaming session)
    # ========================================

    try:
        model_name = agent.model
        is_native_audio = "native-audio" in model_name.lower() or "1.5-flash" in model_name.lower() or "2.0-flash-exp" in model_name.lower() or "3.1-flash" in model_name.lower() or "live" in model_name.lower()

        # Load compaction config if present
        compaction = config.get("agent", {}).get("compaction", {})
        compaction_config = None
        if compaction:
            trigger = compaction.get("trigger_tokens")
            target = compaction.get("target_tokens")
            compaction_config = types.ContextWindowCompressionConfig(
                trigger_tokens=int(trigger) if trigger is not None else None,
                sliding_window=types.SlidingWindow(target_tokens=int(target)) if target is not None else None
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
                    types.ProactivityConfig(proactive_audio=True)
                    if proactivity
                    else None
                ),
                enable_affective_dialog=affective_dialog
                if affective_dialog
                else None,
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
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if not session:
            await session_service.create_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id
            )

        loop = asyncio.get_running_loop()
        def handle_session_chat_message(text: str):
            async def send_to_ws():
                try:
                    custom_event = {
                        "author": "agent",
                        "content": {
                            "parts": [{"text": text}]
                        }
                    }
                    await safe_send_text(json.dumps(custom_event))
                except Exception as e:
                    logger.error(f"Error sending tool chat message to websocket: {e}")
            loop.create_task(send_to_ws())

        chat_tools.on_send_chat_message = handle_session_chat_message

        live_request_queue = LiveRequestQueue()

        # ========================================
        # Phase 3: Active Session (concurrent bidirectional communication)
        # ========================================

        async def upstream_task() -> None:
            """Receives messages from WebSocket and sends to LiveRequestQueue."""
            while True:
                message = await websocket.receive()

                if "bytes" in message:
                    audio_data = message["bytes"]
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
                        image_blob = types.Blob(
                            mime_type=mime_type, data=image_data
                        )
                        live_request_queue.send_realtime(image_blob)

        async def downstream_task() -> None:
            """Receives Events from run_live() and sends to WebSocket."""
            async for event in runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=live_request_queue,
                run_config=run_config,
            ):
                if hasattr(event, "get_function_calls") and event.get_function_calls():
                    for call in event.get_function_calls():
                        if call.name == "show_image":
                            file_path = call.args.get("file_path")
                            if file_path and os.path.exists(file_path):
                                try:
                                    import base64
                                    with open(file_path, "rb") as f:
                                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                                    mime_type = "image/png"
                                    if file_path.lower().endswith(".jpg") or file_path.lower().endswith(".jpeg"):
                                        mime_type = "image/jpeg"
                                    custom_event = {
                                        "custom_image": {
                                            "mimeType": mime_type,
                                            "data": img_b64
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
            chat_tools.on_send_chat_message = None
            # ========================================
            # Phase 4: Session Termination
            # ========================================
            live_request_queue.close()
    except Exception as e:
        print(f"CRITICAL ERROR IN WEBSOCKET SETUP: {e}")
        import traceback
        traceback.print_exc()
        try:
            await websocket.close()
        except:
            pass
