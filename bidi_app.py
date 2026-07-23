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

from agent import chat_tools, image_tools, narratron_agent as agent
from services.disk_artifact_service import DiskArtifactService
from services.live_stream_service import handle_live_websocket_connection
from utils.config_loader import get_config

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
artifact_service = DiskArtifactService("output/artifacts")

runner = Runner(
    app_name=APP_NAME,
    agent=agent,
    session_service=session_service,
    artifact_service=artifact_service,
)

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
        on_global_chat_message=None,
        app_name=APP_NAME,
        send_setup_complete_immediately=True,
        send_setup_after_delay=False,
    )
