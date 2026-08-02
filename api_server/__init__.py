"""api_server package — FastAPI app with all REST/WebSocket routes."""

from api_server.shared import (  # noqa: F401
    app,
    FLAGS,
    config,
    db,
    theater_manager,
    canvas_states,
    theaters_folder,
    get_current_user,
    can_access_agent_websocket,
    can_control_agent_websocket,
    _require_canvas_access,
    _safe_path_param,
    _valid_join_key,
    _grant_canvas_access,
    _canvas_access_grants,
    PROJECT_ROOT,
)

# Register route modules (side-effect imports that attach endpoints to `app`)
import api_server.auth  # noqa: F401
import api_server.payments  # noqa: F401
import api_server.theaters  # noqa: F401
import api_server.canvas  # noqa: F401
import api_server.profiles  # noqa: F401
import api_server.pages  # noqa: F401

# Re-export symbols that external code imports by name
from api_server.payments import _is_mock_payment_mode, CREDIT_PACKAGES  # noqa: F401
from api_server.pages import render_about_markdown  # noqa: F401

# Agent lifecycle and audio websocket routes depend on the shared route surface,
# so load them after the HTTP route modules above.
import api_server.app  # noqa: F401, E402

# Importing the ``api_server.app`` submodule temporarily assigns that module to
# this package's ``app`` attribute.  Re-export the ASGI application instead.
from api_server.shared import app as app  # noqa: F401, E402
