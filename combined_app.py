"""FastAPI application combining Narratron Web Viewer and Bidi Agent WebSocket."""

import logging
import os
from pathlib import Path
import sys
import warnings

from absl import flags
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.staticfiles import StaticFiles
from google import adk
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
import uvicorn

from agent import create_agent
from services.disk_artifact_service import DiskArtifactService
from services.live_stream_service import handle_live_websocket_connection
from services.preloaded_in_memory_artifact_service import PreloadedInMemoryArtifactService
from utils.config_loader import get_config
from utils.session_paths import ensure_sessions_root
from web_viewer_app import (
    app,
    canvas_states,
    db,
    get_current_user,
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

flags.DEFINE_bool("use_local_test_db", False, "Which database to use (local or live).")

flags.DEFINE_string("host", "localhost", "Host to run the app on.")
flags.DEFINE_integer("port", 8000, "Port to run the app on.")

FLAGS = flags.FLAGS
sys.argv = FLAGS(sys.argv, known_only=True)

print("====================================================")
print("HOST", FLAGS.host)
print("PORT", FLAGS.port)
print("====================================================")

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

# Static assets shared by the canvas templates.
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Define services
adk_session_service = InMemorySessionService()

use_in_memory_artifacts = FLAGS.use_in_memory_artifacts


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
    current_user = get_current_user(websocket)
    deployment = db.get_deployment(session_id)
    if not current_user or not deployment or deployment["user_id"] != current_user["id"]:
        # Join-key holders may view and participate in the canvas, but only its
        # owner may open the agent-control channel.
        await websocket.close(code=1008)
        return

    # Create an agent whose bound tool instances are available through agent.tools.
    session_agent = create_agent(
        session_id=session_id,
        config=config,
        canvas_state_service=canvas_states,
    )

    # Construct session-scoped artifact service if using disk-based storage
    disk_service_path = ensure_sessions_root() / session_id / "output" / "artifacts"

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
        session_service=adk_session_service,
        artifact_service=artifact_service,
    )
    
    await handle_live_websocket_connection(
        websocket=websocket,
        user_id=user_id,
        session_id=session_id,
        agent=session_agent,
        runner=session_runner,
        session_service=adk_session_service,
        config=config,
        proactivity=proactivity,
        affective_dialog=affective_dialog,
        on_global_chat_message=None,
        app_name=APP_NAME,
        send_setup_complete_immediately=False,
        send_setup_after_delay=True,
        canvas_state_manager=canvas_states.get(session_id),
    )

if __name__ == "__main__":
    sys.argv = FLAGS(sys.argv, known_only=True)
    logger.info("====================================================")
    logger.info("HOST: %s", FLAGS.host)
    logger.info("PORT: %s", FLAGS.port)
    logger.info("====================================================")
    uvicorn.run(app, host=FLAGS.host, port=FLAGS.port)
