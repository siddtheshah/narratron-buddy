"""FastAPI application combining Narratron Web Viewer and Bidi Agent WebSocket."""

from typing import Optional
import logging
import os
from pathlib import Path
import sys
import warnings

from absl import flags
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.staticfiles import StaticFiles
import uvicorn

from services.agent_manager import AgentSessionManager
from services.live_stream_service import handle_live_websocket_connection
from utils.config_loader import get_config
from web_viewer_app import (
    app,
    canvas_states,
    db,
    get_current_user,
    can_access_agent_websocket,
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

use_in_memory_artifacts = FLAGS.use_in_memory_artifacts

# Global Agent Session Manager
agent_manager = AgentSessionManager(app_name=APP_NAME, config=config)


# ========================================
# Agent Lifecycle REST API Endpoints
# ========================================

@app.post("/api/theaters/{theater_id}/agent/start")
async def start_agent_endpoint(theater_id: str):
    """API endpoint to instantiate/start the agent session in memory if not already started."""
    agent_session = agent_manager.get_or_create_session(
        theater_id=theater_id,
        canvas_state_service=canvas_states,
        use_in_memory_artifacts=use_in_memory_artifacts,
    )
    return {
        "status": agent_session.status,
        "theater_id": theater_id,
        "agent_running": True,
    }


@app.post("/api/theaters/{theater_id}/agent/stop")
async def stop_agent_endpoint(theater_id: str):
    """API endpoint to explicitly stop and remove the agent session from memory."""
    stopped = agent_manager.stop_session(theater_id=theater_id)
    return {
        "status": "stopped" if stopped else "not_found",
        "theater_id": theater_id,
        "agent_running": False,
    }


@app.get("/api/theaters/{theater_id}/agent/status")
async def get_agent_status_endpoint(theater_id: str):
    """API endpoint to check if an agent session is active in memory."""
    session = agent_manager.get_session(theater_id=theater_id)
    if not session or session.status == "stopped":
        return {
            "theater_id": theater_id,
            "agent_running": False,
            "websocket_connected": False,
            "status": "stopped",
        }
    return {
        "theater_id": theater_id,
        "agent_running": True,
        "websocket_connected": session.websocket_connected,
        "status": session.status,
        "created_at": session.created_at,
        "last_active_at": session.last_active_at,
    }


# ========================================
# Live Agent WebSocket Endpoint
# ========================================

@app.websocket("/ws/{theater_id}/agent")
@app.websocket("/ws/{user_id}/{theater_id}")
async def agent_websocket_endpoint(
    websocket: WebSocket,
    theater_id: str,
    user_id: Optional[str] = None,
) -> None:
    """WebSocket endpoint for bidirectional streaming with ADK.
    Retrieves or creates the in-memory AgentSession instance for stream handling.
    """
    current_user = get_current_user(websocket)
    if not current_user and user_id and user_id.isdigit():
        current_user = db.get_user_by_id(int(user_id))

    deployment = db.get_deployment(theater_id)
    if not deployment or not can_access_agent_websocket(websocket, deployment, current_user=current_user):
        await websocket.close(code=1008)
        return

    await handle_live_websocket_connection(
        websocket=websocket,
        theater_id=theater_id,
        agent_manager=agent_manager,
    )
    # Perform periodic cleanup of old idle sessions
    agent_manager.cleanup_idle_sessions(ttl_seconds=300.0)

if __name__ == "__main__":
    sys.argv = FLAGS(sys.argv, known_only=True)
    logger.info("====================================================")
    logger.info("HOST: %s", FLAGS.host)
    logger.info("PORT: %s", FLAGS.port)
    logger.info("====================================================")
    uvicorn.run(app, host=FLAGS.host, port=FLAGS.port)

