"""FastAPI application combining Narratron Web Viewer and Bidi Agent WebSocket."""

from typing import Optional
import logging
import os
from pathlib import Path
import sys
import warnings

from dotenv import load_dotenv
from fastapi import WebSocket, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from services.live_stream_service import handle_live_websocket_connection
from api_server import (
    app,
    FLAGS,
    canvas_states,
    db,
    theater_manager,
    get_current_user,
    get_current_user_async,
    can_access_agent_websocket,
    can_control_agent_websocket,
)
from api_server.dependencies import agent_manager

load_dotenv()

# Configure logging filter
class LogFilter(logging.Filter):
    def __init__(self, prefixes: str = "", filter_polling: bool = True):
        super().__init__()
        self.prefixes = tuple(prefix.strip() for prefix in prefixes.split(",") if prefix.strip())
        self.filter_polling = filter_polling

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if self.filter_polling and ("/api/latest" in msg or "/agent/status" in msg):
            return False
        if self.prefixes and not any(prefix in msg or prefix in record.name for prefix in self.prefixes):
            return False
        return True

logging.basicConfig(
    level=logging.DEBUG if FLAGS.log_prefixes else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger().setLevel(logging.DEBUG if FLAGS.log_prefixes else logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Apply filter to root logger and uvicorn access logger
log_filter = LogFilter(prefixes=FLAGS.log_prefixes, filter_polling=FLAGS.suppress_polling)
for handler in logging.getLogger().handlers:
    handler.addFilter(log_filter)

uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.addFilter(log_filter)

# Suppress PIL debug clutter
logging.getLogger("PIL").setLevel(logging.INFO)

# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


APP_NAME = "narratron-combined"

# Static assets shared by the canvas templates.
static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

use_in_memory_artifacts = FLAGS.use_in_memory_artifacts


def get_theater_owner_credits(theater_id: str):
    """Return (has_credits, credit_balance, owner_user_id) for the theater owner."""
    deployment = db.get_deployment(theater_id)
    if not deployment:
        return False, 0.0, None
    owner_id = deployment.get("user_id")
    if owner_id is None:
        return False, 0.0, None
    owner = db.get_user_by_id(owner_id)
    if not owner:
        return False, 0.0, owner_id
    credits = owner.get("credits", 0.0)
    return (credits > 0.0), credits, owner_id


# ========================================
# Agent Lifecycle REST API Endpoints
# ========================================

@app.post("/api/theaters/{theater_id}/agent/start")
async def start_agent_endpoint(theater_id: str):
    """API endpoint to instantiate/start the agent session in memory if owner has sufficient credits."""
    has_credits, credits_bal, owner_id = get_theater_owner_credits(theater_id)
    if not has_credits:
        agent_manager.stop_session(theater_id=theater_id)
        return JSONResponse(
            status_code=402,
            content={
                "status": "error",
                "detail": "Insufficient credits (0 or fewer). Please top up credits on the deploy page.",
                "insufficient_credits": True,
                "credits": credits_bal,
                "theater_id": theater_id,
                "agent_running": False,
            },
        )
    agent_session = agent_manager.get_or_create_session(
        theater_id=theater_id,
        canvas_state_service=canvas_states,
        use_in_memory_artifacts=use_in_memory_artifacts,
    )
    return {
        "status": agent_session.status,
        "theater_id": theater_id,
        "agent_running": True,
        "credits": credits_bal,
        "insufficient_credits": False,
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
    has_credits, credits_bal, owner_id = get_theater_owner_credits(theater_id)
    if not has_credits:
        session = agent_manager.get_session(theater_id=theater_id)
        if session and session.status != "stopped":
            agent_manager.stop_session(theater_id=theater_id)
        return {
            "theater_id": theater_id,
            "agent_running": False,
            "websocket_connected": False,
            "status": "stopped",
            "insufficient_credits": True,
            "credits": credits_bal,
        }

    session = agent_manager.get_session(theater_id=theater_id)
    if not session or session.status == "stopped":
        return {
            "theater_id": theater_id,
            "agent_running": False,
            "websocket_connected": False,
            "status": "stopped",
            "insufficient_credits": False,
            "credits": credits_bal,
        }
    return {
        "theater_id": theater_id,
        "agent_running": True,
        "websocket_connected": session.websocket_connected,
        "status": session.status,
        "created_at": session.created_at,
        "last_active_at": session.last_active_at,
        "insufficient_credits": False,
        "credits": credits_bal,
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
    # ``user_id`` remains in the legacy URL for client compatibility, but it
    # must never establish identity.  Agent control follows the authenticated
    # holder of the theater baton.
    current_user = await get_current_user_async(websocket)

    deployment = db.get_deployment(theater_id)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        await websocket.close(code=1008)
        return

    has_credits, credits_bal, owner_id = get_theater_owner_credits(theater_id)
    if not has_credits:
        agent_manager.stop_session(theater_id=theater_id)
        await websocket.accept()
        await websocket.send_json({
            "type": "insufficient_credits",
            "detail": "Theater owner has <= 0 credits. Agent stopped.",
            "credits": credits_bal,
        })
        await websocket.close(code=1008)
        return

    await handle_live_websocket_connection(
        websocket=websocket,
        theater_id=theater_id,
        agent_manager=agent_manager,
        user_id=current_user["id"],
        canvas_state_service=canvas_states,
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
