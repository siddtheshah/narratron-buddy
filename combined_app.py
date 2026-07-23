"""FastAPI application combining Narratron Web Viewer and Bidi Agent WebSocket."""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import sys
import warnings

from absl import flags
from dotenv import load_dotenv
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import uvicorn
import yaml

from agent import chat_tools, image_tools, music_tools, narratron_agent as agent
from services.disk_artifact_service import DiskArtifactService
from services.preloaded_in_memory_artifact_service import PreloadedInMemoryArtifactService
from web_viewer_app import (
    add_chat_message,
    app,
    pause_current_playlist,
    resume_current_playlist,
    update_current_playlist,
    update_shown_image,
)

# Load environment variables
load_dotenv()

# Define absl flags
flags.DEFINE_boolean(
    "use_in_memory_artifacts",
    False,
    "Use PreloadedInMemoryArtifactService pre-loaded with test artifacts",
    module_name="combined_app",
)

FLAGS = flags.FLAGS
if not FLAGS.is_parsed():
    FLAGS(sys.argv, known_only=True)

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

config_path = Path(__file__).parent / "config.yaml"
with open(config_path, "r") as f:
    config = yaml.safe_load(f)

APP_NAME = "narratron-combined"

# Mount static folder for Tester UI
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Define services
session_service = InMemorySessionService()

use_in_memory_artifacts = FLAGS.use_in_memory_artifacts or (
    os.getenv("USE_IN_MEMORY_ARTIFACTS", "0").lower() in ("1", "true", "yes")
)

if use_in_memory_artifacts:
    in_mem_svc = PreloadedInMemoryArtifactService()
    test_artifacts_dir = Path(__file__).parent / "testing" / "test_artifacts"
    loaded_count = in_mem_svc.preload_directory(test_artifacts_dir, app_name=APP_NAME)
    logger.info(f"Initialized PreloadedInMemoryArtifactService with {loaded_count} artifacts from {test_artifacts_dir}")
    artifact_service = in_mem_svc
else:
    artifact_service = DiskArtifactService("output/artifacts")

# Define runner
runner = Runner(
    app_name=APP_NAME,
    agent=agent,
    session_service=session_service,
    artifact_service=artifact_service,
)

# Set global callbacks
image_tools.on_show_image = update_shown_image
music_tools.on_play_playlist = update_current_playlist
music_tools.on_pause_playlist = pause_current_playlist
music_tools.on_resume_playlist = resume_current_playlist

def handle_global_chat_message(text: str):
    logger.info(f"Chat message tool triggered: {text}")
    add_chat_message(text, "agent")

chat_tools.on_send_chat_message = handle_global_chat_message

# ========================================
# Additional Combined App Endpoints
# ========================================

@app.get("/tester", response_class=HTMLResponse)
async def read_tester():
    """Serve the Bidi Agent Tester page."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")

# ========================================
# Live Agent WebSocket Endpoint
# ========================================

@app.websocket("/ws/{user_id}/{session_id}")
async def agent_websocket_endpoint(
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
            # 1. Update chat manager so the canvas web viewer sees it
            handle_global_chat_message(text)
            
            # 2. Send to Bidi Tester UI WebSocket
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
                        image_blob = types.Blob(
                            mime_type=mime_type, data=image_data
                        )
                        live_request_queue.send_realtime(image_blob)

        async def downstream_task() -> None:
            """Receives Events from run_live() and sends to WebSocket."""
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
                            resolved_path = None
                            if file_path:
                                if os.path.exists(file_path):
                                    resolved_path = file_path
                                else:
                                    # Fallback resolution
                                    for candidate in [
                                        os.path.join(image_tools.output_dir, file_path),
                                        os.path.join(image_tools.output_dir, os.path.basename(file_path)),
                                        str(Path(__file__).parent / "testing" / "test_artifacts" / "images" / os.path.basename(file_path)),
                                        str(Path(__file__).parent / "testing" / "test_artifacts" / os.path.basename(file_path)),
                                    ]:
                                        if os.path.exists(candidate):
                                            resolved_path = candidate
                                            break
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
            chat_tools.on_send_chat_message = handle_global_chat_message
            # ========================================
            # Phase 4: Session Termination
            # ========================================
            live_request_queue.close()
    except Exception as e:
        logger.error(f"CRITICAL ERROR IN WEBSOCKET SETUP: {e}", exc_info=True)
        try:
            await websocket.close()
        except:
            pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
