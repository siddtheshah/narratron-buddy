"""FastAPI application combining Narratron Web Viewer and Bidi Agent WebSocket."""

import logging
import os
from pathlib import Path
import sys
import warnings

from absl import flags
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
import uvicorn

from agent import chat_tools, image_tools, music_tools, narratron_agent as agent
from services.disk_artifact_service import DiskArtifactService
from services.live_stream_service import handle_live_websocket_connection
from services.preloaded_in_memory_artifact_service import PreloadedInMemoryArtifactService
from utils.config_loader import get_config
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
config = get_config()

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
    test_data_dir = Path(__file__).parent / "testing" / "testdata"
    loaded_count = in_mem_svc.preload_directory(test_data_dir, app_name=APP_NAME)
    logger.info(f"Initialized PreloadedInMemoryArtifactService with {loaded_count} artifacts from {test_data_dir}")
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
    await handle_live_websocket_connection(
        websocket=websocket,
        user_id=user_id,
        session_id=session_id,
        agent=agent,
        runner=runner,
        session_service=session_service,
        config=config,
        image_tools=image_tools,
        chat_tools=chat_tools,
        proactivity=proactivity,
        affective_dialog=affective_dialog,
        on_global_chat_message=handle_global_chat_message,
        app_name=APP_NAME,
        send_setup_complete_immediately=False,
        send_setup_after_delay=True,
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
