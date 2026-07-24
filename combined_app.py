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

from agent import create_agent
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

# Immediately error if sys.argv contains CLI arguments
if len(sys.argv) > 1:
    raise RuntimeError(
        f"CLI arguments (sys.argv) are not allowed when starting the app: {sys.argv[1:]}. "
        "Use config.yaml or environment variables instead."
    )

# Load environment variables
load_dotenv()
config = get_config()

# Define absl flags
flags.DEFINE_boolean(
    "use_in_memory_artifacts",
    False,
    "Use PreloadedInMemoryArtifactService pre-loaded with test artifacts",
)

FLAGS = flags.FLAGS
if not FLAGS.is_parsed():
    FLAGS(sys.argv[:1])

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


def handle_global_chat_message(text: str, session_id: str = None):
    logger.info(f"Chat message tool triggered: {text}")
    add_chat_message(text, "agent", session_id=session_id)


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
    """WebSocket endpoint for bidirectional streaming with ADK.
    Constructs a Runner instance concurrent with the lifespan of the session connection.
    """
    # Create agent and tools bound to this session's lifetime
    session_agent, session_tools = create_agent(session_id=session_id, config=config)
    s_image_tools = session_tools["image_tools"]
    s_chat_tools = session_tools["chat_tools"]
    s_notes_tools = session_tools["notes_tools"]
    s_music_tools = session_tools["music_tools"]

    s_image_tools.on_show_image = lambda path, transition="crossfade": update_shown_image(path, session_id=session_id, transition=transition)
    s_music_tools.on_play_playlist = lambda name, tracks: update_current_playlist(name, tracks, session_id=session_id)
    s_music_tools.on_pause_playlist = lambda: pause_current_playlist(session_id=session_id)
    s_music_tools.on_resume_playlist = lambda: resume_current_playlist(session_id=session_id)
    s_chat_tools.on_send_chat_message = lambda text: handle_global_chat_message(text, session_id=session_id)

    # Construct session-scoped artifact service if using disk-based storage
    disk_service_path = f"sessions/{session_id}/output/artifacts"

    if use_in_memory_artifacts:
        in_mem_svc = PreloadedInMemoryArtifactService()
        test_data_dir = Path(__file__).parent / "testing" / "testdata"
        loaded_count = in_mem_svc.preload_directory(test_data_dir, app_name=APP_NAME)
        logger.info(f"Initialized PreloadedInMemoryArtifactService with {loaded_count} artifacts from {test_data_dir}")
        artifact_service = in_mem_svc
    else:
        artifact_service = DiskArtifactService(disk_service_path)


    session_runner = Runner(
        app_name=APP_NAME,
        agent=session_agent,
        session_service=session_service,
        artifact_service=artifact_service,
    )
    
    await handle_live_websocket_connection(
        websocket=websocket,
        user_id=user_id,
        session_id=session_id,
        agent=session_agent,
        runner=session_runner,
        session_service=session_service,
        config=config,
        image_tools=s_image_tools,
        chat_tools=s_chat_tools,
        notes_tools=s_notes_tools,
        proactivity=proactivity,
        affective_dialog=affective_dialog,
        on_global_chat_message=handle_global_chat_message,
        app_name=APP_NAME,
        send_setup_complete_immediately=False,
        send_setup_after_delay=True,
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
