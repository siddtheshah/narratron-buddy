"""FastAPI application demonstrating ADK Gemini Live API Toolkit with WebSocket."""

import logging
from pathlib import Path
import warnings

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from agent import create_agent
from services.disk_artifact_service import DiskArtifactService
from services.live_stream_service import handle_live_websocket_connection
from utils.config_loader import get_config
from utils.session_paths import ensure_sessions_root

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
config = get_config()

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

app = FastAPI()

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

session_service = InMemorySessionService()

@app.get("/")
async def root():
    """Serve the index.html page."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
    proactivity: bool = False,
    affective_dialog: bool = False,
) -> None:
    """WebSocket endpoint for bidirectional streaming with ADK."""
    session_agent, session_tools = create_agent(session_id=session_id, config=config)
    session_artifact_service = DiskArtifactService(ensure_sessions_root() / "test_session")
    session_runner = Runner(
        app_name=APP_NAME,
        agent=session_agent,
        session_service=session_service,
        artifact_service=session_artifact_service,
    )

    await handle_live_websocket_connection(
        websocket=websocket,
        user_id=user_id,
        session_id=session_id,
        agent=session_agent,
        runner=session_runner,
        session_service=session_service,
        config=config,
        image_tools=session_tools["image_tools"],
        chat_tools=session_tools["chat_tools"],
        notes_tools=session_tools["notes_tools"],
        proactivity=proactivity,
        affective_dialog=affective_dialog,
        on_global_chat_message=None,
        app_name=APP_NAME,
        send_setup_complete_immediately=True,
        send_setup_after_delay=False,
    )
